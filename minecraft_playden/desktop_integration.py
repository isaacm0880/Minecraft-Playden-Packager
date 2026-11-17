"""Launch the host's folder handler without frozen Python/Qt library pollution."""
import os
from pathlib import Path
import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment

from .process_environment import desktop_environment


def needs_system_opener() -> bool:
    return sys.platform.startswith('linux') and (bool(getattr(sys, 'frozen', False)) or bool(os.environ.get('APPIMAGE')))


def open_system_folder(path: Path, parent: QObject, failed: Callable[[str], None]) -> QProcess:
    """Use a tracked asynchronous process so launch failures cannot disappear."""
    process = QProcess(parent)
    env = QProcessEnvironment()
    for key, value in desktop_environment().items():
        env.insert(key, value)
    process.setProcessEnvironment(env)
    process.setProgram('/usr/bin/xdg-open')
    process.setArguments([str(path)])
    reported = False

    def error(detail: str) -> None:
        nonlocal reported
        if not reported:
            reported = True
            failed(detail)

    def finished(code: int, status: QProcess.ExitStatus) -> None:
        if code != 0 or status != QProcess.ExitStatus.NormalExit:
            detail = bytes(process.readAllStandardError().data()).decode(errors='replace').strip()
            error(f'xdg-open exited with status {code}. {detail}')
        process.deleteLater()

    def launch_error(reason: QProcess.ProcessError) -> None:
        error('xdg-open: ' + process.errorString())
        if reason == QProcess.ProcessError.FailedToStart:
            process.deleteLater()

    process.finished.connect(finished)
    process.errorOccurred.connect(launch_error)
    process.start()
    return process
