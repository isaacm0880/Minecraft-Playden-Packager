"""Mounted-volume identity and durability without external utilities."""
import ctypes
import errno
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path


class DriveUnavailable(OSError):
    pass


@dataclass(frozen=True)
class VolumeIdentity:
    key: str
    filesystem: str
    device: int
    inode: int


def _mounted_identity(root: Path) -> VolumeIdentity:
    info = root.stat()
    if sys.platform.startswith('linux'):
        def unescape(value: str) -> str:
            return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)
        with Path('/proc/self/mountinfo').open() as stream:
            for line in stream:
                fields = line.split()
                if Path(unescape(fields[4])).resolve() == root:
                    separator = fields.index('-')
                    return VolumeIdentity(':'.join((fields[0], fields[2], fields[separator + 2])),
                                          fields[separator + 1], info.st_dev, info.st_ino)
        raise DriveUnavailable('Select the root of a mounted USB or external volume, not an ordinary folder.')
    if sys.platform == 'darwin':
        from .macos_storage import external_volume, volume_info
        data = volume_info(root)
        if not external_volume(root, data):
            raise DriveUnavailable('Select a writable physical external volume mounted under /Volumes. Check Files and Folders permissions in System Settings.')
        return VolumeIdentity(f"{data['DeviceIdentifier']}:{data['VolumeUUID']}",
                              str(data.get('FilesystemType', '')).lower(), info.st_dev, info.st_ino)
    if os.name == 'nt':
        kernel = getattr(ctypes, 'WinDLL')('kernel32', use_last_error=True)
        kernel.GetVolumePathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        kernel.GetVolumePathNameW.restype = ctypes.c_int
        kernel.GetVolumeInformationW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p, ctypes.c_uint32]
        kernel.GetVolumeInformationW.restype = ctypes.c_int
        volume_path = ctypes.create_unicode_buffer(32768)
        if not kernel.GetVolumePathNameW(str(root), volume_path, len(volume_path)):
            raise getattr(ctypes, 'WinError')(getattr(ctypes, 'get_last_error')())
        if os.path.normcase(str(Path(volume_path.value).resolve())) != os.path.normcase(str(root)):
            raise DriveUnavailable('Select a mounted drive root, not a subfolder.')
        serial = ctypes.c_uint32()
        filesystem = ctypes.create_unicode_buffer(256)
        if not kernel.GetVolumeInformationW(volume_path.value, None, 0, ctypes.byref(serial), None, None, filesystem, len(filesystem)):
            raise getattr(ctypes, 'WinError')(getattr(ctypes, 'get_last_error')())
        return VolumeIdentity(f'{volume_path.value}:{serial.value}', filesystem.value.lower(), info.st_dev, info.st_ino)
    raise DriveUnavailable('USB transfer supports Linux, Windows and macOS external mounted volumes.')


@dataclass(frozen=True)
class DriveTarget:
    root: Path
    identity: VolumeIdentity

    @classmethod
    def capture(cls, root: Path) -> 'DriveTarget':
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise DriveUnavailable('The selected drive is not available.')
        return cls(root, _mounted_identity(root))

    def check(self) -> None:
        try:
            if _mounted_identity(self.root) != self.identity:
                raise DriveUnavailable('The selected drive was disconnected or changed. Reconnect it and retry.')
        except OSError as exc:
            raise DriveUnavailable('The selected drive is unavailable or changed. Reconnect it and retry.') from exc


def allocation_unit(root: Path) -> int:
    if sys.platform == 'win32':
        kernel = getattr(ctypes, 'WinDLL')('kernel32', use_last_error=True)
        kernel.GetDiskFreeSpaceW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
        kernel.GetDiskFreeSpaceW.restype = ctypes.c_int
        sectors, sector_bytes = ctypes.c_uint32(), ctypes.c_uint32()
        if not kernel.GetDiskFreeSpaceW(str(root).rstrip('\\/') + '\\', ctypes.byref(sectors), ctypes.byref(sector_bytes), None, None):
            raise getattr(ctypes, 'WinError')(getattr(ctypes, 'get_last_error')())
        return max(4096, sectors.value * sector_bytes.value)
    info = os.statvfs(root)
    return max(4096, info.f_frsize, info.f_bsize)


def flush_file(descriptor: int) -> None:
    os.fsync(descriptor)
    if sys.platform == 'darwin':
        import fcntl
        # Ask the device to flush its hardware write cache, not just kernel data.
        fcntl.fcntl(descriptor, fcntl.F_FULLFSYNC)


def flush_directory(path: Path) -> None:
    if os.name != 'nt':
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    # Windows files are individually _commit/FlushFileBuffers'd by os.fsync.
    # Directory publication below uses MoveFileExW with WRITE_THROUGH, without
    # requiring administrator-only volume handles or external sync utilities.


def flush_volume(target: DriveTarget) -> None:
    target.check()
    if sys.platform.startswith('linux'):
        descriptor = os.open(target.root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(descriptor)
            libc = ctypes.CDLL(None, use_errno=True)
            syncfs = getattr(libc, 'syncfs', None)
            if syncfs is not None:
                syncfs.argtypes, syncfs.restype = [ctypes.c_int], ctypes.c_int
                if syncfs(descriptor) != 0:
                    code = ctypes.get_errno()
                    raise OSError(code, os.strerror(code))
        finally:
            os.close(descriptor)
    target.check()


def publish_without_replace(source: Path, destination: Path) -> None:
    """Atomic, same-volume publish which never replaces an existing destination."""
    if os.name == 'nt':
        _move_windows(source, destination, 0x8)
        return
    if sys.platform.startswith('linux'):
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, 'renameat2', None)
        if rename is None:
            raise OSError('This platform cannot publish without overwrite protection; staging data was retained.')
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:  # RENAME_NOREPLACE
            code = ctypes.get_errno()
            if code == errno.EEXIST:
                raise FileExistsError(code, 'Destination already exists; it was not overwritten', str(destination))
            raise OSError(code, os.strerror(code), str(destination))
        return
    if sys.platform == 'darwin':
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renamex_np
        rename.argtypes, rename.restype = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint], ctypes.c_int
        if rename(os.fsencode(source), os.fsencode(destination), 0x4) != 0:  # RENAME_EXCL
            code = ctypes.get_errno()
            if code == errno.EEXIST:
                raise FileExistsError(code, 'Destination already exists; it was not overwritten', str(destination))
            raise OSError(code, os.strerror(code), str(destination))
        return
    raise OSError('Safe USB publication is supported on Linux, Windows and macOS only.')


def _move_windows(source: Path, destination: Path, flags: int) -> None:
    kernel = getattr(ctypes, 'WinDLL')('kernel32', use_last_error=True)
    move = kernel.MoveFileExW
    move.argtypes, move.restype = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32], ctypes.c_int
    def extended(path: Path) -> str:
        value = str(path.absolute())
        if value.startswith('\\\\?\\'):
            return value
        return '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value
    if not move(extended(source), extended(destination), flags):
        raise getattr(ctypes, 'WinError')(getattr(ctypes, 'get_last_error')())


def replace_private(source: Path, destination: Path) -> None:
    """Replace only a file in the caller's private, validated staging session."""
    if os.name == 'nt':
        _move_windows(source, destination, 0x9)  # REPLACE_EXISTING | WRITE_THROUGH
    else:
        os.replace(source, destination)
