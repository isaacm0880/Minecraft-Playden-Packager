"""Resumable, checksum-verified package transfer into a mounted Playden USB drive."""
import hashlib
import errno
import json
import os
import shutil
import stat
import sys
import uuid
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from .downloads import safe_path
from .operations import CancellationToken, OperationCancelled
from .progress import Progress, ProgressCallback
from .validator import validate
from .volumes import DriveTarget, allocation_unit, flush_file, flush_directory, flush_volume, publish_without_replace, replace_private

CHUNK_SIZE = 1024 * 1024


class TransferError(OSError):
    pass


class InsufficientSpace(TransferError):
    def __init__(self, required: int):
        self.required = required
        super().__init__(f'Insufficient USB space: need approximately {required:,} bytes.')


@dataclass(frozen=True)
class FileRecord:
    relative: str
    size: int
    sha256: str
    fingerprint: tuple[int, int, int, int]


@dataclass(frozen=True)
class TransferResult:
    source: Path
    destination: Path
    files: int
    bytes_copied: int
    verified: bool = True
    checksums: tuple[tuple[str, int, str], ...] = ()


def _linked(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))


def _portable_name(name: str) -> None:
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {f'{prefix}{n}' for prefix in ('COM', 'LPT') for n in range(1, 10)}
    if not name or name in ('.', '..') or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name) or name.endswith((' ', '.')) or name.split('.')[0].upper() in reserved:
        raise TransferError(f'Name is not portable to Windows/USB filesystems: {name!r}')


