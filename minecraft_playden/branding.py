"""Shared source/frozen desktop identity and official icon resources."""
import ctypes
import logging
from pathlib import Path
import sys

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
DESKTOP_ID = 'minecraft-playden-packager'


def icon_directory() -> Path:
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return root / 'resources/icons'


def application_icon():
    from PySide6.QtGui import QIcon
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addFile(str(icon_directory() / f'playdencreeper-{size}.png'))
    icon.addFile(str(icon_directory() / 'playdencreeper.png'))
    return icon


def configure_desktop_identity() -> None:
    if sys.platform == 'win32':
        try:
            shell = getattr(ctypes, 'WinDLL')('shell32', use_last_error=True)
            identify = shell.SetCurrentProcessExplicitAppUserModelID
            identify.argtypes, identify.restype = [ctypes.c_wchar_p], ctypes.c_long
            if identify('MinecraftPlayden.Packager') < 0:
                logging.warning('Windows taskbar application identity could not be set')
        except OSError:
            logging.warning('Windows taskbar application identity is unavailable')
