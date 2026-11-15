"""Manual-only VirusTotal lookup/submission of verified desktop build artifacts.

Standard library only. Downloaded artifacts are data: never imported or executed.
"""
import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import time
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

from .audit import FORBIDDEN_FILES, FORBIDDEN_PARTS, SECRET_PATTERNS
from .verify_pair import verify

REPOSITORY = 'isaacm0880/Minecraft-Playden-Packager'
BRANCHES = {'main', 'release/v0.1.0-prep'}
PLATFORMS = {
    'windows': ('Windows-x64', 'Minecraft-Playden-Packager-v0.1.0-Windows-x64.zip'),
    'linux': ('Linux-x86_64', 'Minecraft-Playden-Packager-v0.1.0-Linux-x86_64.AppImage'),
    'macos-arm64': ('macOS-ARM64', 'Minecraft-Playden-Packager-v0.1.0-macOS-ARM64.zip'),
    'macos-x86_64': ('macOS-x86_64', 'Minecraft-Playden-Packager-v0.1.0-macOS-x86_64.zip'),
}
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
MAX_ARCHIVE = 1024 * 1024 * 1024
MAX_UNPACKED = 2 * MAX_ARCHIVE
MAX_VT_FILE = 650 * 1024 * 1024
SMALL_UPLOAD = 32 * 1024 * 1024
DISCLAIMER = ('Zero detections is not proof of safety. Archive reports may not cover every bundled component. '
              'Public uploads may be retained and shared by VirusTotal. Public API use is non-commercial only.')


class ValidationError(ValueError):
    pass


class APIError(Exception):
    """Only fixed, non-secret diagnostic categories cross the API boundary."""


class QuotaStop(APIError):
    pass


