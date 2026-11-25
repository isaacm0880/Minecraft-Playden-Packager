"""Check the tracked/public source set without printing potential secret values."""
import hashlib
from pathlib import Path
import re
import subprocess

from .audit import SECRET_PATTERNS

GAME_SUFFIXES = {'.jar', '.class', '.mca', '.mcr', '.exe', '.appimage', '.zip', '.p12', '.pfx', '.key', '.keystore'}
PRIVATE_PARTS = {'instances', 'saves', '.aws', '.venv', '.cache', 'builds', 'dist'}
PRIVATE_NAMES = {'package.json', 'accounts.json', 'launcher_accounts.json', '.env', 'desktop.log'}
PRIVATE_REFERENCES = [re.compile(rb'Minecraft-Playden-Packager' + rb'-DEV'),
                      re.compile(rb'/(?:mnt/[A-Za-z0-9_-]+/Codex|home/isaac|Users/isaac)(?:/|\b)')]
OBSOLETE = re.compile(rb'(?i)(?:Import[ ]+recipe|Recipe[ ]*>[ ]*Import|web[ ]+recipe[ ]+import)')
SYNTHETIC_CLIENT = 'tests/fixtures/client.jar'
OWNED_JAVA8_CLASS = 'minecraft_playden/resources/java8/Java8Launch.class'
OWNED_JAVA8_SHA256 = '174127a697b1b2b67ce7ed9baf907e817cb47f011103e063a181adadc66610ac'
# The fixture archive contains a single dummy class payload: "fixture-only".
SYNTHETIC_CLIENT_SHA256 = 'a75b07dd049ea1e37b36cd00af507c9a45beda0d16984da1c2703632b28c8c98'


def audit_paths(root: Path, names: list[str]) -> dict:
    checked = 0
    for name in sorted(set(names)):
        path = root / name
        if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink() or not path.is_file():
            raise ValueError(f'Unsafe public source input: {name}')
        data = path.read_bytes()
        if name == SYNTHETIC_CLIENT:
            if hashlib.sha256(data).hexdigest() != SYNTHETIC_CLIENT_SHA256:
                raise ValueError('Synthetic fixture content changed; review required')
        elif name == OWNED_JAVA8_CLASS:
            if hashlib.sha256(data).hexdigest() != OWNED_JAVA8_SHA256:
                raise ValueError('Owned Java 8 bootstrap content changed; review required')
        elif (path.suffix.lower() in GAME_SUFFIXES or set(Path(name).parts) & PRIVATE_PARTS
              or path.name.casefold() in PRIVATE_NAMES):
            raise ValueError(f'Forbidden generated/game/private source input: {name}')
        if any(re.search(pattern, data) for pattern in SECRET_PATTERNS):
            raise ValueError(f'Potential credential in public source: {name}; value suppressed')
        if any(pattern.search(data) for pattern in PRIVATE_REFERENCES):
            raise ValueError(f'Internal reference in public source: {name}')
        if path.suffix.lower() in {'.md', '.rst', '.txt'} and OBSOLETE.search(data):
            raise ValueError(f'Obsolete workflow instruction in public documentation: {name}')
        checked += 1
    return {'passed': True, 'source_files_checked': checked,
            'synthetic_fixture_exception': SYNTHETIC_CLIENT,
            'limitations': 'Known-pattern/content audit; not a guarantee against arbitrary encoded secrets.'}


def audit_source(root: Path) -> dict:
    names = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--cached', '--others',
                                     '--exclude-standard', '-z'], text=True).split('\0')
    return audit_paths(root, list(filter(None, names)))


if __name__ == '__main__':
    print(audit_source(Path.cwd()))
