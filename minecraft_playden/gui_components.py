"""Shared Qt worker, result dialog and mounted-drive picker."""
from collections.abc import Callable
from pathlib import Path
import shutil

from PySide6.QtCore import QObject, Signal, Slot, Qt, QStorageInfo
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QMenu, QVBoxLayout, QWidget, QToolButton, QFormLayout

from .operations import CancellationToken, OperationCancelled
from .progress import ProgressCallback
from .volumes import DriveTarget
from .drive_discovery import external_mounts
from .formatting import format_drive_space, format_size
from .exporter import INSTRUCTIONS
from .transfer import InsufficientSpace
from .network_protocol import NetworkTransferError
from .usb_queue import destination_paths


class AdvancedSection(QWidget):
    """Disclosure groups always start collapsed; their option values live separately."""
    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        layout.addWidget(self.toggle)
        self.content = QWidget()
        self.form = QFormLayout(self.content)
        self.content.hide()
        layout.addWidget(self.content)
        self.toggle.toggled.connect(self.set_expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.content.setVisible(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        if self.toggle.isChecked() != expanded:
            self.toggle.setChecked(expanded)


def error_message(exc: Exception) -> str:
    """Actionable desktop errors without Python tracebacks or exception prefixes."""
    detail = str(exc) or 'The operation could not finish.'
    if isinstance(exc, NetworkTransferError):
        return detail  # The sender includes receiver/recovery advice and publication certainty.
    if isinstance(exc, PermissionError):
        return f'{detail}\nChoose a writable output/cache folder and check file permissions.'
    if isinstance(exc, FileNotFoundError):
        return f'{detail}\nCheck that the source or drive is still available. Rescan after completing launcher downloads.'
    if isinstance(exc, OSError):
        return f'{detail}\nCheck free space, drive availability and your network connection, then retry with a new output folder.'
    if isinstance(exc, ValueError):
        return f'{detail}\nCheck the selected instance and inputs. For missing loader libraries, finish installation in the launcher and rescan.'
    return f'{detail}\nCopy this message and the stage log when reporting a problem. No completed package was confirmed.'


class Task(QObject):
    result = Signal(object)
    error = Signal(str)
    insufficient_space = Signal(object)
    cancelled = Signal(str)
    progress = Signal(object)
    finished = Signal()

    def __init__(self, operation: Callable[[ProgressCallback], object], cancellation: CancellationToken):
        super().__init__()
        self.operation = operation
        self.cancellation = cancellation

    @Slot()
    def run(self) -> None:
        try:
            self.cancellation.check()
            self.result.emit(self.operation(self.progress.emit))
        except InsufficientSpace as exc:
            self.insufficient_space.emit(exc)
        except OperationCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.error.emit(error_message(exc))
        finally:
            self.finished.emit()


class CompletionDialog(QDialog):
    def __init__(self, package: Path, parent: QWidget, open_folder: Callable[[], None],
                 copy_usb: Callable[[], None] | None = None, *, transferred: bool = False,
                 file_only: bool = False, verified: bool = True):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(('USB transfer verified' if verified else 'USB copy complete — verification skipped') if transferred else ('Instance files imported — launch unverified' if file_only else 'Package build complete'))
        self.setModal(True)
        layout = QVBoxLayout(self)
        self.package_label = QLabel(f'Package: {package.name}')
        self.path_label = QLabel(str(package))
        for label in (self.package_label, self.path_label):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)
        text = (('Every file passed SHA-256 verification and pending writes were flushed. ' if verified else
                 'Post-copy checksum verification was skipped. Copy integrity has not been fully verified. Writes were flushed. ') +
                'Safely eject the drive in your operating system before unplugging it.' if transferred else
                ('Instance files were preserved and their import metadata validated. This is not a launch-ready package; '
                 'it has no generated Java command or Playden launch recipe.' if file_only else
                 'Export validation passed. Your package is ready to send over LAN or copy to USB. Console/mod testing is still required.\n\n' + INSTRUCTIONS))
        self.detail = QPlainTextEdit(text)
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(120)
        layout.addWidget(self.detail, 1)
        buttons = QHBoxLayout()
        self.open_button = QPushButton('Open USB Folder' if transferred else 'Open Output Folder')
        self.open_button.clicked.connect(open_folder)
        buttons.addWidget(self.open_button)
        self.copy_button: QPushButton | None = None
        if copy_usb is not None:
            self.copy_button = QPushButton('Copy to USB')
            self.copy_button.clicked.connect(self.accept)
            self.copy_button.clicked.connect(copy_usb)
            buttons.addWidget(self.copy_button)
        self.close_button = QPushButton('Close')
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        screen = self.screen()
        available = screen.availableGeometry() if screen is not None else None
        self.resize(min(680, available.width() - 32) if available is not None else 680,
                    min(440, available.height() - 48) if available is not None else 440)

    def add_ps5_action(self, callback: Callable[[], None]) -> None:
        self.ps5_button = QPushButton('Send to PS5 (LAN)…')
        self.ps5_button.clicked.connect(self.accept)
        self.ps5_button.clicked.connect(callback)
        layout = self.layout()
        assert layout is not None
        layout.addWidget(self.ps5_button)


class QueueCompletionDialog(QDialog):
    def __init__(self, summary: str, output_parent: Path, parent: QWidget,
                 open_folder: Callable[[], None], *, has_packages: bool,
                 copy_usb: Callable[[], None] | None = None, title: str = 'Build queue results',
                 retry_usb: Callable[[], None] | None = None, check_space: Callable[[], None] | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(title)
        self.setModal(True)
        layout = QVBoxLayout(self)
        self.detail = QPlainTextEdit(summary + ('\n\n' + INSTRUCTIONS if has_packages else ''))
        self.detail.setReadOnly(True)
        layout.addWidget(self.detail)
        self.open_button = QPushButton('Open Output Folder')
        self.open_button.setEnabled(output_parent.is_dir())
        self.open_button.clicked.connect(open_folder)
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(self.open_button)
        self.copy_button: QPushButton | None = None
        if copy_usb is not None:
            self.copy_button = QPushButton('Copy to USB')
            self.copy_button.clicked.connect(self.accept)
            self.copy_button.clicked.connect(copy_usb)
            row.addWidget(self.copy_button)
        for label, callback in (('Retry failed/cancelled copies', retry_usb), ('Check again', check_space)):
            if callback is not None:
                button = QPushButton(label)
                button.clicked.connect(self.accept)
                button.clicked.connect(callback)
                row.addWidget(button)
        row.addWidget(close)
        layout.addLayout(row)
        screen = self.screen()
        available = screen.availableGeometry() if screen is not None else None
        self.resize(min(720, available.width() - 32) if available is not None else 720,
                    min(480, available.height() - 48) if available is not None else 480)

    def add_ps5_action(self, callback: Callable[[], None]) -> None:
        self.ps5_button = QPushButton('Send to PS5 (LAN)…')
        self.ps5_button.clicked.connect(self.accept)
        self.ps5_button.clicked.connect(callback)
        layout = self.layout()
        assert layout is not None
        layout.addWidget(self.ps5_button)


def drive_label(storage: QStorageInfo) -> str:
    filesystem = bytes(storage.fileSystemType().data()).decode(errors='replace') or 'Unknown filesystem'
    space = format_drive_space(storage.bytesAvailable(), storage.bytesTotal())
    return f'{storage.displayName()} — {storage.rootPath()} — {space} ({filesystem})'


def mounted_drives() -> list[tuple[str, Path]]:
    choices = []
    volumes = QStorageInfo.mountedVolumes()
    allowed = external_mounts([storage.rootPath() for storage in volumes])
    for storage in volumes:
        if not storage.isValid() or not storage.isReady() or storage.isReadOnly() or storage.rootPath() not in allowed:
            continue
        root = Path(storage.rootPath())
        choices.append((drive_label(storage), root))
    return choices


class DriveDialog(QDialog):
    def __init__(self, package_name: str, parent: QWidget, *, package_names: list[str] | None = None):
        super().__init__(parent)
        self.package_name = package_name
        self.package_names = package_names or [package_name]
        self.target: DriveTarget | None = None
        self.setWindowTitle('Copy packages to USB' if len(self.package_names) > 1 else 'Copy package to USB')
        layout = QVBoxLayout(self)
        notice = QLabel('Select the mounted USB/external volume. Check its label and mount point. '
                        'Only identified external/removable devices are listed. The app does not format or eject drives.')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.skip_verification = QCheckBox('Skip post-copy USB checksum verification (faster)')
        self.skip_verification.setToolTip('Copy integrity will not be fully verified. Source and destination package validation still run.')
        layout.addWidget(self.skip_verification)
        warning = QLabel('Skipping verification reduces detection of USB corruption. Package validation always runs.')
        warning.setWordWrap(True)
        layout.addWidget(warning)
        self.drives = QComboBox()
        layout.addWidget(self.drives)
        self.destination = QLabel()
        self.destination.setTextFormat(Qt.TextFormat.PlainText)
        self.destination.setWordWrap(True)
        layout.addWidget(self.destination)
        row = QHBoxLayout()
        refresh = QPushButton('Refresh drives')
        browse = QPushButton('Advanced')
        menu = QMenu(browse)
        manual = menu.addAction('Select folder manually…')
        manual.triggered.connect(self.browse)
        browse.setMenu(menu)
        self.copy_button = QPushButton('Copy and Verify')
        self.skip_verification.toggled.connect(lambda skip: self.copy_button.setText('Copy (verification skipped)' if skip else 'Copy and Verify'))
        close = QPushButton('Cancel')
        for button in (refresh, browse, self.copy_button, close):
            row.addWidget(button)
        layout.addLayout(row)
        refresh.clicked.connect(self.refresh)
        self.copy_button.clicked.connect(self.choose)
        close.clicked.connect(self.reject)
        self.drives.currentIndexChanged.connect(self.update_destination)
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        self.drives.clear()
        for label, root in mounted_drives():
            self.drives.addItem(label, str(root))
        self.update_destination()

    @Slot()
    def update_destination(self) -> None:
        root = self.drives.currentData()
        self.copy_button.setEnabled(bool(root))
        try:
            paths = destination_paths(self.package_names, Path(root)) if root else []
            self.destination.setText('Destinations (one folder per package):\n' + '\n'.join(str(path) for path in paths) if paths else
                'No writable external drive detected. Connect/mount your drive and refresh, or use Advanced for unusual devices.')
        except OSError as exc:
            self.destination.setText(str(exc))
            self.copy_button.setEnabled(False)

    @Slot()
    def browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, 'Select a folder on the mounted USB/external drive')
        if folder:
            storage = QStorageInfo(folder)
            root = Path(storage.rootPath())
            if not storage.isValid() or not storage.isReady() or storage.isReadOnly() :
                QMessageBox.warning(self, 'Drive unavailable', 'Select a writable USB/external volume, not the system volume.')
                return
            external = storage.rootPath() in external_mounts([storage.rootPath()])
            warning = ('This device was not identified as external. It may be an INTERNAL/SYSTEM drive. ' if not external else '')
            destination = root / 'playden/import' / self.package_name
            answer = QMessageBox.warning(self, 'Confirm manual destination', warning +
                f'The selected folder belongs to volume {root}. Copy to {destination}? '
                'The package is placed at the volume root, not inside the selected folder. Confirm that this is the intended drive.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.drives.addItem(f'Manual: {drive_label(storage)}', str(root))
            self.drives.setCurrentIndex(self.drives.count() - 1)

    @Slot()
    def choose(self) -> None:
        root = self.drives.currentData()
        if root:
            try:
                self.target = DriveTarget.capture(Path(root))
            except OSError as exc:
                QMessageBox.warning(self, 'Drive unavailable', str(exc))
                return
            self.accept()


class SpaceDialog(QDialog):
    """Recheck the actual selected volume; no cached QStorageInfo capacity."""
    def __init__(self, drive: DriveTarget, required: int, parent: QWidget):
        super().__init__(parent)
        self.drive, self.required = drive, required
        self.setWindowTitle('Insufficient destination storage')
        layout = QVBoxLayout(self)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.check_button = QPushButton('Check again')
        self.retry_button = QPushButton('Retry copy')
        close = QPushButton('Close')
        for button in (self.check_button, self.retry_button, close):
            layout.addWidget(button)
        self.check_button.clicked.connect(self.check_space)
        self.retry_button.clicked.connect(self.accept)
        close.clicked.connect(self.reject)
        self.check_space()

    @Slot()
    def check_space(self) -> None:
        try:
            self.drive.check()
            free = shutil.disk_usage(self.drive.root).free
            self.message.setText(f'{self.drive.root}: {format_size(free)} free; approximately {format_size(self.required)} required. '
                                 'Free some space and choose Check again. Retry revalidates the package and drive.')
            self.retry_button.setEnabled(free >= self.required)
        except OSError as exc:
            self.message.setText(str(exc))
            self.retry_button.setEnabled(False)