def read_json(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValidationError('Unsafe or oversized metadata')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValidationError('Expected object metadata')
    return value


def sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValidationError('Expected a regular artifact file')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked_hash(path: Path, digest: str, size: int | None = None) -> str:
    if not SHA256.fullmatch(digest) or (size is not None and path.stat().st_size != size) or sha256(path) != digest:
        raise ValidationError('Artifact SHA-256 or byte size mismatch')
    return digest


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_url(url: str, hosts: tuple[str, ...]) -> str:
    if not isinstance(url, str):
        raise APIError('Invalid HTTPS endpoint')
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        raise APIError('Invalid HTTPS endpoint') from None
    if parsed.scheme != 'https' or parsed.hostname not in hosts or port not in (None, 443) or parsed.username or parsed.password or parsed.fragment:
        raise APIError('Untrusted HTTPS endpoint')
    return url


class GitHub:
    def __init__(self, token: str):
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())

    def get(self, route: str) -> dict:
        url = f'https://api.github.com/repos/{REPOSITORY}/{route}'
        request = urllib.request.Request(url, headers={'Authorization': f'Bearer {self.token}',
                    'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
        with self.opener.open(request, timeout=60) as response:
            return json.loads(response.read(16 * 1024 * 1024))

    def artifacts(self, run_id: str) -> list[dict]:
        result = []
        for page in range(1, 11):
            data = self.get(f'actions/runs/{run_id}/artifacts?per_page=100&page={page}')
            result.extend(data['artifacts'])
            if len(data['artifacts']) < 100:
                return result
        raise ValidationError('Unexpectedly large artifact list')

    def download(self, artifact_id: int, target: Path) -> None:
        url = f'https://api.github.com/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip'
        request = urllib.request.Request(url, headers={'Authorization': f'Bearer {self.token}'})
        try:
            response = self.opener.open(request, timeout=60)
        except urllib.error.HTTPError as exc:
            if exc.code != 302:
                raise APIError('GitHub artifact download failed') from None
            location = exc.headers['Location']
            host = urllib.parse.urlsplit(location).hostname or ''
            if not (re.fullmatch(r'productionresults[a-z0-9-]*\.blob\.core\.windows\.net', host)
                    or host == 'results-receiver.actions.githubusercontent.com'):
                raise APIError('Unexpected GitHub artifact storage endpoint')
            https_url(location, (host,))
            # A fresh request carries no GitHub credentials to the signed storage URL.
            response = self.opener.open(urllib.request.Request(location), timeout=120)
        with response, target.open('xb') as stream:
            total = 0
            while block := response.read(1024 * 1024):
                total += len(block)
                if total > MAX_ARCHIVE:
                    raise ValidationError('Artifact download exceeds size bound')
                stream.write(block)


def select_artifacts(run: dict, artifacts: list[dict], run_id: str) -> dict[str, dict]:
    if (str(run.get('id')) != run_id or run.get('status') != 'completed' or run.get('conclusion') != 'success'
            or run.get('path') != '.github/workflows/desktop-build.yml' or run.get('event') != 'workflow_dispatch'
            or run.get('head_branch') not in BRANCHES or run.get('repository', {}).get('full_name') != REPOSITORY
            or not re.fullmatch(r'[0-9a-f]{40}', run.get('head_sha', ''))):
        raise ValidationError('Select a successful manual desktop build in this repository/release branch')
    expected = {f'{label}-{run["head_sha"]}': key for key, (label, _) in PLATFORMS.items()}
    expected[f'Verified-source-pair-{run["head_sha"]}'] = 'pair'
    found = {}
    for artifact in artifacts:
        name = artifact.get('name')
        if name not in expected:
            continue  # Unrelated workflow outputs are never downloaded/submitted.
        key = expected[name]
        digest = artifact.get('digest', '')
        if key in found or artifact.get('expired') or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            raise ValidationError('Duplicate, expired or undigested release artifact')
        if artifact.get('workflow_run', {}).get('id', int(run_id)) != int(run_id) or artifact.get('workflow_run', {}).get('head_sha', run['head_sha']) != run['head_sha']:
            raise ValidationError('Artifact belongs to a different run')
        if type(artifact.get('id')) is not int or not 0 < artifact.get('size_in_bytes', 0) <= MAX_ARCHIVE:
            raise ValidationError('Invalid artifact ID or size')
        found[key] = artifact
    if set(found) != set(PLATFORMS) | {'pair'}:
        raise ValidationError('All four platform artifacts and the verified pair report are required')
    return found


def zip_entries(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > 10000 or sum(info.file_size for info in infos) > MAX_UNPACKED:
        raise ValidationError('ZIP inventory exceeds resource bounds')
    names = set()
    for info in infos:
        name = info.filename
        parts = PurePosixPath(name).parts
        if (info.orig_filename != name or not name or name in names or '\\' in name or ':' in name or '\x00' in name
                or PurePosixPath(name).is_absolute() or '..' in parts or info.flag_bits & 1
                or info.file_size > MAX_ARCHIVE):
            raise ValidationError('Unsafe, duplicate, encrypted or oversized ZIP member')
        names.add(name)
    return infos


def unpack_wrapper(wrapper: Path, destination: Path, allowed: set[str]) -> None:
    destination.mkdir(exist_ok=True)
    with zipfile.ZipFile(wrapper) as archive:
        infos = zip_entries(archive)
        if {info.filename for info in infos} != allowed:
            raise ValidationError('Unexpected workflow artifact wrapper contents')
        for info in infos:
            if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                raise ValidationError('Workflow wrappers must contain only regular files')
            target = destination / info.filename
            with archive.open(info) as source, target.open('xb') as output:
                while block := source.read(1024 * 1024):
                    output.write(block)


@dataclass
class Target:
    name: str
    path: Path
    sha256: str
    size: int


def audit_zip(path: Path, audit: dict, target_dir: Path, platform: str, extract: bool) -> Target | None:
    prefix = 'MinecraftPlaydenPackager/' if platform == 'windows' else 'Minecraft Playden Packager.app/'
    member = ('MinecraftPlaydenPackager.exe' if platform == 'windows' else 'Contents/MacOS/MinecraftPlaydenPackager')
    files, links = {}, {}
    extracted = None
    with zipfile.ZipFile(path) as archive:
        for info in zip_entries(archive):
            if info.is_dir():
                continue
            if info.filename.startswith('__MACOSX/') and platform != 'windows':
                if not PurePosixPath(info.filename).name.startswith('._') or info.file_size > 4 * 1024 * 1024:
                    raise ValidationError('Unexpected macOS resource metadata')
                if any(re.search(pattern, archive.read(info)) for pattern in SECRET_PATTERNS):
                    raise ValidationError('Potential credential in macOS metadata')
                continue
            if not info.filename.startswith(prefix):
                raise ValidationError('Unexpected application ZIP layout')
            name = info.filename[len(prefix):]
            parts = {part.casefold() for part in PurePosixPath(name).parts}
            if parts & FORBIDDEN_PARTS or PurePosixPath(name).name.casefold() in FORBIDDEN_FILES or PurePosixPath(name).suffix.lower() in {'.jar', '.class', '.mca', '.mcr', '.key', '.pfx', '.p12', '.jks', '.keystore'}:
                raise ValidationError('Forbidden game/private content in application ZIP')
            if stat.S_ISLNK(info.external_attr >> 16):
                link = archive.read(info).decode('utf-8')
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), link))
                if '\x00' in link or '\\' in link or ':' in link or link.startswith('/') or resolved == '..' or resolved.startswith('../') or len(link) > 4096:
                    raise ValidationError('Application ZIP symlink escapes bundle')
                links[name] = link
                continue
            digest = hashlib.sha256()
            output = None
            if name == member and extract:
                target_dir.mkdir(exist_ok=True)
                target = target_dir / f'{platform}-executable.bin'
                if target.exists() or target.is_symlink():
                    raise ValidationError('Executable extraction requires a fresh private path')
                output = target.open('xb')
                target.chmod(0o600)  # Data only: deliberately not executable.
            try:
                with archive.open(info) as stream:
                    previous = b''
                    while block := stream.read(1024 * 1024):
                        if any(re.search(pattern, previous + block) for pattern in SECRET_PATTERNS):
                            raise ValidationError('Potential credential in application ZIP')
                        previous = block[-8192:]
                        digest.update(block)
                        if output:
                            output.write(block)
                files[name] = digest.hexdigest()
            finally:
                if output:
                    output.close()
            if name == member and extract:
                extracted = Target(f'{platform} executable', target, files[name], info.file_size)
        if files != audit.get('files') or links != audit.get('symlinks'):
            raise ValidationError('Application ZIP inventory differs from the build content audit')
        if extract and extracted is None:
            raise ValidationError('Expected application executable is missing')
    return extracted


