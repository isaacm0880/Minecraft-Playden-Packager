"""PySide6 desktop frontend for read-only local import and the shared exporter."""
import sys
import re
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, QSignalBlocker, QThread, QTimer, Slot, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QComboBox, QLayout,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStyledItemDelegate,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .desktop import ValidatedPackage, LocalBuildRequest, suggest_output, check_package, selection_readiness, offline_player_name, BuildRecord, queue_summary, prepare_existing_package, unique_output, sanitise_name
from .compatibility import Status, UNTESTED_WARNING
from . import __version__
from .discovery import DiscoveryResult, Installation, discover
from .exporter import INSTRUCTIONS
from .progress import Progress, ProgressCallback
from .formatting import format_size
from .transfer_timing import ThroughputEstimator, eta_text
from .branding import application_icon, configure_desktop_identity, DESKTOP_ID

from .gui_receiver import ReceiverManager
from .gui_components import AdvancedSection, Task, CompletionDialog, DriveDialog, SpaceDialog, QueueCompletionDialog
from .operations import CancellationToken
from .cleanup import GeneratedOutput
from .desktop_integration import open_system_folder, needs_system_opener
from .usb_queue import UsbRecord, TransferCandidate, transfer_candidates, plan_transfers, usb_summary
from .volumes import DriveTarget
from .transfer import TransferRequest, TransferResult, InsufficientSpace
from .network_transfer import NetworkRequest, NetworkResult, run_network_queue
from .network_protocol import ROOTS, lan_address, safe_relative


class PathDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        option.textElideMode = Qt.TextElideMode.ElideMiddle


class InstallationItem(QTableWidgetItem):
    """Let Qt move complete rows, comparing full text rather than painted labels."""

    def __init__(self, text: str, *, version: bool = False):
        super().__init__(text)
        self.version = version

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if self.version:
            def natural(value: str) -> tuple[tuple[int, int | str], ...]:
                return tuple((0, int(part)) if part.isdecimal() else (1, part.casefold())
                             for part in re.split(r'([0-9]+)', value))
            return natural(self.text()) < natural(other.text())
        return (self.text().casefold(), self.toolTip().casefold()) < (other.text().casefold(), other.toolTip().casefold())


