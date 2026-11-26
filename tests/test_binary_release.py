"""Two required binaries, strict cohort verification and optional Mac isolation."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from release.verify_pair import verify, write_report
from release.virustotal_scan import REPOSITORY, ValidationError, select_artifacts, verified_targets

SOURCE = 'a' * 40
ROOT = Path(__file__).resolve().parents[1]


def candidates(root, version='0.1.0'):
    for key, platform, suffix, python in (
        ('windows', 'win32', 'Windows-x64.zip', '3.11.9'),
        ('linux', 'linux', 'Linux-x86_64.AppImage', '3.11.14')):
        directory = root / key
        directory.mkdir()
        name = f'Minecraft-Playden-Packager-v{version}-' + suffix
        artifact = directory / name
        data = b'synthetic executable, never executed'
        if key == 'windows':
            with zipfile.ZipFile(artifact, 'w') as archive:
                archive.writestr('MinecraftPlaydenPackager/MinecraftPlaydenPackager.exe', data)
        else:
            artifact.write_bytes(data)
        identity = {'source_commit': SOURCE, 'source_tree_sha256': 'b' * 64, 'preview': False}
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        record = {**identity, 'filename': name, 'size': artifact.stat().st_size, 'sha256': digest,
                  'content_audit_passed': True, 'frozen_smoke_passed': True}
        metadata = {**identity, 'source_dirty': False, 'platform': platform, 'architecture': 'x86_64',
                    'python': python, 'version': version, 'source_files': {}, 'dependencies': {}, 'branding': {}}
        audit = {**identity, 'passed': True, 'symlinks': {},
                 'files': {'MinecraftPlaydenPackager.exe': hashlib.sha256(data).hexdigest()} if key == 'windows' else {}}
        for filename, value in (('artifact.json', record), ('build-metadata.json', metadata), ('content-audit.json', audit)):
            (directory / filename).write_text(json.dumps(value))
        (directory / 'SHA256SUMS').write_text(digest + '  ' + name + '\n')
    return root / 'windows', root / 'linux'


def test_two_binary_cli_writes_exactly_two_checksums_without_macos(tmp_path):
    windows, linux = candidates(tmp_path)
    result = subprocess.run([sys.executable, '-m', 'release.verify_pair', '--windows', str(windows),
                             '--linux', str(linux), '--source-sha', SOURCE, '--output', str(tmp_path / 'release-pair.json')],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / 'release-pair.json').read_text())
    assert report['binary_platforms'] == ['win32-x86_64', 'linux-x86_64']
    assert len(report['artifacts']) == 2
    assert len((tmp_path / 'SHA256SUMS').read_text().splitlines()) == 2
    assert not any('Mac' in test for test in report['manual_acceptance_required'])
    assert report['release_published'] is False


@pytest.mark.parametrize('platform', ['windows', 'linux'])
@pytest.mark.parametrize('file,key,value', [
    ('artifact.json', 'content_audit_passed', False), ('artifact.json', 'frozen_smoke_passed', False),
    ('artifact.json', 'preview', True), ('artifact.json', 'sha256', 'c' * 64),
    ('artifact.json', 'size', 0), ('content-audit.json', 'passed', False),
    ('content-audit.json', 'source_commit', 'c' * 40),
    ('build-metadata.json', 'source_dirty', True), ('build-metadata.json', 'source_commit', 'c' * 40),
    ('build-metadata.json', 'source_files', {'changed': 'digest'}),
    ('build-metadata.json', 'dependencies', {'changed': 'version'}),
    ('build-metadata.json', 'architecture', 'arm64'), ('build-metadata.json', 'python', '3.12.0')])
def test_two_binary_mode_keeps_each_existing_gate(tmp_path, platform, file, key, value):
    windows, linux = candidates(tmp_path)
    path = tmp_path / platform / file
    record = json.loads(path.read_text())
    record[key] = value
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        verify(windows, linux, SOURCE)


@pytest.mark.parametrize('platform', ['windows', 'linux'])
def test_original_checksum_manifest_tampering_is_rejected(tmp_path, platform):
    windows, linux = candidates(tmp_path)
    (tmp_path / platform / 'SHA256SUMS').write_text('incorrect\n')
    with pytest.raises(ValueError, match='SHA256SUMS'):
        verify(windows, linux, SOURCE)


def test_pair_workflow_has_only_the_two_official_binary_targets():
    text = (ROOT / '.github/workflows/desktop-build.yml').read_text()
    pair = text.split('  verify-pair:\n')[1]
    assert 'needs: [source, windows, linux]' in pair
    assert '--macos-' not in pair
    assert 'name: macOS-' not in pair
    assert '  macos:\n' not in text
    assert 'build_macos:' not in text


def test_current_beta_candidate_names_follow_the_build_metadata(tmp_path):
    windows, linux = candidates(tmp_path, '0.4.0b2')
    assert verify(windows, linux, SOURCE)['version'] == '0.4.0b2'


def test_windows_linux_scanner_context_verifies_only_selected_artifacts(tmp_path):
    windows, linux = candidates(tmp_path)
    pair = tmp_path / 'pair'
    pair.mkdir()
    write_report(pair / 'release-pair.json', verify(windows, linux, SOURCE))
    run = {'id': 123, 'head_sha': SOURCE, 'status': 'completed', 'conclusion': 'success', 'event': 'push',
           'head_branch': 'release/v0.1.0-prep', 'path': '.github/workflows/desktop-build.yml',
           'repository': {'full_name': REPOSITORY}}
    artifacts = []
    for number, (key, label) in enumerate((('windows', 'Windows-x64'), ('linux', 'Linux-x86_64'), ('pair', 'Verified-source-pair')), 1):
        wrapper = tmp_path / (key + '.zip')
        with zipfile.ZipFile(wrapper, 'w') as archive:
            for path in (tmp_path / key).iterdir():
                archive.write(path, path.name)
        artifacts.append({'id': number, 'name': label + '-' + SOURCE, 'expired': False,
                          'size_in_bytes': wrapper.stat().st_size,
                          'digest': 'sha256:' + hashlib.sha256(wrapper.read_bytes()).hexdigest()})
    # A failed/unavailable optional Mac must never enter the required cohort.
    artifacts.append({'name': 'macOS-ARM64-' + SOURCE, 'expired': True})
    selected = select_artifacts(run, artifacts, '123', include_macos=False)
    assert set(selected) == {'windows', 'linux', 'pair'}
    (tmp_path / 'context.json').write_text(json.dumps({'run': run, 'artifacts': artifacts, 'include_macos': False}))
    _, targets = verified_targets(tmp_path, False)
    assert len(targets) == 3  # Two archives and the audited Windows executable.
    with pytest.raises(ValidationError, match='no macOS'):
        verified_targets(tmp_path, True)
    with pytest.raises(ValidationError):
        select_artifacts(run, artifacts[1:], '123', include_macos=False)


def test_macos_source_ci_is_explicitly_optional_but_static_targets_remain():
    text = (ROOT / '.github/workflows/test.yml').read_text()
    required, optional = text.split('  macos-source:\n')
    assert 'os: [ubuntu-latest, windows-latest]' in required
    assert '--platform darwin minecraft_playden release' in required
    assert "if: github.event_name == 'workflow_dispatch' && inputs.test_macos" in optional
    assert 'continue-on-error: true' in optional
    assert 'python -m pytest' in optional


def test_manual_scan_cli_uses_verified_context_without_a_second_run_id(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from release import virustotal_scan as scanner
    pair = tmp_path / 'pair'
    pair.mkdir()
    (pair / 'release-pair.json').write_text(json.dumps({'source_tree_sha256': 'b' * 64}))
    context = {'run': {'id': 123, 'head_sha': SOURCE}, 'scanner_commit': 'c' * 40}
    verify_context = Mock(return_value=(context, []))
    monkeypatch.setattr(scanner, 'verified_targets', verify_context)
    monkeypatch.setattr(scanner, 'VirusTotal', lambda key: SimpleNamespace(limiter=SimpleNamespace(calls=0)))
    monkeypatch.setattr(scanner, 'scan', lambda targets, client, mode: {})
    monkeypatch.setattr(scanner, 'write_report', Mock())
    monkeypatch.setattr(sys, 'argv', ['scanner', 'scan', '--workspace', str(tmp_path),
                                    '--report-dir', str(tmp_path / 'report')])
    assert scanner.main() == 0
    verify_context.assert_called_once_with(tmp_path, False)


def test_selected_v010_pair_pins_binary_source_and_original_checksums():
    manifest = json.loads((ROOT / 'release/v0.1.0-candidate.json').read_text())
    assert manifest['source_commit'] == '70daa32e92c2fe7cac47737c640ca19c2838bbd4'
    assert manifest['binary_platforms'] == ['win32-x86_64', 'linux-x86_64']
    assert len(manifest['artifacts']) == 2
    # The v0.1.0 binary receipt is historical, not an approval of newer sources.
    assert manifest['application_tree_sha256'] == '0b2f7500e9b1558b767ed89a22e8ee1619fd979053777d0514a723cb58acdc78'
    for item in manifest['artifacts']:
        assert item['source_commit'] == manifest['source_commit']
        assert item['content_audit_passed'] and item['frozen_smoke_passed'] and not item['preview']
    expected = ''.join(item['sha256'] + '  ' + item['filename'] + '\n' for item in manifest['artifacts'])
    assert (ROOT / 'release/v0.1.0-SHA256SUMS').read_text() == expected
    assert manifest['manual_acceptance_completed'] is False
