"""Conservative external block-device classification; never mounts or writes devices."""
import ctypes
import os
import ntpath
import re
import struct
import sys
from pathlib import Path


def _unescape(value: str) -> str:
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)


def linux_external_mounts(mountinfo: str, sys_root: Path = Path('/sys')) -> set[str]:
    mounts = []
    for line in mountinfo.splitlines():
        fields = line.split()
        try:
            split = fields.index('-')
            mounts.append((fields[2], _unescape(fields[3]), _unescape(fields[4]), fields[5], fields[split + 1], _unescape(fields[split + 2])))
        except (ValueError, IndexError):
            continue
    def device(number: str, source: str) -> tuple[Path, bool] | None:
        try:
            link = sys_root / 'dev/block' / number
            if not link.exists() and source.startswith('/dev/'):
                # Btrfs uses an anonymous mount device number; consult its
                # actual block source instead of that anonymous number.
                link = sys_root / 'class/block' / Path(source).name
            node = link.resolve(strict=True)
            # Partitions inherit their physical disk's transport/removable metadata.
            disk = node.parent if (node / 'partition').exists() else node
            ancestry = (disk, *disk.parents)
            removable = (disk / 'removable').read_text().strip() == '1'
            if (disk / 'device/type').exists() and (disk / 'device/type').read_text().strip() == '5':
                return disk, False  # Optical media is not a package destination.
            usb = any(re.fullmatch(r'usb\d+', p.name) for p in ancestry)
            sd = any(p.name.startswith('mmc_host') for p in ancestry)
            return disk, removable or usb or sd
        except OSError:
            return None
    devices = {number: device(number, source) for number, _, _, _, _, source in mounts}
    protected = {'/', '/home', '/boot', '/boot/efi', '/efi', '/usr', '/var', '/run', '/tmp'}
    system_disks = set()
    for number, _, path, *_ in mounts:
        info = devices[number]
        if path in protected and info is not None:
            system_disks.add(info[0])
    result = set()
    for number, root, path, options, filesystem, _ in mounts:
        info = devices[number]
        # A non-root filesystem view is a bind/subvolume; duplicate full-root
        # views are ambiguous binds, so omit both rather than guessing.
        duplicates = sum(n == number and r == root for n, r, *_ in mounts)
        if (info is None or not info[1] or info[0] in system_disks or root != '/' or duplicates != 1
                or 'rw' not in options.split(',') or path in protected
                or filesystem.startswith(('fuse', 'nfs', 'cifs', 'smb', 'overlay'))):
            continue
        result.add(path)
    return result


def _windows_storage(root: str) -> tuple[int, int | None]:
    """GetDriveTypeW plus STORAGE_DEVICE_DESCRIPTOR BusType (no elevated handle)."""
    # QStorageInfo uses forward slashes; Win32 requires a trailing backslash.
    root = root.replace('/', '\\').rstrip('\\') + '\\'
    kernel = getattr(ctypes, 'WinDLL')('kernel32', use_last_error=True)
    kernel.GetDriveTypeW.argtypes, kernel.GetDriveTypeW.restype = [ctypes.c_wchar_p], ctypes.c_uint32
    drive_type = kernel.GetDriveTypeW(root)
    if drive_type not in (2, 3):  # Removable or fixed; never network/CD/RAM/unknown.
        return drive_type, None
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [ctypes.c_void_p], ctypes.c_int
    kernel.DeviceIoControl.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    kernel.DeviceIoControl.restype = ctypes.c_int
    handle = kernel.CreateFileW('\\\\.\\' + root.rstrip('\\/'), 0, 3, None, 3, 0, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        return drive_type, None
    try:
        query = ctypes.create_string_buffer(12)  # StorageDeviceProperty, PropertyStandardQuery
        output = ctypes.create_string_buffer(4096)
        returned = ctypes.c_uint32()
        if kernel.DeviceIoControl(handle, 0x2D1400, query, 12, output, len(output), ctypes.byref(returned), None) and returned.value >= 32:
            return drive_type, struct.unpack_from('<I', output.raw, 28)[0]
        return drive_type, None
    finally:
        kernel.CloseHandle(handle)


def windows_external(root: str, system_drive: str | None = None) -> bool:
    system_drive = system_drive or os.environ.get('SystemDrive', 'C:')
    if ntpath.normcase(root).rstrip('\\') == ntpath.normcase(system_drive).rstrip('\\'):
        return False
    try:
        drive_type, bus = _windows_storage(root)
    except OSError:
        return False
    return drive_type in (2, 3) and (drive_type == 2 or bus in (4, 7, 12, 13))  # 1394, USB, SD, MMC


def external_mounts(roots: list[str]) -> set[str]:
    if sys.platform.startswith('linux'):
        try:
            return linux_external_mounts(Path('/proc/self/mountinfo').read_text())
        except OSError:
            return set()
    if sys.platform == 'darwin':
        from .macos_storage import external_volume, volume_info
        found = set()
        for root in roots:
            path = Path(root)
            if path.parent != Path('/Volumes'):
                continue
            try:
                if not path.is_symlink() and external_volume(path, volume_info(path)):
                    found.add(root)
            except OSError:
                continue  # Unplugged, inaccessible or non-local storage: fail closed.
        return found
    if os.name == 'nt':
        return {root for root in roots if windows_external(root)}
    return set()
