"""Publication is draft-first and gated on exact source and every server digest."""
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from release.publish_release import assert_asset, assert_publish_ref, publish
from release.virustotal_scan import ValidationError

COMMIT = 'a' * 40


class FakeAPI:
    def __init__(self, root, *, corrupt=False, existing=None):
        self.root = root
        self.corrupt = corrupt
        self.release = existing
        self.assets = []
        self.changes = []

    def get(self, route):
        if route == 'branches/main':
            return {'commit': {'sha': COMMIT}}
        if route == 'git/ref/tags/v0.1.0':
            return {'object': {'type': 'commit', 'sha': COMMIT}}
        if route == 'releases/1/assets?per_page=100':
            return self.assets
        raise AssertionError(route)

    def release_or_none(self):
        return self.release

    def change(self, method, route, value):
        self.changes.append((method, route, value))
        if method == 'POST':
            self.release = {**value, 'id': 1, 'upload_url': 'unused', 'html_url': 'https://github.com/release'}
        else:
            self.release.update(value)
        return self.release

    def upload(self, release, path):
        # Every upload must happen while the release is still unpublished.
        assert release['draft'] is True
        digest = '0' * 64 if self.corrupt else hashlib.sha256(path.read_bytes()).hexdigest()
        asset = {'name': path.name, 'size': path.stat().st_size,
                 'digest': 'sha256:' + digest, 'state': 'uploaded'}
        self.assets.append(asset)
        return asset


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_REF', 'refs/tags/v0.1.0')
    (tmp_path / 'RELEASE_NOTES.md').write_text('Approved release text; manual acceptance unverified.')
    asset = tmp_path / 'original.zip'
    asset.write_bytes(b'original unchanged binary fixture')
    return tmp_path, asset


def test_publish_verifies_all_uploads_before_publishing(prepared):
    root, asset = prepared
    api = FakeAPI(root)
    with patch('release.publish_release.git', return_value=COMMIT):
        result = publish(api, root, [asset], COMMIT)
    assert api.changes[0][2]['draft'] is True
    assert api.changes[-1][2]['draft'] is False
    assert result['draft'] is False
    assert asset.read_bytes() == b'original unchanged binary fixture'


def test_corrupt_upload_leaves_release_unpublished(prepared):
    root, asset = prepared
    api = FakeAPI(root, corrupt=True)
    with patch('release.publish_release.git', return_value=COMMIT), pytest.raises(ValidationError, match='digest'):
        publish(api, root, [asset], COMMIT)
    assert len(api.changes) == 1
    assert api.release['draft'] is True


def test_already_published_release_is_not_changed(prepared):
    root, asset = prepared
    api = FakeAPI(root, existing={'draft': False})
    with patch('release.publish_release.git', return_value=COMMIT), pytest.raises(ValidationError, match='published release'):
        publish(api, root, [asset], COMMIT)
    assert not api.changes


def test_mismatched_existing_asset_is_not_overwritten(prepared):
    root, asset = prepared
    api = FakeAPI(root, existing={'draft': True, 'tag_name': 'v0.1.0', 'target_commitish': COMMIT, 'id': 1})
    api.assets = [{'name': asset.name, 'size': 0, 'digest': 'sha256:' + '0' * 64, 'state': 'uploaded'}]
    with patch('release.publish_release.git', return_value=COMMIT), pytest.raises(ValidationError):
        publish(api, root, [asset], COMMIT)
    assert len(api.assets) == 1
    assert not api.changes


@pytest.mark.parametrize('field,value', [('name', 'other.zip'), ('size', 0), ('digest', None), ('state', 'new')])
def test_every_asset_property_is_checked(prepared, field, value):
    root, asset = prepared
    record = {'name': asset.name, 'size': asset.stat().st_size, 'state': 'uploaded',
              'digest': 'sha256:' + hashlib.sha256(asset.read_bytes()).hexdigest()}
    record[field] = value
    with pytest.raises(ValidationError):
        assert_asset(record, asset)


def test_publication_ref_requires_exact_main_and_version_tag(prepared, monkeypatch):
    root, _ = prepared
    api = FakeAPI(root)
    with patch('release.publish_release.git', return_value=COMMIT):
        assert_publish_ref(api, root, COMMIT)
        monkeypatch.setenv('GITHUB_REF', 'refs/heads/release/v0.1.0-prep')
        with pytest.raises(ValidationError, match='version tag'):
            assert_publish_ref(api, root, COMMIT)
    monkeypatch.setenv('GITHUB_REF', 'refs/tags/v0.1.0')
    with patch('release.publish_release.git', return_value='b' * 40), pytest.raises(ValidationError, match='approved main'):
        assert_publish_ref(api, root, COMMIT)


