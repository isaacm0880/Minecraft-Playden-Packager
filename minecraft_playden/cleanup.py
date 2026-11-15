"""Delete only session-owned generated outputs with a confirmed verified USB copy."""
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from .downloads import digest, safe_path
from .operations import CancellationToken
from .progress import Progress, ProgressCallback
from .transfer import TransferRequest, TransferResult
from .volumes import publish_without_replace

Fingerprint = tuple[int, int, int, int, int]


def linked(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) &
                                           getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))


def plain_directory(path: Path) -> None:
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Cleanup requires an absolute path without traversal.')
    for ancestor in (*reversed(path.parents), path):
        info = ancestor.lstat()
        if linked(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f'Cleanup refuses linked or non-directory paths: {ancestor}')


def identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    return info.st_dev, info.st_ino


def snapshot(root: Path) -> dict[str, Fingerprint]:
    result = {}
    def add(path: Path) -> None:
        info = path.lstat()
        if linked(info) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError(f'Cleanup refuses links and special files: {path}')
        result[path.relative_to(root).as_posix()] = (info.st_mode, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    def failure(error: OSError) -> None:
        raise error
    add(root)
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=failure):
        for name in dirs + files:
            add(Path(directory) / name)
    return result


@dataclass(frozen=True)
class GeneratedOutput:
    path: Path
    parent: Path
    parent_identity: tuple[int, int]
    entries: dict[str, Fingerprint]
    protected: tuple[Path, ...]

    @classmethod
    def capture(cls, path: Path, protected: tuple[Path, ...]) -> 'GeneratedOutput':
        """Called only after the shared exporter published a successful build.

        No marker files or package contents are changed. Restarted/validated
        existing packages deliberately have no ownership receipt.
        """
        path = path.absolute()
        plain_directory(path)
        receipt = cls(path, path.parent, identity(path.parent), snapshot(path),
                      tuple(root.resolve() for root in protected))
        receipt.check()
        return receipt

    def check(self, extra_protected: tuple[Path, ...] = ()) -> None:
        plain_directory(self.path)
        if self.path.parent != self.parent or self.path == self.parent or identity(self.parent) != self.parent_identity:
            raise ValueError('Generated-output parent changed; cleanup refused.')
        for root in (*self.protected, *extra_protected):
            root = root.resolve()
            if self.path.is_relative_to(root) or root.is_relative_to(self.path):
                raise ValueError('Cleanup must never overlap an original Minecraft source or library directory.')
        if snapshot(self.path) != self.entries:
            raise ValueError('Generated output changed since publication; local files were retained.')

    def eligible(self, request: TransferRequest, result: TransferResult,
                 protected: tuple[Path, ...] = ()) -> None:
        if not request.verify_after_copy or not result.verified or not result.checksums:
            raise ValueError('Cleanup requires successful checksum verification of every USB file.')
        if request.source.absolute() != self.path or result.source != self.path or result.destination != request.destination:
            raise ValueError('Verified transfer does not match this generated package.')
        drive = request.drive.root.resolve()
        if self.path.is_relative_to(drive) or drive.is_relative_to(self.path):
            raise ValueError('Cleanup must not overlap the USB volume.')
        self.check(protected)
        expected = {name for name, info in self.entries.items() if stat.S_ISREG(info[0])}
        names = [name for name, size, checksum in result.checksums]
        if (len(names) != len(set(names)) or set(names) != expected or result.files != len(names)
                or result.bytes_copied != sum(size for name, size, checksum in result.checksums)):
            raise ValueError('Incomplete verified-file receipt; local files were retained.')

    def remove_verified(self, request: TransferRequest, result: TransferResult, *, confirmed: bool = False,
                        protected: tuple[Path, ...] = (), progress: ProgressCallback | None = None,
                        cancellation: CancellationToken | None = None) -> Path:
        if not confirmed:
            raise ValueError('Explicit confirmation of this local package path is required.')
        token = cancellation or CancellationToken()
        token.check()
        self.eligible(request, result, protected)
        request.drive.check()
        plain_directory(result.destination)
        usb_before = snapshot(result.destination)
        for number, (relative, size, checksum) in enumerate(result.checksums, 1):
            token.check()
            request.drive.check()
            for root in (self.path, result.destination):
                file = safe_path(root, relative)
                if file.stat().st_size != size or digest(file, 'sha256', token) != checksum:
                    raise ValueError('Local/USB content no longer matches the verified copy; cleanup refused.')
            if progress is not None:
                progress(Progress('cleanup-check', 'Checking local and verified USB files before cleanup…',
                                  number, len(result.checksums), relative))
        if snapshot(result.destination) != usb_before:
            raise ValueError('USB copy changed during cleanup checks; local package retained.')
        self.eligible(request, result, protected)
        request.drive.check()
        token.check()
        # Commit point: quarantine only the exact owned directory, never its parent.
        # No cancellation once deletion starts. rmtree does not follow symlinks;
        # the extra snapshot rejects root replacement and newly inserted files.
        quarantine = self.parent / ('.playden-cleanup-' + uuid.uuid4().hex)
        publish_without_replace(self.path, quarantine)
        try:
            plain_directory(quarantine)
            if identity(self.parent) != self.parent_identity or snapshot(quarantine) != self.entries:
                raise ValueError('Generated output changed before deletion; cleanup refused.')
            shutil.rmtree(quarantine)
        except Exception:
            # A failed removal may be partial; restore remaining files when possible
            # without ever replacing a newly created path. USB copy is untouched.
            try:
                plain_directory(quarantine)
                original_identity = self.entries['.'][1:3]
                if (identity(self.parent) == self.parent_identity and identity(quarantine) == original_identity
                        and not self.path.exists() and not self.path.is_symlink()):
                    publish_without_replace(quarantine, self.path)
            except (OSError, ValueError):
                # Never restore through a replaced/linked parent or move an
                # unrecognised quarantine. Leave it for explicit inspection.
                pass
            raise
        return self.path
