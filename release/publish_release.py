"""Publish only the pinned original v0.1.0 assets; never build or repackage."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .provenance import application_hash, git
from .verify_pair import verify, write_report
from .virustotal_scan import (APIError, GitHub, REPOSITORY, ValidationError, audit_zip,
                              checked_hash, https_url)

TAG = 'v0.1.0'
TITLE = 'Minecraft Playden Packager v0.1.0'
BINARY_SOURCE = '70daa32e92c2fe7cac47737c640ca19c2838bbd4'


class ReleaseAPI(GitHub):
    def get(self, route: str) -> Any:
        return super().get(route)

    def change(self, method: str, route: str, value: dict) -> dict:
        url = f'https://api.github.com/repos/{REPOSITORY}/{route}'
        request = urllib.request.Request(url, data=json.dumps(value).encode(), method=method,
                  headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json',
                           'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
        with self.opener.open(request, timeout=120) as response:
            return json.loads(response.read(16 * 1024 * 1024))

    def upload(self, release: dict, path: Path) -> dict:
        url = release['upload_url'].split('{', 1)[0]
        https_url(url, ('uploads.github.com',))
        expected = f'/repos/{REPOSITORY}/releases/{release["id"]}/assets'
        if urllib.parse.urlsplit(url).path != expected:
            raise ValidationError('Unexpected release upload destination')
        request = urllib.request.Request(url + '?' + urllib.parse.urlencode({'name': path.name}),
                  data=path.read_bytes(), method='POST',
                  headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/octet-stream',
                           'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
        with self.opener.open(request, timeout=300) as response:
            return json.loads(response.read(1024 * 1024))

    def release_or_none(self) -> dict | None:
        try:
            return self.get(f'releases/tags/{TAG}')
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # Drafts may not be returned by the tag endpoint on all hosts.
                matches = [item for item in self.get('releases?per_page=100') if item['tag_name'] == TAG]
                if len(matches) > 1:
                    raise ValidationError('Multiple releases found for the tag')
                return matches[0] if matches else None
            raise


def assert_asset(asset: dict, path: Path) -> None:
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    if (asset.get('name') != path.name or asset.get('state') != 'uploaded'
            or asset.get('size') != path.stat().st_size or asset.get('digest') != 'sha256:' + expected):
        raise ValidationError('Uploaded asset digest/size/state mismatch: ' + path.name)


def assert_publish_ref(api: ReleaseAPI, root: Path, expected: str) -> None:
    if not re.fullmatch('[0-9a-f]{40}', expected) or git(root, 'rev-parse', 'HEAD') != expected:
        raise ValidationError('Publisher checkout must match the exact approved main commit')
    if os.environ.get('GITHUB_REF') != 'refs/tags/' + TAG:
        raise ValidationError('Publication is restricted to the approved version tag')
    if api.get('branches/main')['commit']['sha'] != expected:
        raise ValidationError('Tag must point to the approved current main commit')
    ref = api.get('git/ref/tags/' + TAG)['object']
    # Accept lightweight or one annotated tag, without creating/moving either.
    if ref['type'] == 'tag':
        ref = api.get('git/tags/' + ref['sha'])['object']
    if ref['type'] != 'commit' or ref['sha'] != expected:
        raise ValidationError('Version tag does not identify the approved main commit')


def stage_assets(api: ReleaseAPI, root: Path, workspace: Path) -> list[Path]:
    manifest = json.loads((root / 'release/v0.1.0-candidate.json').read_text())
    if manifest['source_commit'] != BINARY_SOURCE or manifest['application_tree_sha256'] != application_hash(root):
        raise ValidationError('Application differs from the selected original binary cohort')
    run_id = manifest['workflow_run']
    run = api.get(f'actions/runs/{run_id}')
    if (run['head_sha'] != BINARY_SOURCE or run['head_branch'] != 'release/v0.1.0-prep'
            or run['path'] != '.github/workflows/desktop-build.yml'
            or run['repository']['full_name'] != REPOSITORY):
        raise ValidationError('Original workflow provenance mismatch')
    selected = {item['filename']: item for item in manifest['artifacts']}
    for platform, wrapper in manifest['workflow_wrappers'].items():
        if platform not in ('windows', 'linux'):
            raise ValidationError('Only Windows/Linux assets may be published')
        job = api.get(f'actions/jobs/{wrapper["job_id"]}')
        if job['run_id'] != run_id or job['name'] != platform or job['conclusion'] != 'success':
            raise ValidationError('Original native build job did not succeed')
        record = api.get(f'actions/artifacts/{wrapper["artifact_id"]}')
        label = 'Windows-x64' if platform == 'windows' else 'Linux-x86_64'
        if (record['expired'] or record['workflow_run']['id'] != run_id
                or record['workflow_run']['head_sha'] != BINARY_SOURCE
                or record['name'] != label + '-' + BINARY_SOURCE
                or record['digest'] != 'sha256:' + wrapper['sha256'] or record['size_in_bytes'] != wrapper['size']):
            raise ValidationError('Original artifact wrapper provenance mismatch')
        archive = workspace / (platform + '.zip')
        api.download(wrapper['artifact_id'], archive)
        checked_hash(archive, wrapper['sha256'], wrapper['size'])
        filename = next(name for name in selected if ('Windows-x64.zip' if platform == 'windows' else 'Linux-x86_64.AppImage') in name)
        from .virustotal_scan import unpack_wrapper
        unpack_wrapper(archive, workspace / platform,
                       {filename, 'SHA256SUMS', 'artifact.json', 'build-metadata.json', 'content-audit.json'})
        checked_hash(workspace / platform / filename, selected[filename]['sha256'], selected[filename]['size'])
    pair = verify(workspace / 'windows', workspace / 'linux', BINARY_SOURCE)
    if pair['artifacts'] != manifest['artifacts']:
        raise ValidationError('Retained artifact records changed')
    audit_zip(workspace / 'windows' / manifest['artifacts'][0]['filename'],
              json.loads((workspace / 'windows/content-audit.json').read_text()), workspace / 'inventory', 'windows', False)
    output = workspace / 'upload'
    output.mkdir()
    write_report(output / 'release-pair.json', pair)
    if (output / 'SHA256SUMS').read_bytes() != (root / 'release/v0.1.0-SHA256SUMS').read_bytes():
        raise ValidationError('Generated checksums differ from selected original checksums')
    for platform in ('windows', 'linux'):
        filename = json.loads((workspace / platform / 'artifact.json').read_text())['filename']
        shutil.copyfile(workspace / platform / filename, output / filename)
        for name in ('artifact.json', 'build-metadata.json', 'content-audit.json', 'SHA256SUMS'):
            shutil.copyfile(workspace / platform / name, output / (platform + '-' + name))
    shutil.copyfile(root / 'release/v0.1.0-candidate.json', output / 'candidate-provenance.json')
    shutil.copyfile(root / 'docs/INSTALLATION.md', output / 'INSTALLATION.md')
    return sorted(output.iterdir())


def publish(api: ReleaseAPI, root: Path, assets: list[Path], expected: str) -> dict:
    assert_publish_ref(api, root, expected)
    body = (root / 'RELEASE_NOTES.md').read_text(encoding='utf-8')
    release = api.release_or_none()
    if release is not None and release.get('draft') is not True:
        raise ValidationError('Existing published release will not be modified')
    if release is None:
        release = api.change('POST', 'releases', {'tag_name': TAG, 'target_commitish': expected,
                             'name': TITLE, 'body': body, 'draft': True, 'prerelease': False})
    if release['tag_name'] != TAG or release['target_commitish'] != expected:
        raise ValidationError('Existing draft has a different tag or approved commit')
    existing = api.get(f'releases/{release["id"]}/assets?per_page=100')
    by_name = {item['name']: item for item in existing}
    if len(by_name) != len(existing) or set(by_name) - {path.name for path in assets}:
        raise ValidationError('Unexpected or duplicate draft assets; original draft retained')
    for path in assets:
        asset = by_name.get(path.name)
        if asset is None:
            asset = api.upload(release, path)
        assert_asset(asset, path)  # Never overwrite a mismatched existing asset.
    uploaded = api.get(f'releases/{release["id"]}/assets?per_page=100')
    if len(uploaded) != len(assets) or {item['name'] for item in uploaded} != {p.name for p in assets}:
        raise ValidationError('Uploaded release asset set is incomplete')
    for path in assets:
        assert_asset(next(item for item in uploaded if item['name'] == path.name), path)
    assert_publish_ref(api, root, expected)
    result = api.change('PATCH', f'releases/{release["id"]}',
                        {'name': TITLE, 'body': body, 'draft': False, 'prerelease': False, 'make_latest': 'true'})
    if result.get('draft') is not False:
        raise ValidationError('GitHub did not confirm release publication')
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true', help='Only on the approved v0.1.0 tag')
    parser.add_argument('--approved-commit')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or not os.environ.get('GH_TOKEN'):
        parser.error('Use the authorized repository publication workflow')
    api = ReleaseAPI(os.environ['GH_TOKEN'])
    try:
        with tempfile.TemporaryDirectory(prefix='playden-release-publish-') as temporary:
            assets = stage_assets(api, root, Path(temporary))
            print('Original binary cohort verified; staged asset SHA-256 values:', flush=True)
            for path in assets:
                print(hashlib.sha256(path.read_bytes()).hexdigest(), path.name, flush=True)
            if args.publish:
                if not args.approved_commit:
                    raise ValidationError('Publication requires the approved main commit')
                print(publish(api, root, assets, args.approved_commit)['html_url'])
            else:
                print('PUBLICATION_PREFLIGHT_OK: no release, tag or asset was created or changed')
        return 0
    except urllib.error.HTTPError as exc:
        print(f'GitHub API access/upload failed: HTTP {exc.code}; stop without substitute binaries. Any draft remains unpublished.')
        return 1
    except (ValidationError, APIError) as exc:
        print('Publication validation failed: ' + str(exc) + '; any draft remains unpublished.')
        return 1
    except (OSError, ValueError, KeyError, StopIteration):
        # No raw network errors, tokens or signed redirect URLs in public logs.
        print('Publication/preflight failed; no substitute binaries used. Any draft remains unpublished.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
