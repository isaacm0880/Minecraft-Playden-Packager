"""Require all audited, smoke-tested candidates to name the same clean commit."""
import argparse
import hashlib
import json
from pathlib import Path


def verify(windows: Path, linux: Path, expected: str, macos_arm64: Path | None = None,
           macos_x86_64: Path | None = None) -> dict:
    toolchain = json.loads((Path(__file__).with_name('toolchain.json')).read_text())
    records = []
    targets = [(windows, '-Windows-x64.zip', 'win32', 'x86_64'),
               (linux, '-Linux-x86_64.AppImage', 'linux', 'x86_64')]
    if (macos_arm64 is None) != (macos_x86_64 is None):
        raise ValueError('Both macOS architectures are required together')
    if macos_arm64 is not None and macos_x86_64 is not None:
        targets += [(macos_arm64, '-macOS-ARM64.zip', 'darwin', 'arm64'),
                    (macos_x86_64, '-macOS-x86_64.zip', 'darwin', 'x86_64')]
    for directory, suffix, target, architecture in targets:
        record = json.loads((directory / 'artifact.json').read_text())
        metadata = json.loads((directory / 'build-metadata.json').read_text())
        audit = json.loads((directory / 'content-audit.json').read_text())
        path = directory / record['filename']
        if path.name != 'Minecraft-Playden-Packager-v0.1.0' + suffix or path.parent != directory:
            raise ValueError('Unexpected release artifact name')
        if record['preview'] or metadata['source_dirty'] or metadata['preview']:
            raise ValueError('Dirty/preview artifacts cannot form a release candidate')
        if metadata['platform'] != target or metadata['architecture'] != architecture or metadata['python'] != toolchain['python'][target]:
            raise ValueError(f'Unexpected platform or Python patch version: {target}')
        if not record['content_audit_passed'] or not record['frozen_smoke_passed'] or not audit['passed']:
            raise ValueError('Missing successful audit or frozen smoke evidence')
        for item in (record, metadata, audit):
            if item['source_commit'] != expected or len(expected) != 40:
                raise ValueError('Artifacts do not use the required exact commit')
            if item['source_tree_sha256'] != metadata['source_tree_sha256']:
                raise ValueError('Artifact source-tree fingerprint mismatch')
        if path.stat().st_size != record['size'] or hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('Artifact checksum/size mismatch')
        checksums = directory / 'SHA256SUMS'
        if checksums.exists() and checksums.read_text(encoding='utf-8') != record['sha256'] + '  ' + record['filename'] + '\n':
            raise ValueError('Candidate SHA256SUMS does not match the verified artifact')
        if target == 'darwin':
            bundle = audit.get('macos_bundle') or {}
            if bundle.get('architecture') != architecture or bundle.get('icon_verified') is not True:
                raise ValueError('Missing macOS bundle/architecture/icon audit')
        records.append((record, metadata))
    for _, metadata in records[1:]:
        for key in ('source_tree_sha256', 'source_files', 'dependencies', 'version', 'branding'):
            if records[0][1][key] != metadata[key]:
                raise ValueError(f'Cross-platform source or toolchain mismatch: {key}')
    manual = ['Visible Windows startup, Explorer and physical USB workflow',
              'Native KDE Wayland and another Linux desktop, with physical USB',
              'Minecraft 26.3 package regression and PS5/Playden acceptance']
    if macos_arm64 is not None:
        manual.append('Optional experimental macOS: physical Apple Silicon and Intel Macs, Gatekeeper, Finder and USB')
    return {'version': '0.1.0', 'source_commit': expected,
            'binary_platforms': [target + '-' + architecture for _, _, target, architecture in targets],
            'macos_status': 'experimental; not verified for release; source-only in v0.1.0',
            'source_tree_sha256': records[0][1]['source_tree_sha256'],
            'python_versions': {(item[1]['platform'] + '-' + item[1]['architecture'] if item[1]['platform'] == 'darwin'
                                 else item[1]['platform']): item[1]['python'] for item in records},
            'artifacts': [item[0] for item in records], 'automated_pair_checks_passed': True,
            'release_published': False,
            'manual_acceptance_required': manual}



def write_report(output: Path, report: dict) -> None:
    checksums = output.parent / 'SHA256SUMS'
    if output.exists() or checksums.exists():
        raise ValueError('Use a new report and checksum destination; existing evidence is preserved')
    with output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + '\n')
    with checksums.open('x', encoding='utf-8') as stream:
        stream.write(''.join(f'{artifact["sha256"]}  {artifact["filename"]}\n' for artifact in report['artifacts']))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--windows', type=Path, required=True)
    parser.add_argument('--linux', type=Path, required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--macos-arm64', type=Path, help='Optional: verify both macOS candidates as an additional cohort')
    parser.add_argument('--macos-x86-64', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output file')
    report = verify(args.windows, args.linux, args.source_sha, args.macos_arm64, args.macos_x86_64)
    write_report(args.output, report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