def verified_targets(workspace: Path, include_macos: bool) -> tuple[dict, list[Target]]:
    context = read_json(workspace / 'context.json')
    selected = select_artifacts(context['run'], context['artifacts'], str(context['run']['id']))
    for key, artifact in selected.items():
        checked_hash(workspace / f'{key}.zip', artifact['digest'].split(':', 1)[1], artifact['size_in_bytes'])
    source = context['run']['head_sha']
    pair = verify(workspace / 'windows', workspace / 'linux', source, workspace / 'macos-arm64', workspace / 'macos-x86_64')
    if pair != read_json(workspace / 'pair/release-pair.json'):
        raise ValidationError('Verified release-pair report does not match the downloaded candidates')
    targets = []
    # Fresh extraction directory each validation pass; it cannot address user files.
    target_dir = workspace / ('targets-' + uuid.uuid4().hex)
    for key, (_, filename) in PLATFORMS.items():
        record = read_json(workspace / key / 'artifact.json')
        path = workspace / key / filename
        checked_hash(path, record['sha256'], record['size'])
        targets.append(Target(filename, path, record['sha256'], record['size']))
        if key != 'linux':
            audit = read_json(workspace / key / 'content-audit.json')
            executable = audit_zip(path, audit, target_dir, key, key == 'windows' or include_macos)
            if executable:
                targets.append(executable)
    return context, targets


