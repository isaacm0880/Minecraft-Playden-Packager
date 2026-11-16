"""Read-only package streaming to the paired PS5 import receiver."""
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import time
from dataclasses import dataclass, field
import struct
import sys
from typing import BinaryIO

from .cleanup import linked, plain_directory, snapshot
from .operations import CancellationToken
from .progress import Progress, ProgressCallback
from .network_metrics import NetworkMetrics, MetricsCallback, decode_receiver_metrics
from .validator import validate
from .network_protocol import (
    ACK, BEGIN, BEGIN_INFO, COMMIT, DATA, END_FILE, ENTRY, ENTRY_INFO, GET_METRICS, VERIFY, VERIFY_PROGRESS,
    MAX_ENTRIES, MAX_FILE, MAX_FRAME, MAX_TOTAL, PORT, ROOTS,
    NetworkTransferError, Session, configure_socket, lan_address, pairing_key, safe_relative,
)


def _windows_handle_identity(fd: int) -> tuple[int, int, int]:
    """Read the opened inode and timestamp without CRT fstat/path differences."""
    import ctypes
    from ctypes import wintypes
    class FileIdInfo(ctypes.Structure):
        _fields_ = [('volume', ctypes.c_ulonglong), ('identifier', ctypes.c_ubyte * 16)]
    class BasicInfo(ctypes.Structure):
        _fields_ = [('creation', ctypes.c_longlong), ('access', ctypes.c_longlong),
                    ('write', ctypes.c_longlong), ('change', ctypes.c_longlong),
                    ('attributes', wintypes.DWORD)]
    native = getattr(__import__('msvcrt'), 'get_osfhandle')(fd)
    kernel = getattr(ctypes, 'WinDLL')('kernel32', use_last_error=True)
    get_info = kernel.GetFileInformationByHandleEx
    get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    get_info.restype = wintypes.BOOL
    identity, basic = FileIdInfo(), BasicInfo()
    if not get_info(native, 18, ctypes.byref(identity), ctypes.sizeof(identity)) or not get_info(native, 0, ctypes.byref(basic), ctypes.sizeof(basic)):
        raise getattr(ctypes, 'WinError')(getattr(ctypes, 'get_last_error')())
    inode = int.from_bytes(bytes(identity.identifier), 'little')
    device = int(identity.volume)
    # CPython 3.11 exposes the legacy 64-bit file index and 32-bit volume ID.
    # Since 3.12 stat/lstat expose the full FileIdInfo values.
    if sys.version_info < (3, 12):
        inode &= (1 << 64) - 1
        device &= (1 << 32) - 1
    modified_ns = (int(basic.write) - 116444736000000000) * 100
    return device, inode, modified_ns


