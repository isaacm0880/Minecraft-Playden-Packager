"""Audit distribution trees and their frozen Python module inventories."""
import hashlib
import json
from pathlib import Path
import re

# TLS libraries legitimately contain PEM delimiter constants. Require key material,
# not merely the parser's marker string, before treating a bundled library as a key.
SECRET_PATTERNS = [rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\r\n]+[A-Za-z0-9+/=\r\n]{64,}-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                   rb'\bgh[pousr]_[A-Za-z0-9]{20,}\b', rb'\bgithub_pat_[A-Za-z0-9_]{20,}\b',
                   rb'\bAKIA[A-Z0-9]{16}\b',
                   rb'\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\b']
FORBIDDEN_PARTS = {'assets', 'libraries', 'natives', 'java', 'jre', 'jdk', 'saves', 'instances',
                   'builds', '.venv', '.git', '.aws', '.cache', '.pytest_cache', '.mypy_cache', 'tests'}
FORBIDDEN_FILES = {'launcher_accounts.json', 'accounts.json', '.env', 'package.json', 'playden.args',
                   'launch.args', 'java.exe', 'jvm.dll', 'wine-minecraft.log'}


def audit_tree(root: Path, executable: Path, expected: dict) -> dict:
    inventory = {}
    symlinks = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f'External distribution symlink: {relative}')
            symlinks[relative] = str(path.readlink())
            continue
        if not path.is_file():
            continue
        parts = {part.casefold() for part in path.relative_to(root).parts}
        from minecraft_playden.java8 import BOOTSTRAP_SHA256
        resource = 'minecraft_playden/resources/java8/Java8Launch.class'
        owned_bootstrap = relative == resource or relative.endswith('/' + resource)
        if owned_bootstrap and hashlib.sha256(path.read_bytes()).hexdigest() != BOOTSTRAP_SHA256:
            raise ValueError('Owned Java 8 bootstrap checksum mismatch')
        if (parts & FORBIDDEN_PARTS or path.name.casefold() in FORBIDDEN_FILES
                or (path.suffix.lower() in {'.jar', '.class', '.mca', '.mcr', '.key', '.pfx', '.p12', '.jks', '.keystore'}
                    and not owned_bootstrap)):
            raise ValueError(f'Forbidden game/private artifact: {relative}')
        if path.name.startswith(('ld-linux', 'libc.so', 'libGL.so', 'libEGL.so', 'libstdc++.so', 'libgcc_s.so',
                                 'libfontconfig.so', 'libfreetype.so', 'libexpat.so')):
            raise ValueError(f'Unnecessary host runtime/driver: {relative}')
        data = path.read_bytes()
        if any(re.search(pattern, data) for pattern in SECRET_PATTERNS):
            raise ValueError(f'Potential credential in {relative}; value suppressed')
        inventory[relative] = hashlib.sha256(data).hexdigest()
    from PyInstaller.archive.readers import CArchiveReader  # type: ignore[import-not-found, import-untyped]
    archive = CArchiveReader(str(executable))
    pyz_name = next(name for name in archive.toc if name.endswith('.pyz'))
    pyz = archive.open_embedded_archive(pyz_name)
    modules = sorted(pyz.toc)
    if any(name.startswith(('tests.', 'pytest', 'mypy', 'tools.', 'release.')) for name in modules):
        raise ValueError('Development/test modules found in application archive')
    required = {'minecraft_playden.gui_app', 'minecraft_playden.packaged_app', 'minecraft_playden.transfer',
                'minecraft_playden.volumes', 'minecraft_playden.discovery', 'minecraft_playden.formatting',
                'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets'}
    # Extension modules are stored outside PYZ and checked by their actual files.
    for module in required:
        if module.startswith('PySide6.'):
            if not any(module.split('.')[-1] in name for name in inventory):
                raise ValueError(f'Missing Qt extension: {module}')
        elif module not in modules:
            raise ValueError(f'Missing application module: {module}')
    infos = [path for path in root.rglob('build-metadata.json') if not path.is_symlink()]
    if len(infos) != 1:
        raise ValueError('Expected exactly one embedded provenance record')
    actual = json.loads(infos[0].read_text(encoding='utf-8'))
    for key in ('source_commit', 'source_tree_sha256', 'source_dirty', 'preview'):
        if actual[key] != expected[key]:
            raise ValueError(f'Build provenance mismatch: {key}')
    branding = actual['branding']
    if branding != expected['branding']:
        raise ValueError('Build branding metadata mismatch')
    icon_root = infos[0].parent / 'resources/icons'
    if json.loads((icon_root / 'icon-manifest.json').read_text()) != branding:
        raise ValueError('Bundled branding manifest mismatch')
    for name, checksum in branding['files'].items():
        if hashlib.sha256((icon_root / name).read_bytes()).hexdigest() != checksum:
            raise ValueError(f'Bundled official icon mismatch: {name}')
    windows_icon_verified = None
    if executable.suffix.lower() == '.exe':
        from .verify_windows_icon import verify
        verify(executable, icon_root / 'playdencreeper.ico')
        windows_icon_verified = True
    macos_bundle = None
    if root.suffix == '.app':
        from .macos_bundle import verify_bundle
        macos_bundle = verify_bundle(root, actual)
    if (root / 'AppRun').exists():
        expected_png = branding['files']['playdencreeper-256.png']
        for name in ('minecraft-playden-packager.png', '.DirIcon'):
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected_png:
                raise ValueError(f'AppImage launcher icon mismatch: {name}')
        for size in branding['icon_sizes']:
            path = root / f'usr/share/icons/hicolor/{size}x{size}/apps/minecraft-playden-packager.png'
            if hashlib.sha256(path.read_bytes()).hexdigest() != branding['files'][f'playdencreeper-{size}.png']:
                raise ValueError(f'AppDir theme icon mismatch: {size}')
        for entry in (root / 'minecraft-playden-packager.desktop', root / 'usr/share/applications/minecraft-playden-packager.desktop'):
            if 'Icon=minecraft-playden-packager\n' not in entry.read_text():
                raise ValueError('Desktop entry does not refer to the official launcher icon')
    return {'passed': True, 'file_count': len(inventory), 'files': inventory, 'symlinks': symlinks,
            'python_modules': modules, 'source_commit': actual['source_commit'],
            'source_tree_sha256': actual['source_tree_sha256'], 'preview': actual['preview'], 'branding': branding,
            'windows_executable_icon_verified': windows_icon_verified, 'macos_bundle': macos_bundle,
            'limitations': 'Pattern and inventory audit, not a guarantee against arbitrary encoded secrets.'}