def prepare(workspace: Path, run_id: str, token: str) -> None:
    if not re.fullmatch(r'[1-9][0-9]{0,19}', run_id):
        raise ValidationError('Build run ID must be a positive integer')
    workspace.mkdir(parents=True, exist_ok=False)
    api = GitHub(token)
    run = api.get(f'actions/runs/{run_id}')
    artifacts = api.artifacts(run_id)
    selected = select_artifacts(run, artifacts, run_id)
    (workspace / 'context.json').write_text(json.dumps({'run': run, 'artifacts': artifacts,
        'scanner_commit': os.environ.get('SCANNER_SOURCE_SHA', 'local')}))
    for key, artifact in selected.items():
        wrapper = workspace / f'{key}.zip'
        api.download(artifact['id'], wrapper)
        checked_hash(wrapper, artifact['digest'].split(':', 1)[1], artifact['size_in_bytes'])
        allowed = {'release-pair.json'} if key == 'pair' else {PLATFORMS[key][1], 'artifact.json', 'content-audit.json', 'build-metadata.json'}
        unpack_wrapper(wrapper, workspace / key, allowed)
    verified_targets(workspace, False)


class Limiter:
    """Account for every attempt; <=3 requests in any rolling 60-second window."""
    def __init__(self, maximum: int = 30, clock: Callable = time.monotonic, sleep: Callable = time.sleep):
        self.maximum, self.clock, self.sleep = maximum, clock, sleep
        self.calls = 0
        self.deadline = self.clock() + 20 * 60
        # Serial workflow runs also leave a full interval before their first call.
        self.last = self.clock()
        self.starts: deque[float] = deque()

    def acquire(self) -> None:
        if self.calls >= self.maximum:
            raise QuotaStop('Per-run request budget exhausted')
        if self.clock() >= self.deadline:
            raise QuotaStop('Per-run time budget exhausted')
        now = self.clock()
        if self.last is not None and now < self.last + 21:
            self.sleep(self.last + 21 - now)
        if self.clock() >= self.deadline:
            raise QuotaStop('Per-run time budget exhausted')
        self.last = self.clock()
        self.starts.append(self.last)
        self.calls += 1


def retry_after(value: str | None, now: Callable = time.time) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = date.timestamp() - now()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, seconds) if seconds == seconds else None


class VirusTotal:
    def __init__(self, key: str, limiter: Limiter | None = None, opener=None, sleep: Callable = time.sleep):
        if not key:
            raise APIError('VIRUSTOTAL_API_KEY is not configured')
        self._key = key
        self.limiter = limiter or Limiter()
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.sleep = sleep

    def request(self, method: str, url: str, body_factory=None, headers: dict | None = None) -> dict:
        https_url(url, ('www.virustotal.com', 'virustotal.com'))
        for attempt in range(2):
            self.limiter.acquire()
            request = urllib.request.Request(url, method=method, data=body_factory() if body_factory else None,
                         headers={**(headers or {}), 'x-apikey': self._key, 'Accept': 'application/json'})
            try:
                with self.opener.open(request, timeout=120) as response:
                    data = json.loads(response.read(4 * 1024 * 1024))
                    if not isinstance(data, dict):
                        raise APIError('Invalid VirusTotal response')
                    return data
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and method == 'GET':
                    return {'missing': True}
                if exc.code == 429:
                    try:
                        error = json.loads(exc.read(16384)).get('error', {})
                    except (ValueError, AttributeError):
                        error = {}
                    delay = retry_after(exc.headers.get('Retry-After'))
                    daily = any(word in str(error.get('message', '')).casefold() for word in ('daily', 'monthly', 'per day', 'per month'))
                    if daily or delay is None or delay > 120 or attempt:
                        raise QuotaStop('VirusTotal quota/rate limit exhausted; remaining files were not requested') from None
                    self.sleep(delay)
                    continue
                if exc.code in (401, 403):
                    raise QuotaStop('VirusTotal authentication/permission rejected; remaining requests stopped') from None
                raise APIError(f'VirusTotal HTTP {exc.code}; no automatic upload retry') from None
            except (OSError, TimeoutError, ValueError):
                raise APIError('VirusTotal timeout/network/JSON error; submission outcome may be incomplete') from None
        raise QuotaStop('VirusTotal request retries exhausted')

    def lookup(self, digest: str) -> dict:
        if not SHA256.fullmatch(digest):
            raise ValidationError('Invalid lookup SHA-256')
        return self.request('GET', 'https://www.virustotal.com/api/v3/files/' + digest)

    def upload(self, target: Target, consent: bool = False) -> dict:
        if not consent:
            raise ValidationError('Explicit upload-missing consent is required')
        checked_hash(target.path, target.sha256, target.size)
        if target.size > MAX_VT_FILE:
            return {'too_large': True}
        url = 'https://www.virustotal.com/api/v3/files'
        if target.size > SMALL_UPLOAD:
            data = self.request('GET', url + '/upload_url')
            url = data.get('data', '')
            https_url(url, ('www.virustotal.com', 'virustotal.com'))
            if not urllib.parse.urlsplit(url).path.startswith(('/_ah/upload/', '/api/v3/files')):
                raise APIError('Unexpected VirusTotal large-upload endpoint')
        boundary = 'playden-' + uuid.uuid4().hex
        header = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="artifact.bin"\r\n'
                  'Content-Type: application/octet-stream\r\n\r\n').encode()
        tail = f'\r\n--{boundary}--\r\n'.encode()
        def body():
            yield header
            with target.path.open('rb') as stream:
                while block := stream.read(1024 * 1024):
                    yield block
            yield tail
        return self.request('POST', url, body, {'Content-Type': f'multipart/form-data; boundary={boundary}',
                                               'Content-Length': str(len(header) + target.size + len(tail))})


