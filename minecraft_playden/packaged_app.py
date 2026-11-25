"""GUI-only frozen desktop startup, provenance and persistent diagnostics."""
import json
import importlib
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import tempfile
import time


def resources() -> Path:
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent / 'release'))


def metadata() -> dict:
    return json.loads((resources() / 'build-metadata.json').read_text(encoding='utf-8'))


def configure_logging() -> Path:
    if sys.platform == 'darwin':
        root = Path.home() / 'Library/Logs'
    elif os.name == 'nt':
        root = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local')
    else:
        root = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    log_root = root / ('MinecraftPlaydenPackager' if sys.platform == 'darwin' else 'MinecraftPlaydenPackager/logs')
    try:
        log_root.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_root / 'desktop.log', maxBytes=2_000_000, backupCount=2, encoding='utf-8')
    except OSError:
        log_root = Path(tempfile.mkdtemp(prefix='playden-desktop-log-'))
        handler = RotatingFileHandler(log_root / 'desktop.log', maxBytes=2_000_000, backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    sys.excepthook = lambda kind, value, tb: logging.critical('Unhandled desktop exception', exc_info=(kind, value, tb))
    return log_root / 'desktop.log'


def main() -> int:
    info = metadata()
    if '--build-info' in sys.argv:
        # Also write to an explicit file for Windows' console-free executable.
        if '--output' in sys.argv:
            target = Path(sys.argv[sys.argv.index('--output') + 1])
            with target.open('x', encoding='utf-8') as stream:
                json.dump(info, stream, indent=2)
        elif sys.stdout is not None:
            print(json.dumps(info, indent=2))
        return 0
    log_path = configure_logging()
    logging.info('Desktop startup: %s', json.dumps(info, sort_keys=True))
    # Supply a known CA bundle without disabling HTTPS verification. An explicitly
    # configured SSL_CERT_FILE still wins for managed/corporate environments.
    certifi = importlib.import_module('certifi')
    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
    from PySide6.QtCore import QSettings, QTimer, qInstallMessageHandler
    from PySide6.QtWidgets import QApplication, QMessageBox
    from . import gui_app
    from .downloads import default_cache
    from .branding import application_icon, configure_desktop_identity, DESKTOP_ID

    qInstallMessageHandler(lambda kind, context, message: logging.warning('Qt: %s', message))
    configure_desktop_identity()
    app = QApplication(sys.argv[:1])
    app.setApplicationName('Minecraft Playden Packager')
    app.setApplicationVersion(info['version'])
    app.setDesktopFileName(DESKTOP_ID)
    app.setWindowIcon(application_icon())
    smoke = '--smoke-test' in sys.argv
    smoke_workspace = None
    if smoke:
        # Exercise the real window in isolation, without scanning installations,
        # downloading files, building Minecraft or writing to a mounted USB drive.
        from .discovery import DiscoveryResult, Installation
        smoke_workspace = tempfile.TemporaryDirectory(prefix='playden-smoke-settings-')
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, smoke_workspace.name)
        base = Path(tempfile.gettempdir()) / 'playden-smoke-missing-source'
        fixtures = DiscoveryResult(tuple(Installation(name, 'Smoke fixture', version, base / name, None)
                                         for name, version in [('beta', '1.10'), ('Alpha', '1.9'), ('Gamma', '26.3')]))
        def smoke_discover(*args, **kwargs) -> DiscoveryResult:
            return fixtures
        gui_app.discover = smoke_discover
    window = gui_app.MainWindow(default_cache(), [])
    previous = ''

    def record_log() -> None:
        nonlocal previous
        current = window.log.toPlainText()
        added = current[len(previous):] if current.startswith(previous) else current
        if added.strip():
            logging.info('GUI: %s', added.strip())
        previous = current

    window.log.textChanged.connect(record_log)
    window.show()
    if '--diagnostics' in sys.argv and not smoke:
        QTimer.singleShot(0, lambda: QMessageBox.information(window, 'Desktop diagnostics',
                          f'Log file: {log_path}\nSource: {info["source_commit"]}\n'
                          f'Preview: {info["source_dirty"]}'))
    if smoke:
        deadline = time.monotonic() + 15

        def check() -> None:
            if window.busy() and time.monotonic() < deadline:
                QTimer.singleShot(50, check)
                return
            try:
                from PySide6.QtCore import QSize, Qt
                from .progress import Progress
                assert not window.busy(), 'Scanner did not complete'
                assert window.table.rowCount() == 3
                assert window.table.height() >= 220
                assert not app.windowIcon().isNull()
                assert not window.windowIcon().isNull()
                from .branding import ICON_SIZES, icon_directory
                from PySide6.QtGui import QImage
                for size in ICON_SIZES:
                    rendered = window.windowIcon().pixmap(QSize(size, size), 1.0).toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                    expected_icon = QImage(str(icon_directory() / f'playdencreeper-{size}.png')).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                    assert rendered == expected_icon, f'Window icon pixels differ at {size}px'
                from . import artwork
                assert artwork.validate_png(artwork.default('icon.png')) == (512, 512)
                assert artwork.validate_png(artwork.default('background.png'))[0] > 0
                assert artwork.validate_png(artwork.default('icon.png', 'Fabric')) == (512, 512)
                from .receiver_manager import ReceiverConfiguration, ensure_receiver_elf
                from .operations import CancellationToken
                receiver = ReceiverConfiguration.load()
                assert receiver.elf.is_file(), 'Bundled receiver ELF missing'
                ensure_receiver_elf(receiver.sdk, receiver.elf, CancellationToken())
                window.table.sortItems(2, Qt.SortOrder.AscendingOrder)
                assert window.table.item(0, 2).text() == '1.9'  # type: ignore[union-attr]
                window.table.selectRow(0)
                assert window.selected_installation().name == 'Alpha'  # type: ignore[union-attr]
                window.output.setText('smoke-output')
                window.table.sortItems(0, Qt.SortOrder.DescendingOrder)
                assert window.output.text() == 'smoke-output'
                assert window.progress_bar.value() == 0, 'Idle progress must start at zero'
                window.task_progress(Progress('smoke', 'Frozen GUI smoke passed', 1, 1))
                assert window.progress_bar.maximum() == 1000
                assert window.progress_bar.value() == 999, 'Only a completed build may show 100%'
                logging.info('FROZEN_SMOKE_OK')
                code = 0
            except Exception:
                logging.exception('FROZEN_SMOKE_FAILED')
                code = 1
            window.close()
            app.exit(code)

        QTimer.singleShot(100, check)
    try:
        return app.exec()
    finally:
        if smoke_workspace is not None:
            smoke_workspace.cleanup()