def _fingerprint(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _tree(root: Path) -> tuple[list[str], list[str]]:
    directories: list[str] = []
    files: list[str] = []
    folded: set[str] = set()
    def failure(error: OSError) -> None:
        raise error
    for directory, child_dirs, child_files in os.walk(root, followlinks=False, onerror=failure):
        for name in sorted(child_dirs + child_files):
            _portable_name(name)
            path = Path(directory) / name
            if _linked(path):
                raise TransferError(f'Symlinks/junctions are not allowed in a transfer: {path}')
            relative = path.relative_to(root).as_posix()
            if relative.casefold() in folded:
                raise TransferError(f'Case-colliding path is not portable: {relative}')
            folded.add(relative.casefold())
            if path.is_dir():
                directories.append(relative)
            elif stat.S_ISREG(path.stat().st_mode):
                files.append(relative)
            else:
                raise TransferError(f'Cannot transfer a non-regular file: {path}')
    return sorted(directories), sorted(files)


def _checksum(path: Path, cancellation: CancellationToken, guard: DriveTarget | None = None,
              advance=None) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            cancellation.check()
            if guard is not None:
                guard.check()
            block = stream.read(CHUNK_SIZE)
            if not block:
                break
            result.update(block)
            if advance is not None:
                advance(len(block))
    return result.hexdigest()


def _check_private_root(path: Path, drive: DriveTarget) -> None:
    drive.check()
    try:
        parts = path.absolute().relative_to(drive.root).parts
    except ValueError:
        raise TransferError('Transfer path escaped the selected volume') from None
    current = drive.root
    for part in parts:
        current /= part
        if _linked(current) or not current.is_dir():
            raise TransferError(f'Unsafe transfer directory: {current}')


def _directory(root: Path, relative: str, drive: DriveTarget) -> Path:
    _check_private_root(root, drive)
    path = root
    for component in Path(relative).parts:
        drive.check()
        path = path / component
        if path.exists() or path.is_symlink():
            if _linked(path) or not path.is_dir():
                raise TransferError(f'Unsafe or non-directory destination component: {path}')
        else:
            try:
                path.mkdir()
            except FileExistsError:
                # A concurrent transfer may create this shared component after
                # the exists check. Validate it below, never trust EEXIST.
                pass
            flush_directory(path.parent)
        _check_private_root(path, drive)
    return path


def _session_parent(drive: DriveTarget) -> Path:
    for _ in range(3):
        try:
            return _directory(drive.root, 'playden/.packager-transfers', drive)
        except FileNotFoundError:
            # Another successful transfer can remove this empty parent while
            # it is being opened/validated. Recheck the selected volume first.
            drive.check()
    raise TransferError('USB temporary parent changed repeatedly; retry with a stable destination')


def _state(path: Path, data: dict, drive: DriveTarget) -> None:
    _check_private_root(path.parent, drive)
    temporary = path.parent / (uuid.uuid4().hex + '.state.tmp')
    identity = None
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            info = os.fstat(stream.fileno())
            identity = info.st_dev, info.st_ino
            json.dump(data, stream, sort_keys=True)
            stream.flush()
            flush_file(stream.fileno())
        replace_private(temporary, path)  # Only the transfer's own state file is replaced.
        identity = None  # The temporary name no longer belongs to this call.
        flush_directory(path.parent)
    finally:
        # A failed checkpoint write is not resumable data. Remove only this
        # call's exclusive file, never another session's checkpoint or payload.
        if identity is not None:
            try:
                _check_private_root(path.parent, drive)
                info = temporary.lstat()
                if identity == (info.st_dev, info.st_ino) and stat.S_ISREG(info.st_mode):
                    temporary.unlink()
            except OSError:
                pass


class _SessionLease:
    """Nonblocking cross-process ownership of one resumable USB session."""

    def __init__(self, path: Path, descriptor: int):
        self.path, self.descriptor = path, descriptor
        info = os.fstat(descriptor)
        self.identity = info.st_dev, info.st_ino

    @staticmethod
    def _lock(descriptor: int, *, release: bool = False) -> None:
        if sys.platform == 'win32':
            import msvcrt
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK if release else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_UN if release else fcntl.LOCK_EX | fcntl.LOCK_NB)

    @classmethod
    def acquire(cls, staging: Path, drive: DriveTarget) -> '_SessionLease | None':
        _check_private_root(staging, drive)
        path = staging / '.session-lock'
        if path.exists() or path.is_symlink():
            if _linked(path) or not stat.S_ISREG(path.lstat().st_mode):
                raise TransferError('Unsafe transfer session lock')
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
                             | getattr(os, 'O_BINARY', 0), 0o600)
        try:
            info = os.fstat(descriptor)
            current = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1
                    or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino) or _linked(path)):
                raise TransferError('Unsafe or changed transfer session lock')
            if info.st_size == 0:
                os.write(descriptor, b'\0')  # Windows byte-range locks need a byte.
            try:
                cls._lock(descriptor)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    os.close(descriptor)
                    return None
                raise
            return cls(path, descriptor)
        except BaseException:
            os.close(descriptor)
            raise

    def matches(self) -> bool:
        if self.descriptor < 0:
            return False
        info = self.path.lstat()
        return stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == self.identity

    def close(self) -> None:
        if self.descriptor >= 0:
            descriptor, self.descriptor = self.descriptor, -1
            try:
                self._lock(descriptor, release=True)
            except OSError:
                pass  # Closing the descriptor also releases its advisory lock.
            finally:
                os.close(descriptor)

    def discard(self) -> None:
        if not self.matches():
            raise TransferError('Transfer session lock changed; cleanup skipped')
        # The checkpoint is already gone. A new resumer must re-read it after
        # obtaining a lease, so it cannot adopt this completed session now.
        self.close()
        info = self.path.lstat()
        if (info.st_dev, info.st_ino) != self.identity:
            raise TransferError('Transfer session lock changed; cleanup skipped')
        self.path.unlink()