def existing_report(data: dict, digest: str) -> dict:
    if data.get('missing'):
        return {'status': 'missing', 'detections': None, 'stats': None}
    record = data.get('data', {})
    if not isinstance(record, dict):
        raise APIError('Invalid VirusTotal file object')
    if record.get('id') != digest or record.get('type') != 'file':
        raise APIError('VirusTotal report SHA-256/type mismatch')
    attributes = record.get('attributes', {})
    if not isinstance(attributes, dict):
        raise APIError('Invalid VirusTotal file attributes')
    stats = attributes.get('last_analysis_stats')
    if not isinstance(stats, dict) or not stats or not attributes.get('last_analysis_date'):
        return {'status': 'pending', 'detections': None, 'stats': None}
    allowed_stats = {'malicious', 'suspicious', 'undetected', 'harmless', 'timeout', 'confirmed-timeout', 'failure', 'type-unsupported'}
    stats = {key: value for key, value in stats.items() if key in allowed_stats}
    if not stats or any(type(value) is not int or value < 0 for value in stats.values()):
        raise APIError('Invalid VirusTotal detection counts')
    if type(attributes['last_analysis_date']) is not int or attributes['last_analysis_date'] < 0:
        raise APIError('Invalid VirusTotal analysis timestamp')
    detections = stats.get('malicious', 0) + stats.get('suspicious', 0)
    completed = sum(stats.get(key, 0) for key in ('malicious', 'suspicious', 'undetected', 'harmless'))
    status = 'detections' if detections else 'no-detections' if completed else 'incomplete'
    return {'status': status, 'detections': detections,
            'stats': stats, 'last_analysis_date': attributes['last_analysis_date']}


def scan(targets: list[Target], client: VirusTotal, mode: str = 'lookup-only') -> dict:
    if mode not in ('lookup-only', 'upload-missing'):
        raise ValidationError('Unknown manual scan mode')
    # Validate ALL bytes before making the first API call, including executable data.
    for target in targets:
        checked_hash(target.path, target.sha256, target.size)
    results, cache = [], {}
    stop = None
    quota_stopped = False
    for target in targets:
        row = {'name': target.name, 'sha256': target.sha256, 'size': target.size,
               'url': f'https://www.virustotal.com/gui/file/{target.sha256}/detection', 'uploaded': False}
        if stop:
            row.update(status='not-requested', reason=stop)
        else:
            try:
                if target.sha256 not in cache:
                    report = existing_report(client.lookup(target.sha256), target.sha256)
                    if report['status'] == 'missing' and mode == 'upload-missing':
                        upload = client.upload(target, consent=True)
                        if upload.get('too_large'):
                            report = {'status': 'too-large', 'detections': None, 'stats': None}
                        else:
                            analysis = upload.get('data', {})
                            if not isinstance(analysis, dict):
                                raise APIError('Invalid upload acknowledgement')
                            identifier = analysis.get('id', '')
                            if not isinstance(identifier, str) or client._key in identifier or analysis.get('type') != 'analysis' or not re.fullmatch(r'[A-Za-z0-9_+=/:-]{1,512}', identifier):
                                raise APIError('Invalid upload acknowledgement; submission may be incomplete')
                            report = {'status': 'queued', 'analysis_id': identifier, 'detections': None,
                                      'stats': None, 'uploaded': True}
                    cache[target.sha256] = report
                row.update(cache[target.sha256])
            except QuotaStop as exc:
                stop = str(exc)  # Fixed categories only; no response/header/key content.
                quota_stopped = True
                row.update(status='quota-stopped', reason=stop)
            except ValidationError:
                stop = 'Artifact changed before upload; remaining requests stopped'
                row.update(status='verification-stopped', reason=stop)
            except APIError as exc:
                row.update(status='error', reason=str(exc))
        results.append(row)
    return {'mode': mode, 'requests_made': client.limiter.calls, 'maximum_requests': client.limiter.maximum,
            'minimum_request_interval_seconds': 21, 'quota_stopped': quota_stopped, 'stop_reason': stop,
            'report_status': 'partial' if any(row['status'] in ('error', 'quota-stopped', 'not-requested', 'queued', 'pending', 'incomplete', 'verification-stopped') for row in results) else 'complete',
            'disclaimer': DISCLAIMER, 'results': results}


