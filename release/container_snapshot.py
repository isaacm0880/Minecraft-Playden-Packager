"""Overlay reviewed source files on a disposable local clone inside the container."""
from pathlib import Path
import shutil
import subprocess

source, target = Path('/input'), Path('/work/source')
names = subprocess.check_output(['git', '-C', str(source), 'ls-files', '--cached', '--others',
                                 '--exclude-standard', '-z']).decode().split('\0')
for name in filter(None, names):
    if not (name.startswith(('minecraft_playden/', 'release/', 'tests/', 'docs/', '.github/workflows/', 'tools/', 'ps5/'))
            or name in ('pyproject.toml', 'LICENSE', '.gitignore', '.gitattributes', 'CHANGELOG.md', 'THIRD_PARTY.md', 'RELEASE_NOTES.md') or name.startswith('README')):
        continue
    path = source / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Unsafe snapshot input: {name}')
    destination = target / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, destination)