def _cleanup_completed(staging: Path, state: dict, identity: tuple[int, int], drive: DriveTarget,
                       lease: _SessionLease | None = None) -> str:
    """After durable publication, unlink our checkpoint and rmdir empty parents.

    Interrupted transfers deliberately retain their checkpoint/payload for retry.
    No cleanup traverses or recursively removes a shared/session directory.
    """
    try:
        _check_private_root(staging, drive)
        info = staging.stat()
        if (info.st_dev, info.st_ino) != identity:
            raise TransferError('Completed transfer session changed; cleanup skipped')
        if lease is None or lease.path.parent != staging or not lease.matches():
            raise TransferError('Completed transfer session ownership is not established; cleanup skipped')
        parent_info = staging.parent.stat()
        parent_identity = parent_info.st_dev, parent_info.st_ino
        checkpoint = staging / 'state.json'
        before = checkpoint.lstat()
        if not stat.S_ISREG(before.st_mode) or _linked(checkpoint):
            raise TransferError('Completed transfer checkpoint is not a regular file; cleanup skipped')
        if json.loads(checkpoint.read_text(encoding='utf-8')) != state or state.get('status') != 'complete':
            raise TransferError('Completed transfer checkpoint changed; cleanup skipped')
        drive.check()
        after = checkpoint.lstat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise TransferError('Completed transfer checkpoint changed while reading; cleanup skipped')
        checkpoint.unlink()
        flush_directory(staging)
        lease.discard()
        try:
            staging.rmdir()
        except OSError as exc:
            if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                return 'Completed checkpoint removed; unrelated session files retained.'
            raise
        sessions = staging.parent
        _check_private_root(sessions, drive)
        parent_info = sessions.stat()
        if (parent_info.st_dev, parent_info.st_ino) != parent_identity:
            raise TransferError('Transfer parent changed; empty-parent cleanup skipped')
        flush_directory(sessions)
        try:
            sessions.rmdir()
        except OSError as exc:
            if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                return 'Completed session removed; other transfer sessions retained.'
            raise
        flush_directory(sessions.parent)
        return 'Completed transfer session and empty temporary parent removed.'
    except (OSError, ValueError) as exc:
        # Publication/verification has already succeeded. Keep that result even
        # when a disconnected drive or permissions prevent metadata cleanup.
        return f'Package published successfully; temporary cleanup incomplete: {exc}'


