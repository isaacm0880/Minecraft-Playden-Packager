"""Read-only package streaming to the paired PS5 import receiver."""
import hashlib
import os
from pathlib import Path
import socket
import stat
import time
from dataclasses import dataclass, field
import struct
from typing import BinaryIO

from .cleanup import linked, plain_directory, snapshot
from .operations import CancellationToken
from .progress import Progress, ProgressCallback
from .validator import validate
from .network_protocol import (
    ACK, BEGIN, BEGIN_INFO, COMMIT, DATA, END_FILE, ENTRY, ENTRY_INFO, VERIFY, VERIFY_PROGRESS,
    MAX_ENTRIES, MAX_FILE, MAX_FRAME, MAX_TOTAL, PORT, ROOTS,
    NetworkTransferError, Session, lan_address, pairing_key, safe_relative,
)


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
        if not stat.S_ISREG(info.st_mode) or actual != expected:
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


@dataclass(frozen=True)
class NetworkRequest:
    source: Path
    host: str
    pairing_token: str = field(repr=False)
    destination: str = 'internal'
    port: int = PORT
    package_name: str | None = None

    def run(self, progress: ProgressCallback | None = None,
            cancellation: CancellationToken | None = None) -> NetworkResult:
        token = cancellation if cancellation is not None else CancellationToken()
        host, key = lan_address(self.host.strip()), pairing_key(self.pairing_token)
        if self.destination not in ROOTS or not 1 <= self.port <= 65535:
            raise ValueError('Select internal or usb0 and a valid TCP port.')
        source = self.source.absolute()
        plain_directory(source)
        source = source.resolve(strict=True)
        name = self.package_name if self.package_name is not None else source.name
        encoded_name = safe_relative(name, package=True)
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
        entries: list[tuple[str, int, int, bytes]] = []
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
        if snapshot(source) != before:
            raise ValueError('Package changed during validation/hashing. Retry with stable local files.')
        report('network-connect', f'Pairing with PS5 at {host}:{self.port}…')
        committed = False
        try:
            with socket.create_connection((host, self.port), timeout=3) as connection:
                session = Session(connection, token)
                session.authenticate(key)
                session.send(BEGIN, BEGIN_INFO.pack(int(self.destination == 'usb0'), len(encoded_name),
                                                   len(entries), total) + encoded_name)
                session.ack()
                copied, started = 0, time.monotonic()
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
                        while block := handle.read(MAX_FRAME):
                            token.check()
                            sent += len(block)
                            if sent > size:
                                raise ValueError('Source grew while uploading; package not committed.')
                            digest.update(block)
                            session.send(DATA, block)
                            copied += len(block)
                            rate = copied / max(.001, time.monotonic() - started) / (1024 * 1024)
                            report('network-send', f'Sending to PS5 — {rate:.1f} MiB/s', copied, total, relative)
                        if sent != size or digest.digest() != expected:
                            raise ValueError('Source changed while uploading; package not committed.')
                    session.send(END_FILE)
                    session.ack()
                if snapshot(source) != before:
                    raise ValueError('Package changed during upload; package not committed.')
                token.check()
                session.send(VERIFY)
                while True:
                    kind, body = session.receive()
                    if kind == ACK and body == b'verified':
                        break
                    if kind != VERIFY_PROGRESS or len(body) != 16:
                        raise NetworkTransferError('Invalid receiver verification response.')
                    verified, expected_total = struct.unpack('!QQ', body)
                    if expected_total != total or verified > total:
                        raise NetworkTransferError('Invalid receiver verification progress.')
                    report('network-verify', 'PS5 is verifying staged file hashes…', verified, total)
                token.check()
                report('network-commit', 'PS5 is verifying and publishing the package…', copied, total)
                session.send(COMMIT)
                committed = True
                # Publication is the commit point: cancellation after COMMIT cannot
                # revoke it. Resolve the receipt rather than incorrectly report cancellation.
                session.token = CancellationToken()
                remote = session.ack().decode('utf-8')
                expected_path = ROOTS[self.destination] + '/' + name
                if remote != expected_path:
                    raise NetworkTransferError('Receiver returned an unexpected publication receipt.')
                report('network-complete', f'Package received and verified: {remote}', total, total)
                return NetworkResult(source, remote, files, total)
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