def write_report(directory: Path, report: dict, summary: Path | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'virustotal-report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    lines = ['# Manual VirusTotal artifact report', '', DISCLAIMER, '',
             f'Mode: **{report.get("mode", "not-started")}**; requests: {report.get("requests_made", 0)}.', '',
             f'Build run: {report.get("build_run_id", "unverified")}; source: `{report.get("source_commit", "unverified")}`.', '',
             '| File | SHA-256 | Status | Detections | Report |', '|---|---|---|---:|---|']
    for row in report.get('results', []):
        lines.append(f'| {row["name"]} | `{row["sha256"]}` | {row["status"]} | {row.get("detections", "pending")} | [VirusTotal]({row["url"]}) |')
    if report.get('error'):
        lines += ['', report['error']]
    if report.get('quota_stopped'):
        lines += ['', 'Further requests stopped due to quota/rate/auth limits; partial results are retained.']
    lines += ['', 'Queued/pending analyses are not repeatedly polled. Run lookup-only later to retrieve available results.']
    markdown = '\n'.join(lines) + '\n'
    (directory / 'virustotal-report.md').write_text(markdown)
    if summary:
        with summary.open('a') as stream:
            stream.write(markdown)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'scan'))
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--report-dir', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--mode', choices=('lookup-only', 'upload-missing'), default='lookup-only')
    parser.add_argument('--macos-executables', action='store_true')
    args = parser.parse_args()
    summary = Path(os.environ['GITHUB_STEP_SUMMARY']) if os.environ.get('GITHUB_STEP_SUMMARY') else None
    try:
        if args.command == 'prepare':
            prepare(args.workspace, args.run_id, os.environ.get('GH_TOKEN', ''))
            print('All four release candidates and ZIP inventories verified. No VirusTotal requests made.')
            return 0
        context, targets = verified_targets(args.workspace, args.macos_executables)
        client = VirusTotal(os.environ.get('VIRUSTOTAL_API_KEY', ''))
        report = scan(targets, client, args.mode)
        report.update(scanner_commit=context.get('scanner_commit', 'local'), build_run_id=str(context['run']['id']), source_commit=context['run']['head_sha'],
                      source_tree_sha256=read_json(args.workspace / 'pair/release-pair.json')['source_tree_sha256'])
        write_report(args.report_dir, report, summary)
        print(f'VirusTotal report saved; requests made: {client.limiter.calls}. No pending-analysis polling.')
        return 0
    except (ValidationError, APIError, OSError, ValueError, KeyError, zipfile.BadZipFile):
        # Do not log raw exceptions: network errors can contain signed URLs or tokens.
        write_report(args.report_dir, {'mode': args.mode, 'requests_made': 0, 'results': [],
                     'error': 'Artifact verification or scanner configuration failed. No scan was started; inspect the selected run and metadata.'}, summary)
        print('Artifact verification/configuration failed; secret values and raw network errors are suppressed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