@dataclass(frozen=True)
class TransferRequest:
    source: Path
    drive: DriveTarget
    verify_after_copy: bool = True
    destination_name: str | None = None

    @property
    def package_name(self) -> str:
        return self.destination_name if self.destination_name is not None else self.source.name

    @property
    def destination(self) -> Path:
        return self.drive.root / 'playden/import' / self.package_name

    def run(self, progress: ProgressCallback | None = None,
            cancellation: CancellationToken | None = None) -> TransferResult:
        token = cancellation if cancellation is not None else CancellationToken()
        try:
            with ExitStack() as leases:
                return self._run(progress, token, leases)
        except InsufficientSpace:
            raise
        except OperationCancelled:
            raise OperationCancelled('USB transfer cancelled. No success was reported. Retry Copy to USB to reuse verified staging files.') from None
        except (OSError, ValueError) as exc:
            raise TransferError(f'USB transfer failed: {exc}. Incomplete staging data is retained under playden/.packager-transfers for retry; no unrelated files were deleted.') from exc

    def _run(self, progress: ProgressCallback | None, token: CancellationToken, leases: ExitStack) -> TransferResult:
        started = time.monotonic()
        token.check()
        self.drive.check()
        source = self.source.resolve(strict=True)
        _portable_name(self.source.name)
        _portable_name(self.package_name)
        destination = self.destination
        if destination.resolve().is_relative_to(source) or source.is_relative_to(destination.resolve()):
            raise TransferError('Source and USB destination must not overlap.')
        def report(stage: str, message: str, completed: int = 0, total: int | None = None,
                   relative: str = '', copied: int | None = None, size: int | None = None) -> None:
            if progress is not None:
                progress(Progress(stage, message, completed, total, relative, copied, size))
        report('transfer-details', f'Local package: {source.name}\nPS5 destination: {self.package_name}\nStorage: {self.drive.root}\nTransfer method: USB copy\nDestination path: {destination}')
        report('transfer-prepare', 'Validating the source package and preparing SHA-256 checksums...')
        errors = validate(source, cancellation=token)
        if errors:
            raise TransferError('Source package validation failed: ' + '\n'.join(errors))
        directories, files = _tree(source)
        records = []
        for relative in files:
            token.check()
            path = safe_path(source, relative)
            before = _fingerprint(path)
            checksum = _checksum(path, token)
            if before != _fingerprint(path):
                raise TransferError(f'Source changed while hashing: {relative}')
            records.append(FileRecord(relative, before[2], checksum, before))
            report('transfer-prepare', 'Preparing SHA-256 checksums...', len(records), len(files), relative)
        total = sum(record.size for record in records)
        content = {'name': self.package_name, 'directories': directories,
                   'files': [(r.relative, r.size, r.sha256) for r in records]}
        content_id = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
        # Never traverse drive-side symlinks, including an existing playden folder.
        import_root = _directory(self.drive.root, 'playden/import', self.drive)
        sessions = _session_parent(self.drive)
        state = None
        staging = None
        session_lease = None
        try:
            candidates = sorted(sessions.glob(content_id[:24] + '-*'))
        except FileNotFoundError:
            candidates = []  # Only an empty parent could have been removed.
        for candidate in candidates:
            try:
                if _linked(candidate) or not candidate.is_dir():
                    continue
                state_path = candidate / 'state.json'
                if not state_path.is_file() or _linked(state_path):
                    continue
                candidate_state = json.loads(state_path.read_text(encoding='utf-8'))
                if (isinstance(candidate_state, dict) and candidate_state.get('schema') == 1 and candidate_state.get('content_id') == content_id
                        and candidate_state.get('package_name') == self.package_name
                        and candidate.name == content_id[:24] + '-' + candidate_state.get('nonce', '')):
                    candidate_lease = _SessionLease.acquire(candidate, self.drive)
                    if candidate_lease is None:
                        continue  # Another process owns this resumable session.
                    try:
                        # Never adopt data read before acquiring the session.
                        if json.loads(state_path.read_text(encoding='utf-8')) != candidate_state:
                            candidate_lease.close()
                            continue
                    except BaseException:
                        candidate_lease.close()
                        raise
                    leases.callback(candidate_lease.close)
                    staging, state, session_lease = candidate, candidate_state, candidate_lease
                    break
            except (OSError, ValueError, TypeError):
                continue
        payload: Path
        published = False
        if destination.exists() or destination.is_symlink():
            # Recover only our own already-published session after a post-rename
            # flush/disconnection failure. Never overwrite a published directory.
            if staging is None or state is None or (staging / 'payload').exists() or state.get('status') not in ('verified', 'copied', 'complete') or _linked(destination):
                raise FileExistsError(f'Destination already exists and was not overwritten: {destination}')
            payload, published = destination, True
        else:
            payload = staging / 'payload' if staging else Path()
        if staging is None:
            cluster = allocation_unit(self.drive.root)
            required = sum(((r.size + cluster - 1) // cluster) * cluster for r in records) + cluster * (len(directories) + 6) + 5 * 1024 * 1024
            if shutil.disk_usage(self.drive.root).free < required:
                raise InsufficientSpace(required)
            if self.drive.identity.filesystem.lower() in ('vfat', 'fat', 'fat32') and any(r.size >= 4 * 1024**3 for r in records):
                raise TransferError('A file exceeds the FAT32 size limit. Use an appropriate exFAT drive.')
            nonce = uuid.uuid4().hex
            for _ in range(3):
                try:
                    sessions = _session_parent(self.drive)
                    staging = sessions / (content_id[:24] + '-' + nonce)
                    _check_private_root(sessions, self.drive)
                    staging.mkdir()
                    break
                except FileNotFoundError:
                    self.drive.check()
            else:
                raise TransferError('USB temporary parent changed repeatedly before session creation')
            session_lease = _SessionLease.acquire(staging, self.drive)
            if session_lease is None:
                raise TransferError('The new USB transfer session is unexpectedly in use')
            leases.callback(session_lease.close)
            state = {'schema': 1, 'content_id': content_id, 'package_name': self.package_name, 'nonce': nonce, 'status': 'copying'}
            try:
                _state(staging / 'state.json', state, self.drive)
                payload = _directory(staging, 'payload', self.drive)
            except OSError:
                # Before a checkpoint/payload exists there is nothing resumable.
                # rmdir refuses any contents introduced by another operation.
                try:
                    _check_private_root(staging, self.drive)
                    if not (staging / 'state.json').exists():
                        session_lease.discard()
                    staging.rmdir()
                    sessions.rmdir()
                except OSError:
                    pass
                raise
        assert state is not None
        info = staging.stat()
        staging_identity = info.st_dev, info.st_ino
        # Only a temporary file explicitly recorded by this private session may
        # be cleaned after a crash/disconnection. Unknown files are never removed.
        active_temp = state.get('active_temp')
        if active_temp is not None:
            try:
                if not isinstance(active_temp, str) or not active_temp.endswith('.part') or uuid.UUID(active_temp[:-5]).hex != active_temp[:-5]:
                    raise ValueError
            except ValueError:
                raise TransferError('Unrecognized transfer checkpoint temporary file; staging was left untouched.') from None
            _check_private_root(staging, self.drive)
            leftover = staging / active_temp
            if leftover.exists() or leftover.is_symlink():
                if _linked(leftover) or not leftover.is_file():
                    raise TransferError('Unsafe checkpoint temporary file; it was not removed.')
                leftover.unlink()
                flush_directory(staging)
        if not published and not payload.exists():
            payload = _directory(staging, 'payload', self.drive)
        if _linked(payload) or not payload.is_dir():
            raise TransferError(f'Unsafe transfer directory: {payload}')
        token.check()
        self.drive.check()
        # Reject unplanned files before resuming; no cleanup ever walks arbitrary
        # directories or deletes unknown contents.
        if payload.exists():
            actual_dirs, actual_files = _tree(payload)
            if not set(actual_dirs).issubset(directories) or not set(actual_files).issubset(files):
                raise TransferError(f'Transfer area contains unrelated files; it was left untouched: {payload}')
        if not published:
            for relative in directories:
                token.check()
                _directory(payload, relative, self.drive)
            remaining = 0
            reusable: set[str] = set()
            for record in records:
                token.check()
                path = safe_path(payload, record.relative)
                if path.is_file() and not _linked(path) and path.stat().st_size == record.size and _checksum(path, token, self.drive) == record.sha256:
                    reusable.add(record.relative)
                else:
                    remaining += record.size
            cluster = allocation_unit(self.drive.root)
            needed = remaining + cluster * (len(records) - len(reusable) + 4) + 1024 * 1024
            if shutil.disk_usage(self.drive.root).free < needed:
                raise InsufficientSpace(needed)
            copied = 0
            for record in records:
                token.check()
                self.drive.check()
                target = safe_path(payload, record.relative)
                if record.relative in reusable:
                    copied += record.size
                    report('transfer-copy', 'Reusing checksum-verified files...', copied, total, record.relative, copied, total)
                    continue
                if _fingerprint(safe_path(source, record.relative)) != record.fingerprint:
                    raise TransferError(f'Source changed before copying: {record.relative}')
                _check_private_root(staging, self.drive)
                _check_private_root(target.parent, self.drive)
                temporary = staging / (uuid.uuid4().hex + '.part')
                state['active_temp'] = temporary.name
                _state(staging / 'state.json', state, self.drive)
                copy_hash = hashlib.sha256()
                try:
                    with safe_path(source, record.relative).open('rb') as src, temporary.open('xb') as dst:
                        report('transfer-copy', 'Copying to USB...', copied, total, record.relative, copied, total)
                        while True:
                            token.check()
                            self.drive.check()
                            block = src.read(CHUNK_SIZE)
                            if not block:
                                break
                            dst.write(block)
                            copy_hash.update(block)
                            copied += len(block)
                            report('transfer-copy', 'Copying to USB...', copied, total, record.relative, copied, total)
                        dst.flush()
                        flush_file(dst.fileno())
                    if copy_hash.hexdigest() != record.sha256 or _fingerprint(safe_path(source, record.relative)) != record.fingerprint:
                        raise TransferError(f'Source changed during transfer: {record.relative}')
                    self.drive.check()
                    # Replacing a failed file is allowed only inside this private,
                    # marker-validated session, never in the final import folder.
                    if target.exists() and (_linked(target) or not target.is_file()):
                        raise TransferError(f'Unsafe staging target: {target}')
                    replace_private(temporary, target)
                    flush_directory(target.parent)
                finally:
                    try:
                        self.drive.check()
                        temporary.unlink(missing_ok=True)  # Only this call's exclusive temporary file.
                    except OSError:
                        pass
        token.check()
        report('transfer-flush', 'Flushing USB writes...', copied=total, size=total)
        flush_volume(self.drive)
        verified = 0
        def advance(count: int, relative: str) -> None:
            nonlocal verified
            verified += count
            report('transfer-verify', 'Verifying every transferred file with SHA-256...', verified, total, relative, total, total)
        for record in records:
            token.check()
            target = safe_path(payload, record.relative)
            if not target.is_file() or _linked(target) or target.stat().st_size != record.size:
                raise TransferError(f'Transferred file is missing or incomplete: {record.relative}')
            if self.verify_after_copy:
                report('transfer-verify', 'Verifying every transferred file with SHA-256...', verified, total, record.relative, total, total)
                if _checksum(target, token, self.drive, lambda n, rel=record.relative: advance(n, rel)) != record.sha256:
                    raise TransferError(f'SHA-256 verification failed: {record.relative}')
        actual_dirs, actual_files = _tree(payload)
        if actual_dirs != directories or actual_files != files:
            raise TransferError('Transferred directory/file list differs from the source package.')
        errors = validate(payload, cancellation=token)
        if errors:
            raise TransferError('Transferred package validation failed: ' + '\n'.join(errors))
        if _tree(source) != (directories, files) or any(_fingerprint(safe_path(source, r.relative)) != r.fingerprint for r in records):
            raise TransferError('Source changed during the transfer. Retry with a stable source package.')
        token.check()
        report('transfer-flush', 'Finalizing USB transfer...', copied=total, size=total)
        if not published:
            state['status'] = 'verified' if self.verify_after_copy else 'copied'
            _state(staging / 'state.json', state, self.drive)
            flush_volume(self.drive)
            token.check()
            self.drive.check()
            publish_without_replace(payload, destination)
            # Commit point: finish flushing even if cancellation arrives now.
            flush_directory(import_root)
        state['status'] = 'complete'
        _state(staging / 'state.json', state, self.drive)
        flush_volume(self.drive)
        report('transfer-cleanup', _cleanup_completed(staging, state, staging_identity, self.drive, session_lease))
        elapsed = max(0., time.monotonic() - started)
        average = total / elapsed if elapsed > 0 else 0.
        verification = 'SHA-256 verified' if self.verify_after_copy else 'post-copy verification skipped'
        report('transfer-statistics', f'Bytes transferred: {total}\nTransfer duration: {elapsed:.1f}s\nAverage transfer speed: {average / 1048576:.1f} MiB/s\nVerification: {verification}')
        report('transfer-complete', ('USB copy verified and writes flushed. Safely eject the drive before unplugging.' if self.verify_after_copy else 'USB copy complete; post-copy checksum verification skipped. Writes flushed. Safely eject the drive.'), total, total, copied=total, size=total)
        return TransferResult(source, destination, len(records), total, self.verify_after_copy,
                              tuple((r.relative, r.size, r.sha256) for r in records) if self.verify_after_copy else ())