def _open_local_file(source: Path, relative: str, expected: tuple[int, int, int, int, int]) -> BinaryIO:
    path = source / relative
    plain_directory(path.parent)
    if linked(path.lstat()):
        raise ValueError('Source became a link; package not transferred.')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    handle = os.fdopen(os.open(path, flags), 'rb')
    try:
        info = os.fstat(handle.fileno())
        actual = (info.st_mode, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if os.name == 'nt':
            device, inode, modified = _windows_handle_identity(handle.fileno())
            # CRT fstat also omits filename-dependent execute bits on Windows.
            actual = (expected[0], device, inode, info.st_size, modified)
        if not stat.S_ISREG(info.st_mode) or actual != expected or linked(path.lstat()):
            raise ValueError('Source changed before opening; package not transferred.')
        return handle
    except BaseException:
        handle.close()
        raise


@dataclass(frozen=True)
class NetworkResult:
    source: Path
    destination: str
    files: int
    bytes_copied: int
    receiver_metrics: dict[str, int | float] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class NetworkRequest:
    source: Path
    host: str
    pairing_token: str = field(repr=False)
    destination: str = 'internal'
    port: int = PORT
    package_name: str | None = None
    socket_buffer: int | None = None
    chunk_size: int = MAX_FRAME

    def run(self, progress: ProgressCallback | None = None,
            cancellation: CancellationToken | None = None, *,
            diagnostics: MetricsCallback | None = None) -> NetworkResult:
        metrics = NetworkMetrics(diagnostics is not None)
        started = metrics.tick()
        try:
            result = self._run(progress, cancellation, metrics)
            metrics.add('published')
            return result
        finally:
            metrics.elapsed('wall_s', started)
            if diagnostics is not None:
                diagnostics(dict(metrics.values))

    def _run(self, progress: ProgressCallback | None, cancellation: CancellationToken | None,
             metrics: NetworkMetrics) -> NetworkResult:
        if not 4096 <= self.chunk_size <= MAX_FRAME:
            raise ValueError('Chunk size must be between 4096 and 65536 bytes.')
        if self.socket_buffer is not None and not 4096 <= self.socket_buffer <= 4 * 1024 * 1024:
            raise ValueError('Socket buffer must be between 4096 and 4194304 bytes.')
        validation_started = metrics.tick()
        token = cancellation if cancellation is not None else CancellationToken()
        host, key = lan_address(self.host.strip()), pairing_key(self.pairing_token)
        if self.destination not in ROOTS or not 1 <= self.port <= 65535:
            raise ValueError('Select internal or usb0 and a valid TCP port.')
        source = self.source.absolute()
        plain_directory(source)
        source = source.resolve(strict=True)
        token.check()
        def report(stage: str, message: str, done: int = 0, total: int | None = None, path: str = '') -> None:
            if progress is not None:
                progress(Progress(stage, message, done, total, path,
                                  done if total is not None else None, total))
        report('network-validate', 'Validating package before LAN transfer…')
        before = snapshot(source)  # Reject every symlink, reparse point and special file before validation.
        if len(before) - 1 > MAX_ENTRIES:
            raise ValueError('Too many package entries for the receiver.')
        errors = validate(source, token)
        if errors:
            raise ValueError('Package validation failed: ' + '\n'.join(errors))
        if not (source / 'recipe.json').is_file():
            raise ValueError('PS5 transfer requires a launch package with recipe.json.')
        if self.package_name is None:
            # The display title survives a renamed output directory. Older
            # packages predate this metadata and keep their folder-name default.
            name_path = 'metadata/instance-name.json'
            if name_path in before:
                if before[name_path][3] > 4096:
                    raise ValueError('Instance name metadata is too large; use --name to override it.')
                try:
                    with _open_local_file(source, name_path, before[name_path]) as manifest:
                        metadata = json.load(manifest)
                except (ValueError, RecursionError) as exc:
                    raise ValueError('Invalid instance name metadata; use --name to override it.') from exc
                if not isinstance(metadata, dict) or type(metadata.get('schema')) is not int or metadata['schema'] != 1:
                    raise ValueError('Invalid instance name metadata; use --name to override it.')
                name = metadata.get('name')
            else:
                name = source.name
        else:
            name = self.package_name
        if not isinstance(name, str):
            raise ValueError('Package instance name must be a string; use --name to override it.')
        encoded_name = safe_relative(name, package=True)
        entries: list[tuple[str, int, int, bytes]] = []
        hashing_started = metrics.tick()
        total = files = 0
        for relative, info in sorted(before.items()):
            if relative == '.':
                continue
            token.check()
            safe_relative(relative)
            kind = int(stat.S_ISREG(info[0]))
            size = info[3] if kind else 0
            if size > MAX_FILE or total + size > MAX_TOTAL:
                raise ValueError('Package exceeds receiver size limits.')
            digest = hashlib.sha256()
            if kind:
                with _open_local_file(source, relative, info) as local:
                    while block := local.read(MAX_FRAME):
                        token.check()
                        digest.update(block)
                files += 1
            total += size
            entries.append((relative, kind, size, digest.digest() if kind else bytes(32)))
        metrics.elapsed('prehash_s', hashing_started)
        if snapshot(source) != before:
            raise ValueError('Package changed during validation/hashing. Retry with stable local files.')
        metrics.elapsed('validation_s', validation_started)
        report('network-connect', f'Pairing with PS5 at {host}:{self.port}…')
        committed = False
        try:
            pairing_started = metrics.tick()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
                configure_socket(connection, self.socket_buffer, metrics)
                connection.settimeout(3)
                connection.connect((host, self.port))
                session = Session(connection, token, metrics=metrics)
                session.authenticate(key)
                metrics.elapsed('pairing_s', pairing_started)
                session.send(BEGIN, BEGIN_INFO.pack(int(self.destination == 'usb0'), len(encoded_name),
                                                   len(entries), total) + encoded_name)
                receiver_capabilities = session.ack()
                metrics.add('receiver_metrics_supported', int(receiver_capabilities == b'ready;metrics=1'))
                copied, started = 0, time.monotonic()
                last_report, last_bytes, rate = started, 0, 0.
                report('network-send', 'Sending to PS5…', 0, total)
                transfer_started = metrics.tick()
                for relative, kind, size, expected in entries:
                    token.check()
                    encoded = safe_relative(relative)
                    session.send(ENTRY, ENTRY_INFO.pack(kind, len(encoded), size) + expected + encoded)
                    session.ack()
                    if not kind:
                        continue
                    digest = hashlib.sha256()
                    with _open_local_file(source, relative, before[relative]) as handle:
                        sent = 0
                        while True:
                            read_started = metrics.tick()
                            block = handle.read(self.chunk_size)
                            metrics.elapsed('file_read_s', read_started)
                            if not block:
                                break
                            token.check()
                            sent += len(block)
                            if sent > size:
                                raise ValueError('Source grew while uploading; package not committed.')
                            hash_started = metrics.tick()
                            digest.update(block)
                            metrics.elapsed('file_hash_s', hash_started)
                            session.send(DATA, block)
                            metrics.add('data_frames')
                            metrics.add('bytes', len(block))
                            copied += len(block)
                            now = time.monotonic()
                            if now - last_report >= .1:
                                sample = (copied - last_bytes) / (now - last_report)
                                rate = sample if not rate else .25 * sample + .75 * rate
                                report('network-send', f'Sending to PS5 — {rate / (1024 * 1024):.1f} MiB/s', copied, total, relative)
                                last_report, last_bytes = now, copied
                        if sent != size or digest.digest() != expected:
                            raise ValueError('Source changed while uploading; package not committed.')
                    metrics.add('files')
                    session.send(END_FILE)
                    session.ack()
                report('network-send', 'All file data sent; waiting for verification…', copied, total)
                metrics.elapsed('transfer_s', transfer_started)
                if snapshot(source) != before:
                    raise ValueError('Package changed during upload; package not committed.')
                token.check()
                report('network-verify', 'PS5 is verifying staged file hashes…', 0, total)
                verify_started = metrics.tick()
                session.send(VERIFY)
                last_verified = 0
                while True:
                    kind, body = session.receive()
                    if kind == ACK and body == b'verified':
                        break
                    if kind != VERIFY_PROGRESS or len(body) != 16:
                        raise NetworkTransferError('Invalid receiver verification response.')
                    verified, expected_total = struct.unpack('!QQ', body)
                    if expected_total != total or not last_verified <= verified <= total:
                        raise NetworkTransferError(
                            f'Invalid receiver verification progress: verified={verified}, '
                            f'receiver_total={expected_total}, sender_total={total}, '
                            f'previous_verified={last_verified}. Package not committed; local output retained.')
                    last_verified = verified
                    report('network-verify', 'PS5 is verifying staged file hashes…', verified, total)
                metrics.elapsed('verification_s', verify_started)
                token.check()
                report('network-commit', 'PS5 is verifying and publishing the package…', copied, total)
                commit_started = metrics.tick()
                session.send(COMMIT)
                committed = True
                # Publication is the commit point: cancellation after COMMIT cannot
                # revoke it. Resolve the receipt rather than incorrectly report cancellation.
                session.token = CancellationToken()
                remote = session.ack().decode('utf-8')
                expected_path = ROOTS[self.destination] + '/' + name
                if remote != expected_path:
                    raise NetworkTransferError('Receiver returned an unexpected publication receipt.')
                metrics.elapsed('commit_s', commit_started)
                receiver_metrics = None
                if receiver_capabilities == b'ready;metrics=1':
                    # The authenticated publication receipt is already confirmed.
                    # Missing optional diagnostics cannot revoke that success.
                    session.timeout = min(session.timeout, 2.)
                    try:
                        session.send(GET_METRICS)
                        receiver_metrics = decode_receiver_metrics(session.ack(), total, files)
                        for metric_name, value in receiver_metrics.items():
                            metrics.add('receiver_' + metric_name, value)
                        report('network-metrics', f"Receiver: verification {receiver_metrics['verify_s']:.3f}s, "
                               f"permissions {receiver_metrics['permissions_s']:.3f}s, "
                               f"flushes {int(receiver_metrics['fsyncs'])}", total, total)
                    except (OSError, ValueError):
                        metrics.add('receiver_metrics_unavailable')
                        report('network-metrics', 'Publication confirmed; optional receiver metrics unavailable.', total, total)
                report('network-complete', f'Package received and verified: {remote}', total, total)
                return NetworkResult(source, remote, files, total, receiver_metrics)
        except OSError as exc:
            state = ('Publication receipt was lost; check the PS5 import folder before retrying under a new name.'
                     if committed else 'Transfer not confirmed; local output retained. Check the receiver, pairing token and LAN.')
            raise NetworkTransferError(f'{state} {exc}') from exc


def run_network_queue(requests: tuple[NetworkRequest, ...], progress: ProgressCallback,
                      cancellation: CancellationToken) -> tuple[NetworkResult, ...]:
    results = []
    for request in requests:
        cancellation.check()
        try:
            results.append(request.run(progress, cancellation))
        except (OSError, ValueError) as exc:
            raise NetworkTransferError(f'{len(results)}/{len(requests)} packages confirmed on PS5. '
                                       f'Queue stopped at {request.source.name}; local outputs retained. {exc}') from exc
    return tuple(results)
