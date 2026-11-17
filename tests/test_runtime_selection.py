"""Runtime policy and target verification without running a Java executable."""
import hashlib
import io
import json
from unittest.mock import patch

import pytest

from minecraft_playden import runtime
from minecraft_playden.downloads import Downloader
from runtime_fixtures import pe, runtime as fake_runtime
from test_packager import archive


@pytest.mark.parametrize('major', [17, 21, 25])
def test_local_matching_windows_runtime_preferred_over_download(tmp_path, major):
    wrong = fake_runtime(tmp_path / 'wrong', 21 if major == 17 else 17)
    host = fake_runtime(tmp_path / 'host', major, os_name='Linux')
    right = fake_runtime(tmp_path / 'right', major)
    downloader = Downloader(tmp_path / 'cache', runtime_paths=(wrong, host, right), allow_runtime_download=True)
    with patch('urllib.request.urlopen', side_effect=AssertionError('Local runtime must avoid network')):
        result = runtime.install({'javaVersion': {'majorVersion': major}}, downloader, tmp_path / 'selected')
    assert result['major'] == major and result['target'] == 'windows-x64'
    assert result['source'] == 'local-or-cache'


def test_cached_runtime_and_explicit_offline_policy(tmp_path):
    downloader = Downloader(tmp_path / 'cache', offline=True)
    fake_runtime(downloader.cache / 'runtimes/java-21-windows-x64', 21)
    with patch('urllib.request.urlopen', side_effect=AssertionError('Offline runtime attempted network')):
        runtime.install({'javaVersion': {'majorVersion': 21}}, downloader, tmp_path / 'selected')
        with pytest.raises(ValueError, match='Missing compatible Windows x64 Java 17'):
            runtime.install({'javaVersion': {'majorVersion': 17}}, downloader, tmp_path / 'missing')


@pytest.mark.parametrize('os_name,arch,machine', [('Linux', 'x86_64', 0x8664), ('Darwin', 'aarch64', 0xAA64),
                                                ('Windows', 'aarch64', 0xAA64), ('Windows', 'x86_64', 0x14C)])
def test_wrong_target_is_rejected_without_running_or_downloading(tmp_path, os_name, arch, machine):
    candidate = fake_runtime(tmp_path / 'wrong', 17, os_name, arch, machine)
    with pytest.raises(ValueError, match='Windows'):
        runtime.inspect(candidate, 17)
    downloader = Downloader(tmp_path / 'cache', runtime_paths=(candidate,))
    with patch('urllib.request.urlopen', side_effect=AssertionError('Runtime download lacked permission')):
        with pytest.raises(ValueError, match='explicitly allow runtime downloads'):
            runtime.install({'javaVersion': {'majorVersion': 17}}, downloader, tmp_path / 'selected')


def test_java8_reports_argument_file_blocker_before_any_network(tmp_path):
    fake_runtime(tmp_path / 'java8', 8)
    assert runtime.inspect(tmp_path / 'java8', 8)['major'] == 8
    downloader = Downloader(tmp_path / 'cache', runtime_paths=(tmp_path / 'java8',), allow_runtime_download=True)
    with patch('urllib.request.urlopen', side_effect=AssertionError('Java8 must fail locally')):
        with pytest.raises(ValueError, match='argument files require Java 9+'):
            runtime.install({'javaVersion': {'majorVersion': 8}}, downloader, tmp_path / 'selected')


def test_runtime_download_requires_permission_and_verified_archive_is_reused_offline(tmp_path):
    data = archive({'jdk/bin/java.exe': pe(), 'jdk/bin/server/jvm.dll': pe(),
                    'jdk/release': b'JAVA_VERSION="21.0.1"\nOS_NAME="Windows"\nOS_ARCH="x86_64"'})
    package = {'link': 'https://fixture/runtime.zip', 'checksum': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    catalog = json.dumps([{'binary': {'package': package}, 'release_name': 'jdk-21'}]).encode()
    metadata = {'javaVersion': {'majorVersion': 21}}
    downloader = Downloader(tmp_path / 'cache')
    with patch('urllib.request.urlopen', side_effect=AssertionError('No runtime permission')):
        with pytest.raises(ValueError, match='Missing compatible Windows x64 Java 21'):
            runtime.install(metadata, downloader, tmp_path / 'refused')
    downloader.allow_runtime_download = True
    def request(req, **kwargs):
        return io.BytesIO(catalog if req.full_url.startswith('https://api.adoptium.net/') else data)
    with patch('urllib.request.urlopen', side_effect=request) as network:
        selected = runtime.install(metadata, downloader, tmp_path / 'selected')
        assert network.call_count == 2
    assert selected['major'] == 21 and selected['target'] == 'windows-x64'
    with patch('urllib.request.urlopen', side_effect=AssertionError('Cached runtime must not access network')):
        runtime.install(metadata, Downloader(tmp_path / 'cache', offline=True), tmp_path / 'offline')


def test_corrupt_runtime_archive_is_not_used_or_silently_repaired_without_permission(tmp_path):
    downloader = Downloader(tmp_path / 'cache')
    url = ('https://api.adoptium.net/v3/assets/latest/17/hotspot'
           '?architecture=x64&image_type=jdk&os=windows&vendor=eclipse')
    package = {'link': 'https://fixture/runtime.zip', 'checksum': '0' * 64, 'size': 3}
    (downloader.cache / hashlib.sha256(url.encode()).hexdigest()).write_text(json.dumps([{'binary': {'package': package}}]))
    (downloader.cache / hashlib.sha256(package['link'].encode()).hexdigest()).write_bytes(b'bad')
    with patch('urllib.request.urlopen', side_effect=AssertionError('No permission to repair runtime')):
        with pytest.raises(ValueError, match='Missing compatible Windows x64 Java 17'):
            runtime.install({'javaVersion': {'majorVersion': 17}}, downloader, tmp_path / 'selected')


def test_runtime_download_progress_and_cached_rebuild_are_preserved(tmp_path):
    data = archive({'jdk/bin/java.exe': pe(), 'jdk/bin/server/jvm.dll': pe(),
                    'jdk/release': b'JAVA_VERSION="21.0.1"\nOS_NAME="Windows"\nOS_ARCH="x86_64"'})
    package = {'link': 'https://fixture/runtime.zip', 'checksum': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    catalog = json.dumps([{'binary': {'package': package}}]).encode()
    downloader = Downloader(tmp_path / 'cache', allow_runtime_download=True)
    events = []
    downloader.progress = events.append
    def request(req, **kwargs):
        return io.BytesIO(catalog if req.full_url.startswith('https://api.adoptium.net/') else data)
    with patch('urllib.request.urlopen', side_effect=request):
        runtime.install({'javaVersion': {'majorVersion': 21}}, downloader, tmp_path / 'selected')
    assert events[0].stage == 'runtime-download' and events[0].bytes_copied == 0
    assert events[-1].bytes_copied == events[-1].bytes_total == len(data)
    events.clear()
    with patch('urllib.request.urlopen', side_effect=AssertionError('Cache must be preferred')):
        runtime.install({'javaVersion': {'majorVersion': 21}}, downloader, tmp_path / 'rebuild')
    assert not events
