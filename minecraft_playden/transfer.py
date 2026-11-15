"""Resumable, checksum-verified package transfer into a mounted Playden USB drive."""
import hashlib
import json
import os
import shutil
import stat
import uuid
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
            path.mkdir()
            flush_directory(path.parent)
    return path


def _state(path: Path, data: dict, drive: DriveTarget) -> None:
    _check_private_root(path.parent, drive)
    temporary = path.parent / (uuid.uuid4().hex + '.state.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, sort_keys=True)
        stream.flush()
        flush_file(stream.fileno())
    replace_private(temporary, path)  # Only the transfer's own state file is replaced.
    flush_directory(path.parent)


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
            return self._run(progress, token)
        except InsufficientSpace:
            raise
        except OperationCancelled:
            raise OperationCancelled('USB transfer cancelled. No success was reported. Retry Copy to USB to reuse verified staging files.') from None
        except (OSError, ValueError) as exc:
            raise TransferError(f'USB transfer failed: {exc}. Incomplete staging data is retained under playden/.packager-transfers for retry; no unrelated files were deleted.') from exc

    def _run(self, progress: ProgressCallback | None, token: CancellationToken) -> TransferResult:
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
        sessions = _directory(self.drive.root, 'playden/.packager-transfers', self.drive)
        state = None
        staging = None
        for candidate in sorted(sessions.glob(content_id[:24] + '-*')):
            if _linked(candidate) or not candidate.is_dir():
                continue
            state_path = candidate / 'state.json'
            if not state_path.is_file() or _linked(state_path):
                continue
            try:
                candidate_state = json.loads(state_path.read_text(encoding='utf-8'))
                if (isinstance(candidate_state, dict) and candidate_state.get('schema') == 1 and candidate_state.get('content_id') == content_id
                        and candidate_state.get('package_name') == self.package_name
                        and candidate.name == content_id[:24] + '-' + candidate_state.get('nonce', '')):
                    staging, state = candidate, candidate_state
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
            staging = sessions / (content_id[:24] + '-' + nonce)
            staging.mkdir()
            state = {'schema': 1, 'content_id': content_id, 'package_name': self.package_name, 'nonce': nonce, 'status': 'copying'}
            _state(staging / 'state.json', state, self.drive)
            payload = _directory(staging, 'payload', self.drive)
        assert state is not None
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
        report('transfer-complete', ('USB copy verified and writes flushed. Safely eject the drive before unplugging.' if self.verify_after_copy else 'USB copy complete; post-copy checksum verification skipped. Writes flushed. Safely eject the drive.'), total, total, copied=total, size=total)
        return TransferResult(source, destination, len(records), total, self.verify_after_copy,
                              tuple((r.relative, r.size, r.sha256) for r in records) if self.verify_after_copy else ())
