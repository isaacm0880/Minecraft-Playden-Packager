"""Queue-aware USB planning and reporting; copying stays in TransferRequest."""
from dataclasses import dataclass
from pathlib import Path

from .desktop import BuildRecord, ValidatedPackage, unique_output
from .transfer import TransferRequest, TransferResult
from .volumes import DriveTarget


@dataclass(frozen=True)
class TransferCandidate:
    name: str
    source: Path
    skip_reason: str = ''


def transfer_candidates(records: list[BuildRecord], standalone: ValidatedPackage | None,
                        use_queue: bool) -> list[TransferCandidate]:
    if not use_queue:
        if standalone is None:
            return []
        reason = ('File-only import is not a launch package.' if standalone.kind == 'instance-import' else
                  'Local package no longer exists.' if not standalone.path.is_dir() else '')
        return [TransferCandidate(standalone.name, standalone.path, reason)]
    candidates = []
    for record in records:
        reason = ''
        if record.state != 'completed' or record.package is None:
            reason = f'Build {record.state}.' + (f' {record.detail}' if record.detail else '')
        elif record.package.kind == 'instance-import':
            reason = 'File-only import is not a launch package.'
        elif record.removed:
            reason = 'Local output was removed after a previous verified USB copy.' + (f' USB retained at {record.usb}.' if record.usb else '')
        elif not record.package.path.is_dir():
            reason = 'Successful build output no longer exists locally.'
        candidates.append(TransferCandidate(record.request.installation.name, record.request.output, reason))
    return candidates


def destination_paths(names: list[str], drive_root: Path) -> list[Path]:
    """One stable plan, including on-disk, queued and case-only collisions."""
    parent = drive_root / 'playden/import'
    reserved: set[Path] = set()
    paths = []
    for name in names:
        path = unique_output(parent / name, reserved)
        paths.append(path)
        reserved.add(path)
    return paths


@dataclass
class UsbRecord:
    candidate: TransferCandidate
    request: TransferRequest | None = None
    state: str = 'queued'
    detail: str = ''
    result: TransferResult | None = None
    cleanup: str = ''
    cleanup_state: str = 'retained'

    @property
    def retryable(self) -> bool:
        return self.request is not None and self.state in ('failed', 'cancelled', 'not started')


def plan_transfers(candidates: list[TransferCandidate], drive: DriveTarget,
                   verify: bool) -> list[UsbRecord]:
    paths = iter(destination_paths([item.source.name for item in candidates if not item.skip_reason], drive.root))
    records = []
    for candidate in candidates:
        if candidate.skip_reason:
            records.append(UsbRecord(candidate, state='skipped', detail=candidate.skip_reason))
        else:
            path = next(paths)
            records.append(UsbRecord(candidate, TransferRequest(candidate.source, drive, verify, path.name)))
    return records


def usb_summary(records: list[UsbRecord]) -> str:
    states = ('verified', 'unverified', 'failed', 'cancelled', 'not started', 'skipped')
    counts = ', '.join(f'{sum(record.state == state for record in records)} {state}' for state in states)
    lines = ['USB queue: ' + counts + '.\nLocal cleanup: ' +
             ', '.join(f'{sum(record.cleanup_state == state for record in records)} {state}'
                       for state in ('removed', 'failed', 'cancelled')) + '.']
    for record in records:
        lines.append(f'{record.candidate.name} — {record.state}\nLocal: {record.candidate.source}' +
                     (f'\nUSB: {record.request.destination}' if record.request is not None else '') +
                     (f'\n{record.detail}' if record.detail else '') +
                     (f'\n{record.cleanup}' if record.cleanup else ''))
    return '\n\n'.join(lines)
