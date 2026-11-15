"""Read-only diskutil metadata for mounted external macOS volumes; no sudo."""
from pathlib import Path
import plistlib
import subprocess
from xml.parsers.expat import ExpatError


def volume_info(root: Path) -> dict:
    try:
        result = subprocess.run(['/usr/sbin/diskutil', 'info', '-plist', str(root)],
                                capture_output=True, check=True, timeout=5)
        info = plistlib.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException, ValueError, ExpatError) as exc:
        raise OSError(f'Cannot inspect external volume {root}: {exc}') from exc
    if not isinstance(info, dict):
        raise OSError(f'Invalid diskutil metadata for {root}')
    return info


def external_volume(root: Path, info: dict) -> bool:
    """Require a physical, writable external mount, including fixed USB SSDs."""
    return (root.parent == Path('/Volumes') and root.name not in ('', '.', '..')
            and info.get('MountPoint') == str(root) and info.get('Mounted') is True
            and info.get('Internal') is False and info.get('DiskImage', False) is False
            and info.get('VirtualOrPhysical') != 'Virtual'
            and info.get('BusProtocol') in ('USB', 'FireWire', 'Thunderbolt', 'PCI-Express', 'Secure Digital', 'SD')
            and info.get('Writable', not info.get('VolumeReadOnly', True)) is True
            and info.get('VolumeReadOnly', False) is False and info.get('MediaReadOnly', False) is False
            and bool(info.get('VolumeUUID')) and bool(info.get('DeviceIdentifier')))