class MainWindow(QMainWindow):
    def __init__(self, cache: Path, paths: list[Path]):
        super().__init__()
        self.setWindowTitle(f'Minecraft Playden Packager {__version__} — Preview')
        self.setWindowIcon(application_icon())
        self.resize(1080, 760)
        self.paths = list(paths)
        self.discovery = DiscoveryResult(())
        self.worker_thread: QThread | None = None
        self.task: Task | None = None
        self.build_queue: list[LocalBuildRequest] = []
        self.queue_row = -1
        self.batch = False
        self.build_records: list[BuildRecord] = []
        self.output_view: Path | None = None
        self.generated_outputs: dict[Path, GeneratedOutput] = {}
        self.sources_seen: set[Path] = set()
        self.cleanup_requested = False
        self.cleanup_transfer: tuple[TransferRequest, TransferResult] | None = None
        self.queue_dialog: QueueCompletionDialog | None = None
        self.use_build_queue = False
        self.usb_records: list[UsbRecord] = []
        self.usb_current = -1
        self.usb_drive: DriveTarget | None = None
        self.usb_batch_active = False
        self.usb_cleanup_opt = False
        self.usb_space_error: InsufficientSpace | None = None
        self.usb_summary_pending = False
        self.usb_dialog: QueueCompletionDialog | None = None
        self.operation = ''
        self.outcome = ''
        self.cancellation = CancellationToken()
        self.completed_package: ValidatedPackage | None = None
        self.last_transfer: TransferRequest | None = None
        self.transfer_verified = True
        self.network_results: tuple[NetworkResult, ...] = ()
        self._output_selection: tuple[Path, str] | None = None
        self.network_confirmation: QMessageBox | None = None
        self.transfer_estimator = ThroughputEstimator()
        self._last_receiver_log = ''
        self.folder_openers: list[object] = []
        self.space_dialog: SpaceDialog | None = None
        self.transfer_retry_available = False
        self.pending_dialog: tuple[Path, bool] | None = None
        self.completion_dialog: CompletionDialog | None = None
        self.last_stage = ''
        self._last_transfer_event: Progress | None = None
        self.eta_timer = QTimer(self)
        self.eta_timer.setInterval(1000)
        self.eta_timer.timeout.connect(self.refresh_transfer_estimate)
        self.eta_timer.start()
        self.build_output: Path | None = None
        self._suggested_output = ''
        self._output_is_manual = False
        self._preferred_path: Path | None = None
        self._fabric_selection: tuple = ()
        self._prepare_fabric_requested = True
        self.settings = QSettings('MinecraftPlayden', 'Packager')
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setSpacing(4)
        title = QLabel('Minecraft → Playden')
        title.setStyleSheet('font-size: 20px; font-weight: bold;')
        title.setWordWrap(True)
        layout.addWidget(title)
        description = QLabel('Select an instance, choose a package folder, then build and transfer to PS5. Your original instance stays untouched.')
        description.setWordWrap(True)
        layout.addWidget(description)
        buttons = QHBoxLayout()
        self.scan_button = QPushButton('Rescan installations')
        self.folder_button = QPushButton('Add source folder…')
        self.validate_button = QPushButton('Validate existing package…')
        self.copy_log_button = QPushButton('Copy progress/error log')
        buttons.addWidget(self.scan_button)
        buttons.addWidget(self.folder_button)
        buttons.addWidget(self.validate_button)
        buttons.addWidget(self.copy_log_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['Instance', 'Launcher', 'Version', 'Source folder', 'Client JAR', 'Mod loader', 'Status'])
        self.table.setMinimumHeight(220)
        self.table.setColumnHidden(3, True)
        self.table.setColumnHidden(4, True)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        for column in (3, 4):
            self.table.setItemDelegateForColumn(column, PathDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        header = self.table.horizontalHeader()
        for column in range(self.table.columnCount()):
            header_item = self.table.horizontalHeaderItem(column)
            if header_item is not None:
                header_item.setData(Qt.ItemDataRole.InitialSortOrderRole, Qt.SortOrder.AscendingOrder)
        # No active column on a fresh install: the first click must ascend,
        # including on Instance. Previously chosen preferences are restored.
        column, order = -1, Qt.SortOrder.AscendingOrder
        try:
            saved_column = int(str(self.settings.value('table/sortColumn', -1)))
            saved_order = int(str(self.settings.value('table/sortOrder', 0)))
            if 0 <= saved_column < self.table.columnCount() and saved_order in (0, 1):
                column, order = saved_column, Qt.SortOrder(saved_order)
        except (TypeError, ValueError):
            pass
        header.setSortIndicator(column, order)
        self.table.setSortingEnabled(True)
        header.setSortIndicatorClearable(False)
        header.sortIndicatorChanged.connect(self.sort_changed)
        for column, width in enumerate((160, 145, 75, 120, 120, 90, 240)):
            self.table.setColumnWidth(column, width)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(8)
        self.splitter.addWidget(self.table)
        layout.addWidget(self.splitter, 1)
        self.details_scroll = QScrollArea()
        self.details_scroll.setWidgetResizable(True)
        self.details_scroll.setMinimumHeight(60)
        self.details_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        details_layout.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.build_advanced = AdvancedSection('Build advanced options')
        self.show_paths = QCheckBox('Show source and client paths in the instance list')
        self.show_paths.toggled.connect(self.show_technical_paths)
        self.build_advanced.form.addRow(self.show_paths)
        self.local_management = AdvancedSection('Local package management')
        self.source_path = QLineEdit()
        self.version = QLineEdit()
        self.client_path = QLineEdit()
        self.loader = QLineEdit()
        for field in (self.source_path, self.version, self.client_path, self.loader):
            field.setReadOnly(True)
        self.build_advanced.form.addRow('Selected source', self.source_path)
        self.build_advanced.form.addRow('Base Minecraft version', self.version)
        self.build_advanced.form.addRow('Local client JAR', self.client_path)
        self.build_advanced.form.addRow('Mod loader', self.loader)
        self.output = QLineEdit()
        self.output.setPlaceholderText('Choose a new, non-existing package folder')
        self.output_button = QPushButton('Choose output…')
        output_row = QHBoxLayout()
        output_row.addWidget(self.output)
        output_row.addWidget(self.output_button)
        form.addRow('Output folder', output_row)
        self.cache = QLineEdit(str(cache))
        self.cache_button = QPushButton('Choose cache…')
        cache_row = QHBoxLayout()
        cache_row.addWidget(self.cache)
        cache_row.addWidget(self.cache_button)
        self.build_advanced.form.addRow('Download cache', cache_row)
        self.allow_runtime_download = QCheckBox('Allow Windows Java runtime download if missing')
        self.allow_runtime_download.setToolTip('Prefer an installed or cached matching Windows x64 runtime. '
                                              'Enabling this permits a verified Temurin runtime download for this build queue.')
        self.allow_runtime_download.setChecked(bool(self.settings.value("build/runtimeDownload", True, type=bool)))
        self.build_advanced.form.addRow(self.allow_runtime_download)
        self.offline = QCheckBox("Offline: use local and cached files only")
        self.offline.setChecked(bool(self.settings.value("build/offline", False, type=bool)))
        self.build_advanced.form.addRow(self.offline)
        self.prepare_fabric = QCheckBox('Prepare Fabric for Playden')
        self.prepare_fabric.setToolTip("Prepares Fabric's game files before transfer to avoid first-launch remapping issues on Playden.")
        self.build_advanced.form.addRow(self.prepare_fabric)
        self.remap_java = QLineEdit(str(self.settings.value('preparation/java', '')))
        self.remap_java.setPlaceholderText('Automatic: host-native JDK 17+ with compiler module')
        self.remap_java.setToolTip('Host preparation Java executable. Separate from the Windows runtime packaged for PS5; no Wine or downloads.')
        self.remap_java_button = QPushButton('Choose Java…')
        java_row = QHBoxLayout()
        java_row.addWidget(self.remap_java)
        java_row.addWidget(self.remap_java_button)
        self.build_advanced.form.addRow('Host preparation JDK', java_row)
        self.privacy_cleanup = QCheckBox('Privacy cleanup (recommended)')
        self.privacy_cleanup.setChecked(bool(self.settings.value("build/privacyCleanup", True, type=bool)))
        self.build_advanced.form.addRow(self.privacy_cleanup)
        self.privacy_cleanup.setToolTip('Remove known credentials, sessions and personal identifiers from the package copy. '
                                        'Worlds and ordinary mod settings are preserved. Unknown secrets need manual review.')
        self.controller_layout = QCheckBox('Use Minecraft keyboard/mouse controller layout')
        self.controller_layout.setChecked(bool(self.settings.value('build/controllerLayout', True, type=bool)))
        self.controller_layout.setToolTip('Include the built-in Minecraft layout in the Playden recipe. '
                                          'Turn off for Controlify or other controller mods. Assumes default Minecraft keybinds.')
        form.addRow(self.controller_layout)
        self.username = QLineEdit(offline_player_name(self.settings.value('player/offlineName', 'Player')))
        self.save_offline_name(self.username.text())
        self.username.setToolTip('Remembered for this user on this device. Offline display name only; this does not sign in or enable authenticated services.')
        form.addRow('Offline player name', self.username)
        form.addRow(self.build_advanced)
        self.receiver_manager = ReceiverManager(self.settings, self)
        self.ps5_host = self.receiver_manager.host
        form.addRow(self.receiver_manager)
        self.ps5_destination = QComboBox()
        self.ps5_destination.addItem('Internal storage (default)', 'internal')
        self.ps5_destination.addItem('USB0 storage', 'usb0')
        saved_destination = self.settings.value('transfer/destination', 'internal')
        destination_index = self.ps5_destination.findData(saved_destination)
        self.ps5_destination.setCurrentIndex(max(0, destination_index))
        self.ps5_destination.setToolTip('LAN destination remembered per user. USB0 never falls back to internal storage.')
        form.addRow('PS5 transfer destination', self.ps5_destination)
        self.ps5_pipeline = QCheckBox('Pipeline files (experimental)')
        self.ps5_pipeline.setChecked(bool(self.settings.value('transfer/pipelineFiles', False, type=bool)))
        self.ps5_pipeline.setToolTip('Optional file pipelining. Disabled by default; buffered receiver reads are the normal protocol.')
        self.transfer_advanced = self.receiver_manager.advanced
        self.transfer_advanced.toggle.setText('Transfer advanced options')
        self.transfer_advanced.form.addRow('LAN protocol', self.ps5_pipeline)
        receiver_note = QLabel('Build locally → Send to PS5 (LAN) → Playden: Play from Import Folder. '
                              'Start Receiver requires an already running PS5 ELF loader. '
                              'Copy to USB writes to a drive attached to this computer.')
        receiver_note.setWordWrap(True)
        form.addRow(receiver_note)
        details_layout.addLayout(form)
        self.cleanup_after_usb = QCheckBox('Remove local generated package after verified USB copy (ask first)')
        self.cleanup_after_usb.setChecked(False)
        self.cleanup_after_usb.setToolTip('Off by default each session. Requires full checksum verification and explicit path confirmation. Original instances are never removed.')
        details_layout.addWidget(self.local_management)
        self.local_management.form.addRow(self.cleanup_after_usb)
        self.queue_table = QTableWidget(0, 3)
        self.queue_table.setHorizontalHeaderLabels(['Instance', 'Progress / result / error', 'Output'])
        self.queue_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.setMaximumHeight(140)
        self.queue_table.setToolTip('Select a successful result to open that package. Copy/send processes all successful packages in this build queue.')
        self.queue_table.itemSelectionChanged.connect(self.select_queue_result)
        self.queue_table.setMinimumHeight(60)
        self.queue_table.hide()
        details_layout.addWidget(self.queue_table)
        self.usb_aggregate = QLabel()
        self.usb_aggregate.setTextFormat(Qt.TextFormat.PlainText)
        self.usb_aggregate.setWordWrap(True)
        self.usb_aggregate.hide()
        details_layout.addWidget(self.usb_aggregate)
        self.usb_table = QTableWidget(0, 3)
        self.usb_table.setHorizontalHeaderLabels(['USB package', 'Progress / result', 'Destination'])
        self.usb_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.usb_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.usb_table.setMinimumHeight(60)
        self.usb_table.setMaximumHeight(140)
        self.usb_table.hide()
        details_layout.addWidget(self.usb_table)
        self.build_button = QPushButton('Build Playden package')
        layout.addWidget(self.build_button)
        actions = QHBoxLayout()
        self.open_output_button = QPushButton('Open Output Folder')
        self.copy_usb_button = QPushButton('Copy to USB')
        self.copy_ps5_button = QPushButton('Send to PS5 (LAN)')
        self.retry_usb_button = QPushButton('Retry USB Transfer')
        self.cancel_button = QPushButton('Cancel Operation')
        for button in (self.open_output_button, self.copy_usb_button, self.copy_ps5_button, self.retry_usb_button, self.cancel_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.prepare_existing_button = QPushButton('Prepare Fabric…')
        self.prepare_existing_button.setEnabled(False)
        self.local_management.form.addRow(self.prepare_existing_button)
        self.fabric_package_status = QLabel()
        self.fabric_package_status.setWordWrap(True)
        details_layout.addWidget(self.fabric_package_status)
        self.completed_label = QLabel('No validated package built in this session yet.')
        self.completed_label.setTextFormat(Qt.TextFormat.PlainText)
        self.completed_label.setWordWrap(True)
        details_layout.addWidget(self.completed_label)
        self.build_reason = QLabel()
        self.build_reason.setTextFormat(Qt.TextFormat.PlainText)
        self.build_reason.setWordWrap(True)
        self.build_reason.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self.build_reason)
        self.status = QLabel('Ready to scan local installations.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setMaximumHeight(self.status.fontMetrics().lineSpacing() * 3)
        layout.addWidget(self.status)
        self.progress_bar = QProgressBar()
        self.reset_progress()
        layout.addWidget(self.progress_bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setMinimumHeight(60)
        self.log.setMaximumHeight(90)
        layout.addWidget(self.log)
        self.copy_log_button.clicked.connect(lambda: QApplication.clipboard().setText(self.log.toPlainText()))
        instructions = QLabel(INSTRUCTIONS)
        instructions.setTextFormat(Qt.TextFormat.PlainText)
        instructions.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.local_management.form.addRow(instructions)
        details_layout.addStretch()
        self.details_scroll.setWidget(details)
        self.splitter.addWidget(self.details_scroll)
        self.splitter.handle(1).setToolTip('Drag the divider to resize the installation list and package settings.')
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([280, 180])
        state = self.settings.value('layout/instanceSplitter')
        if isinstance(state, QByteArray):
            self.splitter.restoreState(state)
        self.setCentralWidget(body)
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(min(1080, available.width() - 32), min(760, available.height() - 48))
        self.scan_button.clicked.connect(self.scan)
        self.folder_button.clicked.connect(self.add_folder)
        self.validate_button.clicked.connect(self.validate_existing)
        self.prepare_existing_button.clicked.connect(self.prepare_existing)
        self.remap_java_button.clicked.connect(self.choose_remap_java)
        self.prepare_fabric.toggled.connect(self.fabric_preparation_toggled)
        self.remap_java.textChanged.connect(self.save_remap_java)
        self.output_button.clicked.connect(self.choose_output)
        self.cache_button.clicked.connect(self.choose_cache)
        self.build_button.clicked.connect(self.start_build)
        self.open_output_button.clicked.connect(self.open_output_folder)
        self.copy_usb_button.clicked.connect(self.copy_to_usb)
        self.copy_ps5_button.clicked.connect(self.copy_to_ps5)
        self.retry_usb_button.clicked.connect(self.retry_transfer)
        self.cancel_button.clicked.connect(self.cancel_operation)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.output.textChanged.connect(self.update_build_button)
        self.output.textEdited.connect(self.output_edited)
        self.cache.textChanged.connect(self.update_build_button)
        self.privacy_cleanup.toggled.connect(self.update_build_button)
        self.ps5_destination.currentIndexChanged.connect(self.save_ps5_destination)
        self.ps5_host.textChanged.connect(self.save_ps5_host)
        self.receiver_manager.credentials_changed.connect(self.update_build_button)
        self.receiver_manager.state_changed.connect(self.receiver_state_changed)
        self.allow_runtime_download.toggled.connect(lambda value: self.settings.setValue('build/runtimeDownload', value))
        self.offline.toggled.connect(lambda value: self.settings.setValue('build/offline', value))
        self.privacy_cleanup.toggled.connect(lambda value: self.settings.setValue('build/privacyCleanup', value))
        self.controller_layout.toggled.connect(lambda value: self.settings.setValue('build/controllerLayout', value))
        self.ps5_pipeline.toggled.connect(lambda checked: self.settings.setValue('transfer/pipelineFiles', checked))
        self.username.textChanged.connect(self.offline_name_changed)
        self.username.editingFinished.connect(self.normalise_offline_name)
        self.update_build_button()
        QTimer.singleShot(0, self.scan)

    def show_technical_paths(self, shown: bool) -> None:
        for column in (3, 4):
            self.table.setColumnHidden(column, not shown)

    @Slot(str)
    def receiver_state_changed(self, state: str) -> None:
        detail = self.receiver_manager.error.text()
        if state == 'Unknown' and not detail:
            return
        message = f'PS5 receiver: {state}' + (' — ' + detail if detail else '')
        if message != self._last_receiver_log:
            self.log.appendPlainText(message)
            self._last_receiver_log = message

    @Slot(int)
    def save_ps5_destination(self, index: int) -> None:
        destination = self.ps5_destination.itemData(index)
        if destination not in ('internal', 'usb0'):
            return
        self.settings.setValue('transfer/destination', destination)
        self.settings.sync()

    @Slot(str)
    def save_ps5_host(self, host: str) -> None:
        if not host.strip():
            self.settings.remove('transfer/host')
        else:
            try:
                self.settings.setValue('transfer/host', lan_address(host.strip()))
            except ValueError:
                self.update_build_button()
                return
        self.settings.sync()
        self.update_build_button()

    def save_offline_name(self, name: str) -> None:
        self.settings.setValue('player/offlineName', offline_player_name(name))
        self.settings.sync()

    @Slot(str)
    def offline_name_changed(self, name: str) -> None:
        self.save_offline_name(name)
        self.update_build_button()

    @Slot()
    def normalise_offline_name(self) -> None:
        self.username.setText(offline_player_name(self.username.text()))

    def reset_progress(self) -> None:
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)

    def busy(self) -> bool:
        return self.worker_thread is not None

    @Slot(int, Qt.SortOrder)
    def sort_changed(self, column: int, order: Qt.SortOrder) -> None:
        if 0 <= column < self.table.columnCount():
            self.settings.setValue('table/sortColumn', column)
            self.settings.setValue('table/sortOrder', order.value)

    def selected_installation(self) -> Installation | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        # Use item identity rather than assuming the visual row equals the model index.
        item = self.table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    @Slot()
    def selection_changed(self) -> None:
        if not self.busy():
            self.reset_progress()
        installation = self.selected_installation()
        self.source_path.setText(str(installation.instance) if installation else '')
        self.version.setText(installation.version if installation else '')
        self.client_path.setText(str(installation.client) if installation and installation.client else '')
        self.source_path.setToolTip(self.source_path.text())
        self.client_path.setToolTip(self.client_path.text())
        self.loader.setText(installation.loader_label if installation else '')
        selection_key = (installation.instance, installation.version) if installation else None
        changed_instance = installation is not None and selection_key != self._output_selection
        if installation and (changed_instance or (not self._output_is_manual and (not self.output.text().strip() or self.output.text() == self._suggested_output))):
            try:
                suggested = suggest_output(installation)
                if self.output.text().strip():
                    suggested = unique_output(Path(self.output.text()).expanduser().parent / sanitise_name(installation.name))
                self._suggested_output = str(suggested)
                self._output_selection = selection_key
                self._output_is_manual = False
                self.output.setText(self._suggested_output)
            except (OSError, ValueError, RuntimeError):
                # Keep manual output selection available if no safe suggestion can be made.
                pass
        selected = self.selected_installations()
        key = tuple(sorted((str(item.instance), item.version, tuple((loader.name, loader.version) for loader in item.loaders), str(item.needs_fabric_preparation)) for item in selected))
        if key != self._fabric_selection:
            self._fabric_selection = key
            self._prepare_fabric_requested = True
            relevant = any(len(item.loaders) == 1 and item.loaders[0].name == 'Fabric' and item.needs_fabric_preparation is not False for item in selected)
            with QSignalBlocker(self.prepare_fabric):
                self.prepare_fabric.setChecked(relevant)
        self.update_build_button()

    @Slot()
    def update_build_button(self) -> None:
        # Re-evaluate dynamic local-file blockers without losing row identities or
        # changing the selection during status-column sorting.
        with QSignalBlocker(self.table):
            sorting = self.table.isSortingEnabled()
            self.table.setSortingEnabled(False)
            for row in range(self.table.rowCount()):
                identity = self.table.item(row, 0)
                status = self.table.item(row, 6)
                if identity is not None and status is not None:
                    compatibility = identity.data(Qt.ItemDataRole.UserRole).compatibility
                    label = compatibility.label
                    if status.text() != label:
                        status.setText(label)
                    status.setToolTip(compatibility.details)
            self.table.setSortingEnabled(sorting)
        request, reason = self.build_request()
        self.build_button.setEnabled(request is not None)
        self.build_button.setText(f'Build {len(self.selected_installations())} packages' if len(self.selected_installations()) > 1 else 'Build Playden package')
        self.build_button.setToolTip(reason or 'Build this selected local installation.')
        explanation = 'Ready to build. Each instance is validated independently. For multiple rows, output folders use instance names under the displayed output parent.'
        notices = '\n'.join(f'{installation.name}: {installation.compatibility.details}'
                            for installation in self.selected_installations())
        self.build_reason.setText(('Build disabled: ' + reason if reason else explanation) + ('\n' + notices if notices else ''))
        self.username.setEnabled(not self.busy())
        relevant = any(len(item.loaders) == 1 and item.loaders[0].name == 'Fabric' and item.needs_fabric_preparation is not False for item in self.selected_installations())
        self.prepare_fabric.setEnabled(not self.busy() and relevant)
        state = self.completed_package.preparation if self.completed_package else None
        self.prepare_existing_button.setEnabled(not self.busy() and state is not None and state.required and not state.prepared)
        self.prepare_existing_button.setToolTip(str(self.completed_package.path) if self.completed_package else 'Validate an existing Fabric package first.')
        self.fabric_package_status.setText(state.message if state is not None and self.completed_package is not None and self.completed_package.kind == 'launch-package' else '')
        open_path = self.output_view if self.output_view is not None else (self.completed_package.path if self.completed_package else None)
        self.open_output_button.setEnabled(not self.busy() and open_path is not None and open_path.is_dir())
        transferable = any(not candidate.skip_reason for candidate in self.usb_candidates())
        self.copy_usb_button.setEnabled(not self.busy() and transferable)
        candidates = [c for c in self.usb_candidates() if not c.skip_reason]
        network_ready = False
        try:
            lan_address(self.ps5_host.text().strip())
            if not self.receiver_manager.ready:
                raise ValueError('Receiver is busy; finish its active transfer first.' if self.receiver_manager.state == 'Running' else 'Start or pair with the receiver first.')
            for candidate in candidates:
                safe_relative(candidate.source.name, package=True)
            network_ready = True
        except ValueError:
            pass
        self.copy_ps5_button.setEnabled(not self.busy() and transferable and network_ready)
        self.copy_ps5_button.setToolTip('Start Receiver or enter the six-digit code and Pair, then send validated packages. Local files are retained.')
        if self.queue_dialog is not None and self.queue_dialog.copy_button is not None:
            self.queue_dialog.copy_button.setEnabled(not self.busy() and transferable)
        self.retry_usb_button.setEnabled(not self.busy() and self.last_transfer is not None and self.transfer_retry_available)
        self.cancel_button.setEnabled(self.busy() and self.operation in ('build', 'prepare', 'transfer', 'network', 'validate', 'cleanup') and not self.cancellation.cancelled)

    def build_request(self) -> tuple[LocalBuildRequest | None, str]:
        requests, reason = self.build_requests()
        return (requests[0] if requests else None), reason

    def build_requests(self) -> tuple[list[LocalBuildRequest], str]:
        return selection_readiness(self.selected_installations(), self.output.text(), self.cache.text(), self.busy(),
                                   username=offline_player_name(self.username.text()), privacy_cleanup=self.privacy_cleanup.isChecked(),
                                   controller_layout=self.controller_layout.isChecked(),
                                   allow_runtime_download=self.allow_runtime_download.isChecked(), offline=self.offline.isChecked(), prepare_fabric=self._prepare_fabric_requested,
                                   remap_java=Path(self.remap_java.text().strip()).expanduser() if self.remap_java.text().strip() else None)

    def confirm_untested(self, requests: list[LocalBuildRequest]) -> bool:
        untested = [request.installation for request in requests
                    if request.installation.compatibility.status in (Status.UNTESTED, Status.RETEST)]
        if not untested:
            return True
        names = '\n'.join(f'• {item.name} ({item.loader_label}, {item.version})' for item in untested)
        warning = ('A selected configuration has a confirmed PS5 launch failure. Headless-prepared packages require physical retesting; no fix is PS5-verified.'
                   if any(item.compatibility.status == Status.RETEST for item in untested) else UNTESTED_WARNING)
        answer = QMessageBox.warning(self, 'Build untested versions?', warning + '\n\n' + names,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def selected_installations(self) -> list[Installation]:
        result = []
        for index in sorted(self.table.selectionModel().selectedRows(), key=lambda index: index.row()):
            item = self.table.item(index.row(), 0)
            if item is not None:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result

    def select_queue_result(self) -> None:
        if self.busy():
            return
        row = self.queue_table.currentRow()
        item = self.queue_table.item(row, 1)
        package = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(package, ValidatedPackage):
            self.completed_package = package
            self.use_build_queue = True
            if len(self.build_records) > 1:
                self.completed_label.setText(queue_summary(self.build_records))
                self.output_view = package.path
            else:
                self.completed_label.setText(f'Validated package: {package.path}')
                self.output_view = package.path
            self.update_build_button()
        else:
            self.output_view = None
            self.completed_package = None
            self.update_build_button()

    def queue_status(self, text: str, package: ValidatedPackage | None = None) -> None:
        if self.operation == 'build' and self.queue_row >= 0:
            item = self.queue_table.item(self.queue_row, 1)
            assert item is not None
            item.setText(text)
            item.setToolTip(text)
            if package is not None:
                item.setData(Qt.ItemDataRole.UserRole, package)

    def operation_controls(self) -> tuple[QWidget, ...]:
        return (self.scan_button, self.folder_button, self.validate_button, self.output_button,
                self.cache_button, self.output, self.cache, self.table, self.privacy_cleanup, self.allow_runtime_download, self.username, self.controller_layout,
                self.prepare_fabric, self.remap_java, self.remap_java_button, self.prepare_existing_button,
                self.cleanup_after_usb, self.ps5_destination, self.receiver_manager, self.offline, self.ps5_pipeline)

    def start_task(self, operation: str, work: Callable[[ProgressCallback], object]) -> None:
        if self.busy():
            return
        self.operation = operation
        self.transfer_estimator = ThroughputEstimator()
        self.outcome = 'running'
        self.pending_dialog = None
        self.worker_thread = QThread(self)
        self.task = Task(work, self.cancellation)
        self.task.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.task.run)
        self.task.result.connect(self.task_result)
        self.task.error.connect(self.task_error)
        self.task.insufficient_space.connect(self.insufficient_space)
        self.task.cancelled.connect(self.task_cancelled)
        self.task.progress.connect(self.task_progress)
        self.task.finished.connect(self.worker_thread.quit)
        self.task.finished.connect(self.task.deleteLater)
        self.worker_thread.finished.connect(self.task_finished)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        for widget in self.operation_controls():
            widget.setEnabled(False)
        self.update_build_button()
        self.reset_progress()
        self.worker_thread.start()

    @Slot()
    def scan(self) -> None:
        if self.busy():
            return
        self.cancellation = CancellationToken()
        paths = list(self.paths)
        self.status.setText('Scanning known launcher locations and selected folders…')
        self.start_task('scan', lambda progress: discover(paths))

    @Slot()
    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, 'Choose a launcher data folder, instance or version folder')
        if folder:
            path = Path(folder)
            self._preferred_path = path
            if path not in self.paths:
                self.paths.append(path)
            self.scan()

    @Slot()
    def output_edited(self) -> None:
        self._output_is_manual = True
        self.update_build_button()

    @Slot()
    def choose_output(self) -> None:
        # A save dialog permits a NEW path; the exporter creates a directory there.
        path, _ = QFileDialog.getSaveFileName(self, 'Choose a new package folder', self.output.text() or str(Path.home() / 'Minecraft-Playden-Package'))
        if path:
            self._output_is_manual = True
            self.output.setText(path)

    @Slot()
    def choose_cache(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, 'Choose a download cache folder', self.cache.text())
        if folder:
            self.cache.setText(folder)

    @Slot()
    def start_build(self) -> None:
        if self.busy():
            return
        requests, reason = self.build_requests()
        if not requests:
            self.update_build_button()
            QMessageBox.warning(self, 'Cannot build package', reason)
            return
        if not self.confirm_untested(requests):
            return
        # Revalidate after the confirmation dialog; source files can change while it is open.
        requests, reason = self.build_requests()
        if not requests:
            self.update_build_button()
            QMessageBox.warning(self, 'Cannot build package', reason)
            return
        self.batch = len(requests) > 1
        self.build_queue = list(requests)
        self.build_records = [BuildRecord(request) for request in requests]
        self.use_build_queue = True
        self.usb_records = []
        self.usb_current = -1
        self.usb_space_error = None
        self.transfer_retry_available = False
        self.usb_table.hide()
        self.usb_aggregate.hide()
        self.output_view = requests[0].output.parent if self.batch else (self.completed_package.path if self.completed_package else None)
        for request in requests:
            self.remember_sources(request.installation)
        self.queue_table.show()
        self.queue_table.setRowCount(len(self.build_queue))
        for row, queued in enumerate(self.build_queue):
            for column, value in enumerate((queued.installation.name, 'Queued', str(queued.output))):
                self.queue_table.setItem(row, column, QTableWidgetItem(value))
        self.queue_row = -1
        self.log.clear()
        self.cancellation = CancellationToken()
        self.start_next_build()

    def start_next_build(self) -> None:
        request = self.build_queue.pop(0)
        self.queue_row += 1
        self.build_output = request.output
        self.build_records[self.queue_row].state = 'running'
        self.last_stage = ''
        self.log.appendPlainText(f'Building {request.installation.name} → {request.output}')
        self.status.setText(f'Preparing {request.installation.name}…')
        self.start_task('build', lambda progress: request.run(progress, self.cancellation))
        self.queue_status('Building')

    @Slot()
    def validate_existing(self) -> None:
        if self.busy():
            return
        folder = QFileDialog.getExistingDirectory(self, 'Select a package folder containing package.json')
        if folder:
            self.cancellation = CancellationToken()
            self.last_stage = ''
            self.log.clear()
            self.status.setText('Checking package inventory and launch configuration…')
            self.start_task('validate', lambda progress: check_package(Path(folder), progress, self.cancellation))

    @Slot(bool)
    def fabric_preparation_toggled(self, enabled: bool) -> None:
        self._prepare_fabric_requested = enabled
        self.update_build_button()

    @Slot(str)
    def save_remap_java(self, value: str) -> None:
        if value.strip():
            self.settings.setValue('preparation/java', value.strip())
        else:
            self.settings.remove('preparation/java')

    @Slot()
    def choose_remap_java(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, 'Choose host-native JDK 17+ Java executable', self.remap_java.text())
        if filename:
            self.remap_java.setText(filename)

    @Slot()
    def prepare_existing(self) -> None:
        if self.busy() or self.completed_package is None:
            return
        package = self.completed_package
        if package.preparation is None or not package.preparation.required or package.preparation.prepared:
            return
        folder = QFileDialog.getExistingDirectory(self, 'Choose parent folder for a separate prepared package', str(package.path.parent))
        if not folder:
            return
        output = unique_output(Path(folder) / (package.name + ' Prepared'))
        java = Path(self.remap_java.text().strip()).expanduser() if self.remap_java.text().strip() else None
        self.cancellation = CancellationToken()
        self.last_stage = ''
        self.status.setText('Preparing a separate Fabric package; original files are retained…')
        self.start_task('prepare', lambda progress: prepare_existing_package(package.path, output, java, progress, self.cancellation))

    def confirm_unprepared_transfer(self, paths: list[Path]) -> bool:
        packages = [record.package for record in self.build_records if record.package is not None]
        if self.completed_package is not None:
            packages.append(self.completed_package)
        unprepared = {str(package.path) for package in packages if package.path in paths and package.preparation is not None
                      and package.preparation.required and not package.preparation.prepared}
        if not unprepared:
            return True
        answer = QMessageBox.warning(self, 'Transfer unprepared Fabric?',
                                     'Fabric preparation is required for these packages. They may fail during first-launch remapping on Playden. '
                                     'Use Prepare Fabric first, or transfer the legacy package for troubleshooting.\n\n' + '\n'.join(sorted(unprepared)),
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def refresh_transfer_estimate(self) -> None:
        if self.busy() and self._last_transfer_event is not None and self.last_stage in ('network-send', 'transfer-copy'):
            self.task_progress(self._last_transfer_event)

    @Slot(object)
    def task_progress(self, event: Progress) -> None:
        self._last_transfer_event = event
        usb_record = self.current_usb_record()
        if self.usb_batch_active and usb_record is not None:
            usb_record.detail = event.message + (f' ({event.completed}/{event.total})' if event.total else '')
            self.refresh_usb_results()
        self.queue_status(event.message + (f' ({event.completed}/{event.total})' if event.total else ''))
        if event.bytes_total is not None:
            fraction = event.completed / event.total if event.total else 0
            percent = min(100, max(0, int(fraction * 100)))
            percentage = f' {percent}%' if event.total is not None else ''
            unit = 'verified' if event.stage == 'network-verify' else 'sent' if event.stage.startswith('network-') else 'copied'
            timing = ''
            if event.stage in ('network-send', 'transfer-copy'):
                if self.last_stage != event.stage:
                    self.transfer_estimator = ThroughputEstimator()
                estimate = self.transfer_estimator.update(event.bytes_copied or 0, event.bytes_total, time.monotonic())
                speed = f'{format_size(int(estimate.rate))}/s' if estimate.rate is not None else 'measuring speed'
                timing = ' — stalled; waiting for data' if estimate.stalled else f' — {speed} — ETA {eta_text(estimate.eta)}'
            message = re.sub(r' — [0-9.]+ MiB/s$', '', event.message) if event.stage == 'network-send' else event.message
            self.status.setText(f'{message}{percentage} — {format_size(event.bytes_copied)} / {format_size(event.bytes_total)} {unit}{timing} — {event.current_file}')
            if self.usb_batch_active and usb_record is not None:
                self.status.setText(f'{usb_record.candidate.name}: ' + self.status.text())
            self.status.setToolTip(self.status.text())
            if event.total is None:
                self.progress_bar.setRange(0, 0)
                self.progress_bar.setValue(0)
            else:
                self.progress_bar.setRange(0, 1000)
                self.progress_bar.setValue(min(999, max(0, int(fraction * 1000))))
            if self.last_stage != event.stage:
                self.log.appendPlainText(event.message)
                self.last_stage = event.stage
            return
        suffix = f' ({event.completed}/{event.total})' if event.total is not None else ''
        self.status.setText(event.message + suffix + (f' — {event.current_file}' if event.current_file else ''))
        if self.usb_batch_active and usb_record is not None:
            self.status.setText(f'{usb_record.candidate.name}: ' + self.status.text())
        self.status.setToolTip(self.status.text())
        if event.total is not None and event.total > 0:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(min(999, max(0, int(event.completed / event.total * 1000))))
        else:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setValue(0)
        if self.last_stage != event.stage:
            self.log.appendPlainText(event.message)
            self.last_stage = event.stage

    @Slot(object)
    def task_result(self, result: object) -> None:
        if self.operation == 'network':
            if not isinstance(result, tuple) or not result or not all(isinstance(item, NetworkResult) for item in result):
                self.task_error('Receiver did not confirm publication; local outputs retained.')
                return
            self.network_results = result
            self.outcome = 'succeeded'
            summary = f'PS5 LAN: {len(result)} package(s) verified and publication confirmed. Local outputs retained. Open Playden → Play from Import Folder:\n\n' + '\n'.join(item.destination for item in result)
            self.status.setText(summary)
            self.log.appendPlainText(summary)
        elif self.operation == 'scan':
            assert isinstance(result, DiscoveryResult)
            previous = self.selected_installation()
            preferred = self._preferred_path or (previous.instance if previous else None)
            self._preferred_path = None
            self.discovery = result
            for installation in result.installations:
                self.remember_sources(installation)
            # Prevent sorting half-populated rows or emitting transient empty
            # selections while replacing the model's items.
            with QSignalBlocker(self.table):
                self.table.setSortingEnabled(False)
                self.table.setRowCount(0)
                self.table.setRowCount(len(result.installations))
                for row, installation in enumerate(result.installations):
                    values = [installation.name, installation.launcher, installation.version, str(installation.instance),
                              str(installation.client) if installation.client else 'Not available',
                              installation.loader_label,
                              installation.compatibility.label]
                    for col, value in enumerate(values):
                        item = InstallationItem(value, version=col == 2)
                        item.setToolTip(installation.compatibility.details if col == 6 else value)
                        if col == 0:
                            item.setData(Qt.ItemDataRole.UserRole, installation)
                        self.table.setItem(row, col, item)
                self.table.setSortingEnabled(True)
            self.log.clear()
            for warning in result.warnings:
                self.log.appendPlainText(warning)
            self.outcome = 'succeeded'
            self.status.setText(f'Found {len(result.installations)} installation(s). Select one, or add a source folder.' if result.installations else
                                'No installations found. Add a Prism/MultiMC instance or official Minecraft folder. See the log for scan warnings.')
            matches = []
            if preferred is not None:
                target = preferred.resolve()
                matches = [row for row, installation in enumerate(result.installations)
                           if installation.instance.resolve() == target or installation.instance.resolve().is_relative_to(target)
                           or target.is_relative_to(installation.instance.resolve())]
            choices = matches or list(range(len(result.installations)))
            selected = next((row for row in choices if result.installations[row].buildable), choices[0] if choices else None)
            # Keep an exact prior selection even if it has become unbuildable.
            # A user-selected Add source folder still takes priority.
            if previous is not None and preferred == previous.instance:
                same_path = [row for row in matches if result.installations[row].instance.resolve() == previous.instance.resolve()]
                selected = next((row for row in same_path if result.installations[row].version == previous.version),
                                same_path[0] if same_path else selected)
            if selected is not None:
                installation = result.installations[selected]
                for row in range(self.table.rowCount()):
                    row_item = self.table.item(row, 0)
                    if row_item is not None and row_item.data(Qt.ItemDataRole.UserRole) == installation:
                        self.table.selectRow(row)
                        self.table.scrollToItem(row_item)
                        break
            self.selection_changed()
        elif self.operation in ('build', 'validate', 'prepare'):
            if not isinstance(result, ValidatedPackage) or (self.operation == 'build' and result.path != self.build_output):
                self.task_error('Build returned without a validated package result; success was not reported.')
                return
            self.outcome = 'succeeded'
            if self.operation == 'build' and self.build_records:
                record = self.build_records[self.queue_row]
                record.state, record.package = 'completed', result
                if not self.batch:
                    self.output_view = result.path
                if result.generated is not None:
                    self.generated_outputs[result.generated.path] = result.generated
            else:
                self.output_view = result.path
                self.use_build_queue = False
                self.usb_records = []
                self.usb_current = -1
                self.usb_space_error = None
                self.transfer_retry_available = False
                self.usb_table.hide()
                self.usb_aggregate.hide()
            self.completed_package = result
            if result.generated is not None:
                self.generated_outputs[result.generated.path] = result.generated
            message = (f'Instance files validated at {result.path}. File-only import; launchability is unverified.' if result.kind == 'instance-import' else
                       (f'Validated package created at {result.path}.' if self.operation == 'build' else f'Package file validation passed at {result.path}.') +
                       ' Console/mod compatibility still requires testing.')
            if result.preparation is not None and result.kind == 'launch-package':
                message += '\n' + result.preparation.message
            self.status.setText(message)
            label = 'Last file import (launch unverified)' if result.kind == 'instance-import' else 'Last validated package'
            self.completed_label.setText(queue_summary(self.build_records) if self.operation == 'build' and self.batch else
                                         f'{label}: {result.name} — {result.path}')
            self.log.appendPlainText(message + ('\n' + INSTRUCTIONS if result.kind != 'instance-import' else ''))
            self.queue_status(f'Completed: {result.path}', result)
            if (self.operation == 'build' and not self.batch) or self.operation == 'prepare':
                self.pending_dialog = (result.path, False)
        elif self.operation == 'transfer':
            self.transfer_retry_available = False
            if not isinstance(result, TransferResult):
                self.task_error('Transfer returned without a completed copy/flush result; success was not reported.')
                return
            if self.last_transfer is None or result.source != self.last_transfer.source.resolve() or result.destination != self.last_transfer.destination:
                self.task_error('Transfer result does not match the selected source/destination; cleanup is unavailable.')
                return
            self.outcome = 'succeeded'
            self.transfer_verified = result.verified and self.last_transfer.verify_after_copy
            usb_record = self.current_usb_record()
            if self.usb_batch_active and usb_record is not None:
                usb_record.result = result
                usb_record.state = 'verified' if self.transfer_verified else 'unverified'
                usb_record.detail = ('Every copied file checksum-verified; writes flushed.' if self.transfer_verified else
                                     'Post-copy checksum verification skipped; writes flushed. Local output retained.')
                usb_record.cleanup = 'Local output retained (cleanup not requested or verification skipped).'
                self.refresh_usb_results()
            self.cleanup_transfer = (self.last_transfer, result)
            for record in self.build_records:
                if record.request.output.resolve() == result.source:
                    record.usb = result.destination
                    record.detail = ('USB checksum-verified.' if self.transfer_verified else
                                     'USB checksum verification skipped; local output retained.')
            if len(self.build_records) > 1:
                self.completed_label.setText(queue_summary(self.build_records))
            transfer_label = 'USB transfer verified' if self.transfer_verified else 'USB copy complete (post-copy verification skipped)'
            self.status.setText(f'{transfer_label}: {result.files} files, {format_size(result.bytes_copied)} at {result.destination}. Safely eject before unplugging.')
            self.log.appendPlainText(self.status.text())
            self.pending_dialog = (result.destination, True)

        elif self.operation == 'cleanup':
            if not isinstance(result, Path) or self.cleanup_transfer is None or result != self.cleanup_transfer[1].source or result.exists() or result.is_symlink():
                self.task_error('Cleanup did not confirm removal of its exact generated output path.')
                return
            self.outcome = 'succeeded'
            self.generated_outputs.pop(result, None)
            usb_record = self.current_usb_record()
            if self.usb_batch_active and usb_record is not None:
                usb_record.cleanup_state = 'removed'
                usb_record.cleanup = f'Local generated output removed after verified copy: {result}'
                self.refresh_usb_results()
            for row, record in enumerate(self.build_records):
                if record.request.output.resolve() == result:
                    record.removed = True
                    result_item = self.queue_table.item(row, 1)
                    if result_item is not None:
                        result_item.setText('Completed; local output removed after verified USB copy')
                    location_item = self.queue_table.item(row, 2)
                    if location_item is not None:
                        location_item.setText(f'{result} (removed locally; USB: {record.usb})')
            if self.completed_package is not None and self.completed_package.path == result:
                self.completed_package = None
            self.completed_label.setText(queue_summary(self.build_records) if self.build_records else f'Local generated output removed: {result}')
            self.status.setText(f'Local generated output removed after verified USB copy: {result}')
            self.log.appendPlainText(self.status.text())
            if len(self.build_records) <= 1:
                self.output_view = None
            if self.cleanup_transfer is not None:
                self.pending_dialog = (self.cleanup_transfer[1].destination, True)

    def record_copy_problem(self, message: str) -> None:
        if self.last_transfer is None:
            return
        for row, record in enumerate(self.build_records):
            if record.request.output.resolve() == self.last_transfer.source.resolve():
                record.detail = message
                item = self.queue_table.item(row, 1)
                if item is not None:
                    item.setText('Completed; ' + message)
        if self.build_records:
            self.completed_label.setText(queue_summary(self.build_records))

    @Slot(str)
    def task_error(self, message: str) -> None:
        self.outcome = 'uncertain' if self.operation == 'network' and 'Publication receipt was lost' in message else 'failed'
        self.reset_progress()
        if self.operation == 'transfer':
            self.transfer_retry_available = True
        usb_record = self.current_usb_record()
        if self.usb_batch_active and usb_record is not None:
            if self.operation == 'transfer':
                usb_record.state, usb_record.detail = 'failed', message
                usb_record.cleanup = 'Local output retained; transfer failed.'
            elif self.operation == 'cleanup':
                usb_record.cleanup_state = 'failed'
                usb_record.cleanup = 'Cleanup failed; inspect local output. Verified USB copy retained: ' + message
            self.refresh_usb_results()
        self.pending_dialog = None
        self.status.setText({'scan': 'Scan failed.', 'prepare': 'Fabric preparation failed; no new package was published. Select a host JDK if Java is unavailable.', 'validate': 'Package validation failed.',
                             'network': 'PS5 LAN transfer failed. Local outputs retained; see the log for completed packages and errors.',
                             'transfer': 'USB transfer failed. Retry is available; no success was reported.',
                             'cleanup': 'Local cleanup failed; the verified USB copy was not deleted.'}.get(self.operation, 'Build failed. No completed output was published.'))
        if self.operation == 'network':
            self.status.setText(('PS5 publication uncertain. Check the import path before retrying. ' if self.outcome == 'uncertain' else 'PS5 LAN transfer failed. ') + message)
        self.log.appendPlainText(message)
        if self.operation in ('transfer', 'cleanup'):
            self.record_copy_problem(('USB copy failed: ' if self.operation == 'transfer' else 'Cleanup incomplete; inspect local output: ') + message)
        self.queue_status('Failed: ' + message)
        if self.operation == 'build' and self.build_records:
            record = self.build_records[self.queue_row]
            record.state, record.detail = 'failed', message
        if not ((self.operation == 'build' and self.batch) or (self.usb_batch_active and len(self.usb_records) > 1)):
            QMessageBox.warning(self, 'Operation failed', message)

    @Slot()
    def task_finished(self) -> None:
        if self.operation == 'network':
            self.receiver_manager.set_transfer_active(False)
        self.worker_thread = None
        self.task = None
        if self.usb_batch_active and self.operation in ('transfer', 'cleanup'):
            if self.operation == 'transfer' and self.outcome == 'succeeded' and not self.cancellation.cancelled and self.maybe_cleanup():
                return
            self.pending_dialog = None
            if self.advance_usb_queue():
                return
            self.finish_usb_queue()
        if self.operation == 'build':
            if self.build_queue and not self.cancellation.cancelled:
                self.start_next_build()
                return
            if self.build_queue:
                for row in range(self.queue_row + 1, self.queue_table.rowCount()):
                    item = self.queue_table.item(row, 1)
                    assert item is not None
                    item.setText('Cancelled before starting')
                    self.build_records[row].state = 'not started'
                    self.build_records[row].detail = 'Cancelled before starting'
                self.build_queue.clear()
            consumed = any(
                (item := self.queue_table.item(row, 1)) is not None
                and isinstance(package := item.data(Qt.ItemDataRole.UserRole), ValidatedPackage)
                and str(package.path) == self.output.text()
                for row in range(self.queue_table.rowCount()))
            if consumed:
                # A consumed custom path is no longer a usable output. Refresh the
                # suggestion so the next selection/build is immediately available.
                self._output_is_manual = False
                self.output.setText(self._suggested_output)
                self.selection_changed()
            if self.batch:
                self.pending_dialog = None
                summary = queue_summary(self.build_records)
                self.completed_label.setText(summary)
                self.status.setText(summary.split('\n', 1)[0])
                self.log.appendPlainText(summary)
        for widget in self.operation_controls():
            widget.setEnabled(True)
        if self.space_dialog is not None and self.space_dialog.isVisible():
            self.space_dialog.check_space()
        self.reset_progress()
        succeeded = self.outcome == 'succeeded' and self.operation != 'scan'
        if self.operation == 'build' and self.batch:
            succeeded = succeeded and all(
                (item := self.queue_table.item(row, 1)) is not None
                and isinstance(item.data(Qt.ItemDataRole.UserRole), ValidatedPackage)
                for row in range(self.queue_table.rowCount()))
        if succeeded:
            self.progress_bar.setValue(1000)
        self.update_build_button()
        if self.operation == 'build' and self.batch:
            self.show_queue_completion()
        if self.operation == 'transfer' and not self.usb_records and self.maybe_cleanup():
            return
        if self.usb_summary_pending:
            self.usb_summary_pending = False
            self.show_usb_completion()
        if self.operation == 'network' and self.outcome == 'succeeded':
            self.show_network_completion()
        if self.pending_dialog is not None:
            path, transferred = self.pending_dialog
            self.pending_dialog = None
            self.show_completion(path, transferred)

    def show_network_completion(self) -> None:
        dialog = QMessageBox(QMessageBox.Icon.Information, 'Sent to PS5',
                             'Transfer verified and published on PS5. Your local package has been kept.',
                             QMessageBox.StandardButton.Ok, self)
        self.network_confirmation = dialog
        dialog.finished.connect(self.finish_network_completion)
        dialog.open()

    def finish_network_completion(self, _result: int) -> None:
        self.network_confirmation = None
        self.network_results = ()
        self.operation = ''
        self.outcome = ''
        self.last_stage = ''
        self.reset_progress()
        self.status.setText('Ready. Select another instance to build, or transfer your saved package again.')
        self.update_build_button()
        self.table.setFocus()

    def show_queue_completion(self) -> None:
        parent = self.build_records[0].request.output.parent
        self.queue_dialog = QueueCompletionDialog(queue_summary(self.build_records), parent, self,
                                                  lambda: self.open_directory(parent),
                                                  has_packages=any(record.package for record in self.build_records),
                                                  copy_usb=self.copy_to_usb if any(not c.skip_reason for c in self.usb_candidates()) else None)
        self.queue_dialog.finished.connect(lambda: setattr(self, 'queue_dialog', None))
        if any(not c.skip_reason for c in self.usb_candidates()):
            self.queue_dialog.add_ps5_action(self.prepare_ps5_transfer)
        self.queue_dialog.open()

    def remember_sources(self, installation: Installation) -> None:
        self.sources_seen.update(path.resolve() for path in (installation.instance, installation.launcher_root,
                                 installation.libraries_root,
                                 installation.client.parent if installation.client else None) if path is not None)

    def maybe_cleanup(self) -> bool:
        if not self.cleanup_requested or self.cleanup_transfer is None or self.outcome != 'succeeded':
            return False
        request, result = self.cleanup_transfer
        receipt = self.generated_outputs.get(result.source)
        if receipt is None:
            self.note_usb_cleanup('Local output retained: only packages generated in this session are eligible for cleanup.')
            return False
        protected = tuple(self.sources_seen)
        try:
            receipt.eligible(request, result, protected)
        except (OSError, ValueError) as exc:
            self.note_usb_cleanup(f'Local output retained: {exc}')
            return False
        if not self.confirm_cleanup(receipt.path, result.destination):
            self.note_usb_cleanup('Local cleanup declined; output retained.')
            return False
        if not self.usb_batch_active:
            self.cancellation = CancellationToken()
        self.status.setText(f'Checking verified copies before removing local output: {receipt.path}')
        self.start_task('cleanup', lambda progress: receipt.remove_verified(request, result, confirmed=True,
                        protected=protected, progress=progress, cancellation=self.cancellation))
        return True

    def confirm_cleanup(self, local: Path, usb: Path) -> bool:
        dialog = QMessageBox(self)
        dialog.setWindowTitle('Remove verified local package?')
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f'Permanently delete this locally generated package directory and its copied contents?\n\n{local}\n\n'
                       f'Verified USB copy (kept): {usb}\nOriginal Minecraft instances are never deleted. '
                       'Local and USB contents will be rechecked before removal.')
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        answer = dialog.exec()
        dialog.deleteLater()
        return answer == QMessageBox.StandardButton.Yes

    def show_completion(self, path: Path, transferred: bool) -> None:
        self.completion_dialog = CompletionDialog(path, self, lambda: self.open_directory(path),
                                                  None if transferred or (self.completed_package and self.completed_package.kind == 'instance-import') else self.copy_to_usb,
                                                  transferred=transferred, file_only=bool(self.completed_package and self.completed_package.kind == 'instance-import'),
                                                  verified=self.transfer_verified)
        if not transferred and self.completed_package and self.completed_package.kind != 'instance-import':
            self.completion_dialog.add_ps5_action(self.prepare_ps5_transfer)
        self.completion_dialog.finished.connect(lambda: setattr(self, "completion_dialog", None))
        self.completion_dialog.open()

    def prepare_ps5_transfer(self) -> None:
        """Reveal connection settings for the package just built; no folder repicking."""
        for dialog in (self.completion_dialog, self.queue_dialog):
            if dialog is not None:
                dialog.accept()
        self.details_scroll.ensureWidgetVisible(self.ps5_host)
        self.ps5_host.setFocus()
        self.status.setText('Select the PS5 IP, choose Start Receiver, or enter its six-digit code and Pair, then Send to PS5 (LAN). The PS5 ELF loader must already be running.')

    def open_directory(self, path: Path) -> None:
        def failed(detail: str) -> None:
            message = (f'Could not open folder: {path}\n{detail}\n'
                       'Open your file manager and paste this path into its location bar. '
                       'On KDE, check that Dolphin and xdg-utils are installed and a default folder handler is configured.')
            self.log.appendPlainText(message)
            QMessageBox.warning(self, 'Could not open folder', message)
        try:
            if not path.is_dir():
                failed('The folder no longer exists or the drive is unavailable.')
            elif needs_system_opener():
                opener = open_system_folder(path.resolve(), self, failed)
                self.folder_openers.append(opener)
                opener.destroyed.connect(lambda: self.folder_openers.remove(opener))
            elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
                failed('Qt could not hand the folder to the desktop service.')
        except (OSError, RuntimeError) as exc:
            failed(str(exc))

    @Slot()
    def open_output_folder(self) -> None:
        path = self.output_view or (self.completed_package.path if self.completed_package else None)
        if path is not None:
            self.open_directory(path)
        else:
            QMessageBox.warning(self, 'No output selected', 'Build or validate a package, or select a successful output in the results table.')

    def usb_candidates(self) -> list[TransferCandidate]:
        return transfer_candidates(self.build_records, self.completed_package, self.use_build_queue)

    def current_usb_record(self) -> UsbRecord | None:
        return self.usb_records[self.usb_current] if 0 <= self.usb_current < len(self.usb_records) else None

    def note_usb_cleanup(self, message: str) -> None:
        self.log.appendPlainText(message)
        record = self.current_usb_record()
        if self.usb_batch_active and record is not None:
            record.cleanup = message
            self.refresh_usb_results()

    def refresh_usb_results(self) -> None:
        for row, record in enumerate(self.usb_records):
            values = (record.candidate.name, f'{record.state}: {record.detail}' +
                      (f'\n{record.cleanup}' if record.cleanup else ''),
                      str(record.request.destination) if record.request is not None else 'Not transferred')
            for column, value in enumerate(values):
                item = self.usb_table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.usb_table.setItem(row, column, item)
                item.setText(value)
                item.setToolTip(value)
        current = self.current_usb_record()
        total = sum(record.request is not None for record in self.usb_records)
        finished = sum(record.state in ('verified', 'unverified', 'failed', 'cancelled') for record in self.usb_records)
        prefix = f'USB packages: {finished}/{total} finished'
        if self.usb_batch_active and current is not None:
            prefix += f' — current: {current.candidate.name}'
        self.usb_aggregate.setText(prefix + '\n' + usb_summary(self.usb_records).split('\n', 1)[0])

    @Slot()
    def copy_to_ps5(self) -> None:
        if self.busy():
            return
        candidates = [candidate for candidate in self.usb_candidates() if not candidate.skip_reason]
        if not candidates:
            return
        try:
            host = lan_address(self.ps5_host.text().strip())
            if not self.receiver_manager.ready:
                raise ValueError('Receiver is busy; finish its active transfer first.' if self.receiver_manager.state == 'Running' else 'Start or pair with the receiver first.')
            destination = self.ps5_destination.currentData()
            if destination not in ROOTS:
                raise ValueError('Choose internal or USB0 storage.')
        except ValueError as exc:
            QMessageBox.warning(self, 'PS5 connection settings', str(exc))
            return
        try:
            for candidate in candidates:
                safe_relative(candidate.source.name, package=True)
        except ValueError as exc:
            QMessageBox.warning(self, 'Invalid package folder name', str(exc))
            return
        paired = self.receiver_manager.paired
        if paired is None:
            QMessageBox.warning(self, 'Receiver not paired', 'Start Receiver or enter the six-digit PS5 code and Pair first.')
            return
        requests = tuple(NetworkRequest(item.source, host, paired.key.hex(), destination, port=paired.info.port,
                                        pipeline_files=self.ps5_pipeline.isChecked()) for item in candidates)
        if not self.confirm_unprepared_transfer([request.source for request in requests]):
            return
        self.network_results = ()
        self.cancellation = CancellationToken()
        self.last_stage = ''
        self.status.setText(f'Preparing {len(requests)} package(s) for PS5 LAN transfer…')
        self.receiver_manager.set_transfer_active(True)
        self.start_task('network', lambda progress: run_network_queue(requests, progress, self.cancellation))

    @Slot()
    def copy_to_usb(self) -> None:
        if self.busy():
            return
        candidates = self.usb_candidates()
        available = [candidate for candidate in candidates if not candidate.skip_reason]
        if not available:
            self.update_build_button()
            return
        if not self.confirm_unprepared_transfer([candidate.source for candidate in available]):
            return
        names = [candidate.source.name for candidate in available]
        dialog = (DriveDialog(names[0], self, package_names=names) if len(names) > 1 else DriveDialog(names[0], self))
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.target is not None:
            try:
                records = plan_transfers(self.usb_candidates(), dialog.target, not dialog.skip_verification.isChecked())
                self.start_usb_queue(records, drive=dialog.target)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, 'Cannot prepare USB queue', str(exc))
        dialog.deleteLater()

    def start_usb_queue(self, records: list[UsbRecord], *, drive: DriveTarget | None = None) -> None:
        if self.busy():
            return
        self.usb_records = records
        self.usb_drive = drive or next((record.request.drive for record in records if record.request is not None), None)
        self.reset_progress()
        self.usb_current = -1
        self.usb_batch_active = True
        self.usb_cleanup_opt = self.cleanup_after_usb.isChecked()
        self.usb_space_error = None
        self.usb_summary_pending = False
        self.transfer_retry_available = False
        self.cancellation = CancellationToken()
        self.usb_table.setRowCount(0)
        self.usb_table.setRowCount(len(records))
        self.usb_table.show()
        self.usb_aggregate.show()
        self.refresh_usb_results()
        self.log.appendPlainText('Starting USB queue:\n' + usb_summary(records))
        if not self.advance_usb_queue():
            self.finish_usb_queue()
            self.update_build_button()
            self.show_usb_completion()
            self.usb_summary_pending = False

    def advance_usb_queue(self) -> bool:
        if self.cancellation.cancelled:
            for record in self.usb_records:
                if record.state == 'queued':
                    record.state, record.detail = 'not started', 'Cancelled before copying; local output retained.'
        for row, record in enumerate(self.usb_records):
            if record.state == 'queued' and record.request is not None:
                self.usb_current = row
                record.state, record.detail = 'copying', 'Preparing package validation and USB copy…'
                self.refresh_usb_results()
                self.start_transfer(record.request, queued=True)
                return True
        return False

    def finish_usb_queue(self) -> None:
        self.usb_batch_active = False
        self.transfer_retry_available = any(record.retryable for record in self.usb_records)
        failed = any(record.state == 'failed' or record.cleanup_state == 'failed' for record in self.usb_records) or not any(record.result is not None for record in self.usb_records)
        self.outcome = 'cancelled' if self.cancellation.cancelled else 'failed' if failed else 'succeeded'
        summary = usb_summary(self.usb_records)
        self.status.setText(summary.split('\n', 1)[0])
        self.log.appendPlainText(summary)
        self.refresh_usb_results()
        if len(self.usb_records) == 1 and self.usb_records[0].result is not None:
            self.pending_dialog = (self.usb_records[0].result.destination, True)
        elif len(self.usb_records) > 1:
            self.usb_summary_pending = True

    def show_usb_completion(self) -> None:
        request = next((record.request for record in self.usb_records if record.request is not None), None)
        drive = request.drive if request is not None else self.usb_drive
        if drive is None:
            return
        parent = drive.root / 'playden/import'
        summary = usb_summary(self.usb_records)
        if any(record.result is not None for record in self.usb_records):
            summary += '\n\nSafely eject the USB drive in your operating system before unplugging it.'
        self.usb_dialog = QueueCompletionDialog(summary, parent, self,
                            lambda: self.open_directory(parent), has_packages=False, title='USB queue results',
                            retry_usb=self.retry_transfer if any(record.retryable for record in self.usb_records) else None,
                            check_space=self.show_usb_space_dialog if self.usb_space_error is not None else None)
        self.usb_dialog.finished.connect(lambda: setattr(self, 'usb_dialog', None))
        self.usb_dialog.open()

    def retry_usb_queue(self) -> None:
        if self.busy() or not any(record.retryable for record in self.usb_records):
            return
        for record in self.usb_records:
            if record.retryable:
                record.state, record.detail, record.cleanup = 'queued', '', ''
                record.result = None
                record.cleanup_state = 'retained'
        self.usb_batch_active = True
        self.usb_cleanup_opt = self.cleanup_after_usb.isChecked()
        self.usb_space_error = None
        self.usb_summary_pending = False
        self.transfer_retry_available = False
        self.cancellation = CancellationToken()
        if not self.advance_usb_queue():
            self.finish_usb_queue()
            self.update_build_button()

    def show_usb_space_dialog(self) -> None:
        if self.usb_space_error is None:
            return
        request = next((record.request for record in self.usb_records if record.retryable), None)
        if request is not None:
            self.space_dialog = SpaceDialog(request.drive, self.usb_space_error.required, self)
            self.space_dialog.accepted.connect(self.retry_usb_queue)
            if self.busy():
                self.space_dialog.retry_button.setEnabled(False)
            self.space_dialog.open()

    def start_transfer(self, request: TransferRequest, *, queued: bool = False) -> None:
        if self.busy():
            return
        if self.space_dialog is not None:
            self.space_dialog.close()
        if not queued:
            self.usb_records = []
            self.usb_current = -1
            self.usb_batch_active = False
            self.usb_space_error = None
        self.last_transfer = request
        self.cleanup_transfer = None
        self.cleanup_requested = (self.usb_cleanup_opt if queued else self.cleanup_after_usb.isChecked()) and request.verify_after_copy
        self.transfer_retry_available = False
        if not queued:
            self.cancellation = CancellationToken()
        self.last_stage = ''
        self.status.setText(f'Preparing USB copy to {request.destination}…')
        self.start_task('transfer', lambda progress: request.run(progress, self.cancellation))

    @Slot(object)
    def insufficient_space(self, error: InsufficientSpace) -> None:
        self.outcome = 'failed'
        self.reset_progress()
        self.transfer_retry_available = True
        self.status.setText(str(error))
        self.log.appendPlainText(str(error))
        record = self.current_usb_record()
        if self.usb_batch_active and record is not None:
            record.state, record.detail = 'failed', str(error)
            record.cleanup = 'Local output retained; insufficient storage.'
            if self.usb_space_error is None or error.required > self.usb_space_error.required:
                self.usb_space_error = error
            self.record_copy_problem(str(error))
            self.refresh_usb_results()
            if len(self.usb_records) == 1:
                self.show_usb_space_dialog()
            return
        if self.last_transfer is not None:
            self.space_dialog = SpaceDialog(self.last_transfer.drive, error.required, self)
            self.space_dialog.accepted.connect(self.retry_transfer)
            self.space_dialog.retry_button.setEnabled(False)
            self.space_dialog.open()

    @Slot()
    def retry_transfer(self) -> None:
        if not self.busy() and any(record.retryable for record in self.usb_records):
            request = next(record.request for record in self.usb_records if record.retryable)
            assert request is not None
            try:
                request.drive.check()
            except OSError:
                QMessageBox.warning(self, 'Reconnect the drive', 'Reconnect the selected USB drive before retrying failed copies.')
                return
            if self.usb_space_error is not None:
                self.show_usb_space_dialog()
            else:
                self.retry_usb_queue()
            return
        if self.busy() or self.last_transfer is None:
            return
        try:
            self.last_transfer.drive.check()
        except OSError:
            QMessageBox.warning(self, 'Reconnect the drive', 'Reconnect the USB drive, then choose Copy to USB and confirm its label/mount point again. Verified staging files will be reused.')
            return
        self.start_transfer(self.last_transfer)

    @Slot()
    def cancel_operation(self) -> None:
        if self.busy() and self.operation in ('build', 'prepare', 'transfer', 'network', 'validate', 'cleanup'):
            self.cancellation.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText('Cancellation requested. Waiting for current I/O to stop safely…')

    @Slot(str)
    def task_cancelled(self, message: str) -> None:
        self.outcome = 'cancelled'
        if self.usb_batch_active:
            self.cancellation.cancel()
        self.reset_progress()
        self.queue_status('Cancelled')
        usb_record = self.current_usb_record()
        if self.usb_batch_active and usb_record is not None:
            if self.operation == 'transfer':
                usb_record.state, usb_record.detail = 'cancelled', message
                usb_record.cleanup = 'Local output retained; copy cancelled.'
            elif self.operation == 'cleanup':
                usb_record.cleanup_state = 'cancelled'
                usb_record.cleanup = 'Local cleanup cancelled; verified USB copy retained.'
            self.refresh_usb_results()
        if self.operation in ('transfer', 'cleanup'):
            self.record_copy_problem('USB copy cancelled; local output retained.' if self.operation == 'transfer' else 'Local cleanup cancelled; verified USB copy retained.')
        if self.operation == 'build' and self.build_records:
            self.build_records[self.queue_row].state = 'cancelled'
        if self.operation == 'transfer':
            self.transfer_retry_available = True
        self.pending_dialog = None
        self.status.setText({'transfer': 'USB transfer cancelled.', 'prepare': 'Fabric preparation cancelled; no new package was published.', 'validate': 'Package validation cancelled.',
                             'network': 'PS5 LAN transfer cancelled. Earlier confirmed packages remain on PS5; local outputs retained.',
                             'cleanup': 'Local cleanup cancelled; verified USB copy retained.'}.get(self.operation, 'Build cancelled. No completed output was published.'))
        self.log.appendPlainText(message)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.busy() or self.receiver_manager.busy():
            QMessageBox.information(self, 'Operation in progress', 'Please wait for the operation to finish, or request cancellation, before closing the packager.')
            event.ignore()
        else:
            self.receiver_manager.shutdown_local()
            self.settings.setValue('layout/instanceSplitter', self.splitter.saveState())
            event.accept()


def run(cache: Path, paths: list[Path]) -> int:
    configure_desktop_identity()
    app = QApplication(sys.argv[:1])
    app.setApplicationName('Minecraft Playden Packager')
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(DESKTOP_ID)
    app.setWindowIcon(application_icon())
    window = MainWindow(cache, paths)
    window.show()
    return app.exec()
