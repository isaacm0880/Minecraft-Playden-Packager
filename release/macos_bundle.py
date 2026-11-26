"""Audit .app identity, official icon and native Mach-O architecture."""
import hashlib
from pathlib import Path
import plistlib
import re
import subprocess

APP_NAME = 'Minecraft Playden Packager.app'
BUNDLE_ID = 'io.github.isaacm0880.minecraft-playden-packager'
MACHO_MAGICS = {b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xce',
                b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}


def bundle_version(package_version: str) -> str:
    """Apple's bundle version fields require three numeric components."""
    match = re.fullmatch(r'(\d+\.\d+\.\d+)(?:[a-z]+\d+)?', package_version)
    if not match:
        raise ValueError('Unsupported package version for macOS bundle')
    return match.group(1)


def verify_bundle(app: Path, expected: dict) -> dict:
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if (info.get('CFBundleIdentifier') != BUNDLE_ID or info.get('CFBundleDisplayName') != 'Minecraft Playden Packager'
            or info.get('CFBundleShortVersionString') != bundle_version(expected['version'])
            or info.get('CFBundleVersion') != bundle_version(expected['version'])
            or info.get('CFBundleExecutable') != 'MinecraftPlaydenPackager'):
        raise ValueError('macOS bundle identity/version mismatch')
    for key in ('NSRemovableVolumesUsageDescription', 'NSDocumentsFolderUsageDescription', 'NSDownloadsFolderUsageDescription'):
        if not info.get(key):
            raise ValueError(f'Missing macOS access explanation: {key}')
    icon_name = info.get('CFBundleIconFile', '')
    if not isinstance(icon_name, str) or Path(icon_name).name != icon_name:
        raise ValueError('Invalid macOS bundle icon path')
    if not icon_name.endswith('.icns'):
        icon_name += '.icns'
    icon = app / 'Contents/Resources' / icon_name
    if hashlib.sha256(icon.read_bytes()).hexdigest() != expected['branding']['files']['playdencreeper.icns']:
        raise ValueError('macOS bundle does not use the official ICNS')
    architecture = expected['architecture']
    if architecture not in ('arm64', 'x86_64'):
        raise ValueError('Invalid macOS target architecture')
    checked = 0
    for path in app.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open('rb') as stream:
            magic = stream.read(4)
        if magic in MACHO_MAGICS:
            archs = subprocess.check_output(['/usr/bin/lipo', '-archs', str(path)], text=True).split()
            if architecture not in archs or (path.parent.name == 'MacOS' and archs != [architecture]):
                raise ValueError(f'Wrong Mach-O architecture: {path.name}')
            checked += 1
    if not checked:
        raise ValueError('No Mach-O executables found in app bundle')
    return {'bundle_identifier': BUNDLE_ID, 'architecture': architecture, 'macho_files': checked,
            'icon_verified': True, 'developer_id_signed': False, 'notarized': False}