def test_no_candidate_rebuild_is_triggered_by_publication():
    root = Path(__file__).resolve().parents[1]
    candidate = (root / '.github/workflows/desktop-build.yml').read_text()
    assert '  push:' not in candidate
    publisher = (root / '.github/workflows/publish-release.yml').read_text()
    assert 'tags: [v0.1.0]' in publisher
    assert 'release.build_desktop' not in publisher
    assert 'refs/tags/v0.1.0' in publisher
    assert 'contents: write' not in publisher.split('  publish:\n')[0]


def test_annotated_tag_must_dereference_to_approved_main(prepared):
    root, _ = prepared
    api = FakeAPI(root)
    original = api.get
    def get(route):
        if route == 'git/ref/tags/v0.1.0':
            return {'object': {'type': 'tag', 'sha': 'b' * 40}}
        if route == 'git/tags/' + 'b' * 40:
            return {'object': {'type': 'commit', 'sha': COMMIT}}
        return original(route)
    api.get = get
    with patch('release.publish_release.git', return_value=COMMIT):
        assert_publish_ref(api, root, COMMIT)


@pytest.mark.parametrize('corrupt', [False, True])
def test_staging_preserves_original_bytes_and_rejects_corrupt_wrappers(tmp_path, corrupt):
    import json
    import shutil
    import zipfile
    from test_binary_release import candidates
    from release.publish_release import BINARY_SOURCE, REPOSITORY, stage_assets
    from release.provenance import application_hash
    source = tmp_path / 'source'
    (source / 'release').mkdir(parents=True)
    (source / 'docs').mkdir()
    (source / 'docs/INSTALLATION.md').write_text('Current guide; Mac experimental.')
    payload = tmp_path / 'payload'
    payload.mkdir()
    candidates(payload)
    manifest = {'source_commit': BINARY_SOURCE, 'workflow_run': 123,
                'application_tree_sha256': application_hash(source), 'artifacts': [], 'workflow_wrappers': {}}
    wrappers, records = {}, {}
    for index, platform in enumerate(('windows', 'linux'), 1):
        directory = payload / platform
        for filename in ('artifact.json', 'build-metadata.json', 'content-audit.json'):
            value = json.loads((directory / filename).read_text())
            value['source_commit'] = BINARY_SOURCE
            (directory / filename).write_text(json.dumps(value))
        manifest['artifacts'].append(json.loads((directory / 'artifact.json').read_text()))
        wrapper = tmp_path / (platform + '.zip')
        with zipfile.ZipFile(wrapper, 'w') as archive:
            for path in directory.iterdir():
                archive.write(path, path.name)
        digest = hashlib.sha256(wrapper.read_bytes()).hexdigest()
        manifest['workflow_wrappers'][platform] = {'artifact_id': index, 'job_id': index,
                                                  'sha256': digest, 'size': wrapper.stat().st_size}
        wrappers[index] = wrapper
        records[index] = {'expired': False, 'workflow_run': {'id': 123, 'head_sha': BINARY_SOURCE},
                          'name': ('Windows-x64' if platform == 'windows' else 'Linux-x86_64') + '-' + BINARY_SOURCE,
                          'digest': 'sha256:' + digest, 'size_in_bytes': wrapper.stat().st_size}
    (source / 'release/v0.1.0-candidate.json').write_text(json.dumps(manifest))
    (source / 'release/v0.1.0-SHA256SUMS').write_text(''.join(a['sha256'] + '  ' + a['filename'] + '\n' for a in manifest['artifacts']))
    class ReadOnlyAPI:
        def get(self, route):
            if route == 'actions/runs/123':
                return {'head_sha': BINARY_SOURCE, 'head_branch': 'release/v0.1.0-prep',
                        'path': '.github/workflows/desktop-build.yml', 'repository': {'full_name': REPOSITORY}}
            index = int(route.rsplit('/', 1)[1])
            if '/jobs/' in route:
                return {'run_id': 123, 'name': 'windows' if index == 1 else 'linux', 'conclusion': 'success'}
            return records[index]
        def download(self, index, target):
            shutil.copyfile(wrappers[index], target)
            if corrupt:
                with target.open('ab') as stream:
                    stream.write(b'corruption')
    workspace = tmp_path / 'staging'
    workspace.mkdir()
    if corrupt:
        with pytest.raises(ValidationError, match='mismatch'):
            stage_assets(ReadOnlyAPI(), source, workspace)
    else:
        assets = {p.name: p for p in stage_assets(ReadOnlyAPI(), source, workspace)}
        for record in manifest['artifacts']:
            assert hashlib.sha256(assets[record['filename']].read_bytes()).hexdigest() == record['sha256']
        assert len(assets) == 14
