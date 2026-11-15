"""VirusTotal safety tests: fake HTTP, fake clocks, synthetic artifacts only."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import zipfile

from release.virustotal_scan import (APIError, GitHub, Limiter, PLATFORMS, QuotaStop, REPOSITORY,
    Target, ValidationError, VirusTotal, audit_zip, checked_hash, existing_report, retry_after,
    scan, select_artifacts, unpack_wrapper, verified_targets, write_report, zip_entries)
from release.verify_pair import verify

KEY = 'unit-test-api-key'
SOURCE = 'a' * 40


class Clock:
    def __init__(self):
        self.now = 0
        self.waits = []
    def time(self):
        return self.now
    def sleep(self, value):
        self.waits.append(value)
        self.now += value


class Response(io.BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode())


class HTTP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.bodies = []
    def open(self, request, timeout):
        self.requests.append(request)
        self.bodies.append(b''.join(request.data) if request.data is not None else b'')
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return Response(result)


def error(code, retry=None, message='rate limit'):
    return urllib.error.HTTPError('https://www.virustotal.com/api/v3/files/fixture', code, 'fixture',
        {'Retry-After': retry} if retry is not None else {},
        io.BytesIO(json.dumps({'error': {'code': 'QuotaExceededError', 'message': message}}).encode()))


def report(digest, malicious=0, suspicious=0):
    return {'data': {'id': digest, 'type': 'file', 'attributes': {'last_analysis_date': 1700000000,
        'last_analysis_stats': {'malicious': malicious, 'suspicious': suspicious, 'undetected': 70}}}}


def run_and_artifacts():
    run = {'id': 123, 'status': 'completed', 'conclusion': 'success', 'head_sha': SOURCE,
           'head_branch': 'release/v0.1.0-prep', 'event': 'workflow_dispatch',
           'path': '.github/workflows/desktop-build.yml', 'repository': {'full_name': REPOSITORY}}
    artifacts = [{'id': i + 1, 'name': label + '-' + SOURCE, 'digest': 'sha256:' + 'b' * 64,
                  'size_in_bytes': 100, 'expired': False, 'workflow_run': {'id': 123}}
                 for i, label in enumerate([p[0] for p in PLATFORMS.values()] + ['Verified-source-pair'])]
    return run, artifacts


class ProvenanceTests(unittest.TestCase):
    def test_hash_mismatch_size_and_symlinks_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'file'
            path.write_bytes(b'fixture')
            digest = hashlib.sha256(b'fixture').hexdigest()
            self.assertEqual(checked_hash(path, digest, 7), digest)
            for wrong, size in [('c' * 64, 7), (digest, 8), ('../unsafe', 7)]:
                with self.subTest(wrong=wrong, size=size), self.assertRaises(ValidationError):
                    checked_hash(path, wrong, size)

    def test_exact_run_selection_ignores_unrelated_outputs_but_requires_all_candidates(self):
        run, artifacts = run_and_artifacts()
        selected = select_artifacts(run, artifacts + [{'name': 'source-cache'}], '123')
        self.assertEqual(set(selected), set(PLATFORMS) | {'pair'})
        for wrong in ({'conclusion': 'failure'}, {'head_branch': 'unreviewed-branch'}, {'head_sha': 'not-a-sha'},
                      {'path': '.github/workflows/untrusted.yml'}, {'repository': {'full_name': 'other/repo'}}):
            with self.subTest(wrong=wrong), self.assertRaises(ValidationError):
                select_artifacts({**run, **wrong}, artifacts, '123')
        for bad in (artifacts[:-1], artifacts + [artifacts[0]], [{**a, 'expired': True} for a in artifacts],
                    [{**a, 'workflow_run': {'id': 456}} for a in artifacts]):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                select_artifacts(run, bad, '123')

    def test_zip_executable_is_hash_checked_against_inventory_without_execution(self):
        for platform, prefix, member in [('windows', 'MinecraftPlaydenPackager/', 'MinecraftPlaydenPackager.exe'),
                ('macos-arm64', 'Minecraft Playden Packager.app/', 'Contents/MacOS/MinecraftPlaydenPackager')]:
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); path = root / 'artifact.zip'
                data = b'synthetic native executable'
                digest = hashlib.sha256(data).hexdigest()
                with zipfile.ZipFile(path, 'w') as archive:
                    archive.writestr(prefix + member, data)
                target = audit_zip(path, {'files': {member: digest}, 'symlinks': {}}, root / 'targets', platform, True)
                self.assertEqual(target.path.read_bytes(), data)
                self.assertEqual(target.sha256, digest)
                self.assertFalse(stat.S_IMODE(target.path.stat().st_mode) & 0o111)
                if os.name == 'posix':
                    self.assertEqual(stat.S_IMODE(target.path.stat().st_mode), 0o600)
                with self.assertRaises(ValidationError):
                    audit_zip(path, {'files': {member: '0' * 64}, 'symlinks': {}}, root / 'wrong', platform, True)

    def test_untrusted_zip_paths_duplicates_and_missing_executable_rejected(self):
        for name in ('../escape', '/absolute', 'C:/drive', 'dir\\escape', 'nul\x00name'):
            with self.subTest(name=name), tempfile.TemporaryFile() as stream:
                with zipfile.ZipFile(stream, 'w') as archive:
                    # Preserve malicious archive bytes on every host. ZipInfo's
                    # constructor normalizes Windows separators and truncates NUL.
                    info = zipfile.ZipInfo('placeholder')
                    info.filename = info.orig_filename = name
                    archive.writestr(info, b'fixture')
                stream.seek(0)
                with zipfile.ZipFile(stream) as archive, self.assertRaises(ValidationError):
                    zip_entries(archive)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / 'app.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('MinecraftPlaydenPackager/file.txt', b'fixture')
            with self.assertRaises(ValidationError):
                audit_zip(path, {'files': {'file.txt': hashlib.sha256(b'fixture').hexdigest()}, 'symlinks': {}}, root / 'out', 'windows', True)
            with self.assertRaises(ValidationError):
                unpack_wrapper(path, root / 'wrapper', {'artifact.json'})

    def test_full_four_platform_metadata_chain_and_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run, artifacts = run_and_artifacts()
            artifacts_by_name = {a['name'].split('-' + SOURCE)[0]: a for a in artifacts}
            for platform, (label, filename) in PLATFORMS.items():
                directory = root / platform; directory.mkdir()
                files = {}
                artifact = directory / filename
                if platform == 'linux':
                    artifact.write_bytes(b'synthetic AppImage, never executed')
                else:
                    member = 'MinecraftPlaydenPackager.exe' if platform == 'windows' else 'Contents/MacOS/MinecraftPlaydenPackager'
                    prefix = 'MinecraftPlaydenPackager/' if platform == 'windows' else 'Minecraft Playden Packager.app/'
                    data = b'synthetic executable ' + platform.encode()
                    with zipfile.ZipFile(artifact, 'w') as archive:
                        archive.writestr(prefix + member, data)
                    files[member] = hashlib.sha256(data).hexdigest()
                identity = {'source_commit': SOURCE, 'source_tree_sha256': 'd' * 64, 'preview': False}
                record = {**identity, 'filename': filename, 'size': artifact.stat().st_size, 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
                          'content_audit_passed': True, 'frozen_smoke_passed': True}
                metadata = {**identity, 'source_dirty': False, 'platform': 'win32' if platform == 'windows' else 'linux' if platform == 'linux' else 'darwin',
                            'architecture': 'arm64' if platform == 'macos-arm64' else 'x86_64',
                            'python': '3.11.14' if platform == 'linux' else '3.11.9',
                            'source_files': {}, 'dependencies': {}, 'version': '0.1.0', 'branding': {}}
                audit = {**identity, 'passed': True, 'files': files, 'symlinks': {},
                         'macos_bundle': {'architecture': metadata['architecture'], 'icon_verified': True}}
                for name, value in [('artifact.json', record), ('content-audit.json', audit), ('build-metadata.json', metadata)]:
                    (directory / name).write_text(json.dumps(value))
                wrapper = root / (platform + '.zip')
                with zipfile.ZipFile(wrapper, 'w') as z:
                    for item in directory.iterdir():z.write(item, item.name)
                a = artifacts_by_name[label]
                a.update(digest='sha256:' + hashlib.sha256(wrapper.read_bytes()).hexdigest(), size_in_bytes=wrapper.stat().st_size)
            pair = verify(root / 'windows', root / 'linux', SOURCE, root / 'macos-arm64', root / 'macos-x86_64')
            directory = root / 'pair'; directory.mkdir()
            (directory / 'release-pair.json').write_text(json.dumps(pair))
            wrapper = root / 'pair.zip'
            with zipfile.ZipFile(wrapper, 'w') as z:z.write(directory / 'release-pair.json', 'release-pair.json')
            artifacts_by_name['Verified-source-pair'].update(digest='sha256:' + hashlib.sha256(wrapper.read_bytes()).hexdigest(), size_in_bytes=wrapper.stat().st_size)
            (root / 'context.json').write_text(json.dumps({'run': run, 'artifacts': artifacts}))
            _, targets = verified_targets(root, True)
            self.assertEqual(len(targets), 7)
            (root / 'windows' / PLATFORMS['windows'][1]).write_bytes(b'tampered')
            with self.assertRaises((ValidationError, ValueError)):
                verified_targets(root, False)


class VirusTotalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'artifact.bin'; self.path.write_bytes(b'fixture artifact')
        self.target = Target('fixture', self.path, hashlib.sha256(self.path.read_bytes()).hexdigest(), self.path.stat().st_size)
        self.clock = Clock()

    def client(self, responses, maximum=30):
        http = HTTP(responses)
        return VirusTotal(KEY, Limiter(maximum, self.clock.time, self.clock.sleep), http, self.clock.sleep), http

    def test_lookup_only_missing_never_uploads_and_duplicates_reuse_result(self):
        client, http = self.client([error(404)])
        result = scan([self.target, self.target], client)
        self.assertEqual([r['status'] for r in result['results']], ['missing', 'missing'])
        self.assertEqual(result['requests_made'], 1)
        self.assertEqual([r.get_method() for r in http.requests], ['GET'])
        self.assertNotIn(KEY, json.dumps(result))

    def test_report_states_counts_and_hash_validation(self):
        for malicious, suspicious, status in [(0, 0, 'no-detections'), (1, 0, 'detections'), (0, 1, 'detections')]:
            with self.subTest(status=status):
                result = existing_report(report(self.target.sha256, malicious, suspicious), self.target.sha256)
                self.assertEqual(result['status'], status)
                self.assertEqual(result['detections'], malicious + suspicious)
        self.assertEqual(existing_report({'data': {'type': 'file', 'id': self.target.sha256, 'attributes': {}}}, self.target.sha256)['status'], 'pending')
        with self.assertRaises(APIError):existing_report(report('0' * 64), self.target.sha256)

    def test_upload_requires_consent_and_does_not_resubmit_existing_or_pending(self):
        client, http = self.client([])
        with self.assertRaises(ValidationError):client.upload(self.target)
        self.assertEqual(len(http.requests), 0)
        for response in (report(self.target.sha256), {'data': {'type': 'file', 'id': self.target.sha256, 'attributes': {}}}):
            client, http = self.client([response])
            scan([self.target], client, 'upload-missing')
            self.assertEqual([r.get_method() for r in http.requests], ['GET'])

    def test_small_and_large_uploads_are_counted_and_not_polled(self):
        for threshold, responses, methods in [(100, [error(404), {'data': {'type': 'analysis', 'id': 'fixture-analysis'}}], ['GET', 'POST']),
            (1, [error(404), {'data': 'https://www.virustotal.com/_ah/upload/fixture'}, {'data': {'type': 'analysis', 'id': 'fixture-analysis'}}], ['GET', 'GET', 'POST'])]:
            with self.subTest(threshold=threshold), patch('release.virustotal_scan.SMALL_UPLOAD', threshold):
                self.clock = Clock()
                client, http = self.client(responses)
                result = scan([self.target], client, 'upload-missing')
                self.assertEqual(result['results'][0]['status'], 'queued')
                self.assertTrue(result['results'][0]['uploaded'])
                self.assertEqual([r.get_method() for r in http.requests], methods)
                self.assertEqual(result['requests_made'], len(methods))
                self.assertEqual(len(self.clock.waits), len(methods))
                self.assertTrue(all(wait >= 21 for wait in self.clock.waits))
                self.assertIn(self.path.read_bytes(), http.bodies[-1])
                self.assertNotIn(KEY.encode(), http.bodies[-1])

    def test_rate_limit_retry_after_and_daily_quota_stop(self):
        client, http = self.client([error(429, '45'), report(self.target.sha256)])
        result = scan([self.target], client)
        self.assertEqual(result['requests_made'], 2)
        self.assertEqual(self.clock.waits, [21, 45])
        self.assertEqual(result['results'][0]['status'], 'no-detections')
        client, http = self.client([error(429, '60', 'daily quota exhausted')])
        result = scan([self.target, self.target], client)
        self.assertTrue(result['quota_stopped'])
        self.assertEqual(result['requests_made'], 1)
        self.assertEqual(result['results'][1]['status'], 'not-requested')

    def test_http_date_budget_and_rolling_rate_limit(self):
        self.assertEqual(retry_after('Thu, 01 Jan 1970 00:01:00 GMT', lambda: 0), 60)
        self.assertIsNone(retry_after('invalid'))
        limiter = Limiter(10, self.clock.time, self.clock.sleep)
        for _ in range(10):limiter.acquire()
        starts = list(limiter.starts)
        self.assertTrue(all(sum(t <= other < t + 60 for other in starts) <= 3 for t in starts))
        with self.assertRaises(QuotaStop):limiter.acquire()

    def test_timeout_bad_upload_host_and_reports_do_not_leak_key(self):
        client, http = self.client([urllib.error.URLError(KEY)])
        result = scan([self.target], client)
        self.assertEqual(result['results'][0]['status'], 'error')
        self.assertNotIn(KEY, json.dumps(result))
        report_dir = Path(self.temp.name) / 'report'
        write_report(report_dir, result)
        self.assertIn('not proof of safety', (report_dir / 'virustotal-report.md').read_text())
        self.assertNotIn(KEY, (report_dir / 'virustotal-report.json').read_text())
        with patch('release.virustotal_scan.SMALL_UPLOAD', 1):
            client, http = self.client([error(404), {'data': 'https://evil.example/upload'}])
            result = scan([self.target], client, 'upload-missing')
            self.assertEqual(result['results'][0]['status'], 'error')
            self.assertEqual(len(http.requests), 2)

    def test_repeated_429_and_budget_stop_without_unaccounted_requests(self):
        client, http = self.client([error(429, '1'), error(429, '1')])
        result = scan([self.target, self.target], client)
        self.assertEqual(result['requests_made'], 2)
        self.assertTrue(result['quota_stopped'])
        self.assertEqual(result['results'][1]['status'], 'not-requested')
        client, http = self.client([error(404)], maximum=1)
        result = scan([self.target], client, 'upload-missing')
        self.assertEqual(result['requests_made'], 1)
        self.assertEqual(len(http.requests), 1)
        self.assertTrue(result['quota_stopped'])

    def test_incomplete_and_oversized_files_are_not_claimed_clean(self):
        data = report(self.target.sha256)
        data['data']['attributes']['last_analysis_stats'] = {'timeout': 70}
        self.assertEqual(existing_report(data, self.target.sha256)['status'], 'incomplete')
        with patch('release.virustotal_scan.MAX_VT_FILE', 1):
            client, http = self.client([error(404)])
            result = scan([self.target], client, 'upload-missing')
            self.assertEqual(result['results'][0]['status'], 'too-large')
            self.assertEqual(len(http.requests), 1)

    def test_github_storage_redirect_never_forwards_repository_token(self):
        github = GitHub('synthetic-github-token')
        github.opener = Mock()
        github.opener.open.side_effect = [urllib.error.HTTPError('https://api.github.com/fixture', 302, 'redirect',
            {'Location': 'https://productionresultssa17.blob.core.windows.net/fixture?signature=fixture'}, None), io.BytesIO(b'zip fixture')]
        target = Path(self.temp.name) / 'download.zip'
        github.download(123, target)
        requests = github.opener.open.call_args_list
        self.assertIn('Authorization', requests[0].args[0].headers)
        self.assertNotIn('Authorization', requests[1].args[0].headers)
        self.assertEqual(target.read_bytes(), b'zip fixture')

    def test_tampered_file_prevents_even_lookup_and_invalid_modes_fail_closed(self):
        client, http = self.client([])
        self.path.write_bytes(b'changed')
        with self.assertRaises(ValidationError):scan([self.target], client)
        self.assertEqual(http.requests, [])
        with self.assertRaises(ValidationError):scan([], client, 'automatic')


if __name__ == '__main__':
    unittest.main()
