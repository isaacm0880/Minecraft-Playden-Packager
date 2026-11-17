"""Host child-process environment without frozen Python/Qt library pollution."""
import os


def desktop_environment() -> dict[str, str]:
    env = dict(os.environ)
    # PyInstaller prepends its libraries; KDE's executables must load host Qt.
    original = env.pop('LD_LIBRARY_PATH_ORIG', '')
    env.pop('LD_LIBRARY_PATH', None)
    if original:
        env['LD_LIBRARY_PATH'] = original
    for key in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH',
                'PYTHONHOME', 'PYTHONPATH'):
        env.pop(key, None)
    return env
