"""Optional desktop entry point. The CLI does not import Qt."""
import argparse
import sys
from pathlib import Path
from .downloads import default_cache
from . import __version__


def launch(cache: Path, paths: list[Path]) -> int:
    try:
        import PySide6
        if PySide6.__version__ == '6.12.0' and sys.version_info < (3, 12):
            print('PySide6 6.12.0 has a known signal crash on Python 3.11 (PYSIDE-3474). '
                  'Create a fresh virtual environment and install with: python -m pip install -e ".[gui]"', file=sys.stderr)
            return 1
        from .gui_app import run
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith('PySide6'):
            print('Desktop dependencies are missing. Install them with: python -m pip install ".[gui]"', file=sys.stderr)
            return 1
        raise
    return run(cache, paths)


def main() -> int:
    parser = argparse.ArgumentParser(description='Minecraft Playden Packager desktop (local Vanilla/Fabric packaging)')
    parser.add_argument('--version', action='version', version=f'Minecraft Playden Packager {__version__}')
    parser.add_argument('--cache', type=Path, default=default_cache())
    parser.add_argument('--path', type=Path, action='append', default=[], help='Additional launcher or instance folder to scan; repeatable')
    args = parser.parse_args()
    return launch(args.cache, args.path)


if __name__ == '__main__':
    raise SystemExit(main())
