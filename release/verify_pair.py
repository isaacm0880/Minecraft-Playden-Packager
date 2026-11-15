"""Require all audited, smoke-tested candidates to name the same clean commit."""
import argparse
import hashlib
import json
from pathlib import Path
from .provenance import write_json


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
        if target == 'darwin':
            bundle = audit.get('macos_bundle') or {}
            if bundle.get('architecture') != architecture or bundle.get('icon_verified') is not True:
                raise ValueError('Missing macOS bundle/architecture/icon audit')
        records.append((record, metadata))
    for _, metadata in records[1:]:
        for key in ('source_tree_sha256', 'source_files', 'dependencies', 'version', 'branding'):
            if records[0][1][key] != metadata[key]:
                raise ValueError(f'Cross-platform source or toolchain mismatch: {key}')
    return {'version': '0.1.0', 'source_commit': expected,
            'source_tree_sha256': records[0][1]['source_tree_sha256'],
            'python_versions': {(item[1]['platform'] + '-' + item[1]['architecture'] if item[1]['platform'] == 'darwin'
                                 else item[1]['platform']): item[1]['python'] for item in records},
            'artifacts': [item[0] for item in records], 'automated_pair_checks_passed': True,
            'release_published': False,
            'manual_acceptance_required': ['Visible Windows startup, Explorer and USB workflow',
                                           'Native KDE Wayland and another Linux desktop',
                                           'Physical Apple Silicon and Intel Macs: Gatekeeper, Finder, drive permissions and USB workflow',
                                           'Minecraft 26.3 package regression and console acceptance']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--windows', type=Path, required=True)
    parser.add_argument('--linux', type=Path, required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--macos-arm64', type=Path, required=True)
    parser.add_argument('--macos-x86-64', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output file')
    report = verify(args.windows, args.linux, args.source_sha, args.macos_arm64, args.macos_x86_64)
    write_json(args.output, report)
    (args.output.parent / 'SHA256SUMS').write_text(
        ''.join(f'{artifact["sha256"]}  {artifact["filename"]}\n' for artifact in report['artifacts']), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
