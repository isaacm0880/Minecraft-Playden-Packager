"""Host preparation Java detection, separate from the Windows PS5 runtime."""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from minecraft_playden import host_java
from minecraft_playden.operations import CancellationToken, OperationCancelled


def output(major=21, compiler=True, os_name=None):
    native = 'Windows 11' if os.name == 'nt' else 'Mac OS X' if sys.platform == 'darwin' else 'Linux'
    return (f'Property settings:\n    java.version = {major}.0.1\n    os.name = {os_name or native}\n'
            'java.base@21.0.1\n' + ('jdk.compiler@21.0.1\n' if compiler else '')).encode()


def executable(tmp_path):
    path = tmp_path / 'java'
    path.write_bytes(b'MZ\0\0' if os.name == 'nt' else b'\x7fELF')
    return path


def process(data):
    child = Mock(returncode=0)
    child.communicate.return_value = (data, None)
    child.poll.return_value = 0
    return child


def test_probe_validates_major_compiler_and_native_os_without_injected_options(tmp_path, monkeypatch):
    java = executable(tmp_path)
    for key in ('JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS', 'CLASSPATH'):
        monkeypatch.setenv(key, 'unwanted')
    with patch('minecraft_playden.host_java.subprocess.Popen', return_value=process(output())) as start:
        result = host_java.probe(java)
    assert result.executable == java and result.major == 21
    assert start.call_args.args[0] == [str(java), '-XshowSettings:properties', '--list-modules']
    assert all(key not in start.call_args.kwargs['env'] for key in ('JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS', 'CLASSPATH'))


@pytest.mark.parametrize('data,reason', [(output(8), '17'), (output(16), '17'), (output(21, False), 'compiler'),
                                        (output(os_name='Foreign OS'), 'native')])
def test_incompatible_java_is_rejected(tmp_path, data, reason):
    with patch('minecraft_playden.host_java.subprocess.Popen', return_value=process(data)):
        with pytest.raises(ValueError, match=reason):
            host_java.probe(executable(tmp_path))


@pytest.mark.skipif(os.name == 'nt', reason='Foreign Windows executable on a non-Windows host')
def test_windows_ps5_java_is_not_started_through_wine(tmp_path):
    path = tmp_path / 'java.exe'
    path.write_bytes(b'MZ\0\0')
    with patch('minecraft_playden.host_java.subprocess.Popen') as start:
        with pytest.raises(ValueError, match='host-native'):
            host_java.probe(path)
    start.assert_not_called()


def test_auto_discovery_skips_incompatible_java_but_manual_selection_does_not(tmp_path):
    first, second = tmp_path / 'java8', tmp_path / 'jdk21'
    with patch.object(host_java, 'candidates', return_value=[first, second]), patch.object(host_java, 'probe', side_effect=[ValueError('Java 8'), host_java.HostJdk(second, 21)]):
        assert host_java.find().executable == second
    with patch.object(host_java, 'candidates') as discover, patch.object(host_java, 'probe', side_effect=ValueError('no compiler')):
        with pytest.raises(ValueError, match='Host preparation JDK'):
            host_java.find(first)
        discover.assert_not_called()


@pytest.mark.parametrize('platform', ['linux', 'darwin', 'win32'])
def test_path_java_home_and_common_locations_are_bounded_and_deduplicated(tmp_path, platform):
    binary = 'java.exe' if platform == 'win32' else 'java'
    java = tmp_path / 'jdk/bin' / binary
    java.parent.mkdir(parents=True)
    java.write_bytes(b'fixture')
    env = {'JAVA_HOME': str(tmp_path / 'jdk'), 'PATH': '/unused'}
    packaged = tmp_path / 'packaged/java.exe'
    packaged.parent.mkdir()
    packaged.write_bytes(b'fixture')
    with patch('minecraft_playden.host_java.shutil.which', return_value=str(java)), patch.object(Path, 'glob', return_value=iter(())):
        found = host_java.candidates(packaged, platform=platform, environ=env, home=tmp_path)
    assert found.count(java) == 1
    assert (packaged in found) == (platform == 'win32')
    # Common installation locations are consulted even if PATH/JAVA_HOME are absent.
    with patch('minecraft_playden.host_java.shutil.which', return_value=None), patch.object(Path, 'glob', side_effect=lambda pattern: iter([java])) as locations:
        assert java in host_java.candidates(platform=platform, environ={'ProgramFiles': str(tmp_path / 'install')} if platform == 'win32' else {}, home=tmp_path)
        assert locations.called


def test_probe_cancellation_and_timeout_reap_the_child(tmp_path):
    java = executable(tmp_path)
    child = Mock(returncode=None)
    child.poll.return_value = None
    child.communicate.side_effect = [subprocess.TimeoutExpired('java', .1), (b'', None)]
    with patch('minecraft_playden.host_java.subprocess.Popen', return_value=child), patch('minecraft_playden.host_java.time.monotonic', side_effect=[0, 1, 20]):
        with pytest.raises(ValueError, match='timed out'):
            host_java.probe(java)
    child.kill.assert_called_once()
    token = CancellationToken()
    token.cancel()
    with patch('minecraft_playden.host_java.subprocess.Popen') as start:
        with pytest.raises(OperationCancelled):
            host_java.probe(java, token)
        start.assert_not_called()


@pytest.mark.skipif(not shutil.which('java'), reason='No local Java for native probe integration')
def test_real_native_jdk_probe():
    try:
        result = host_java.probe(Path(shutil.which('java')))
    except (OSError, ValueError) as exc:
        pytest.skip(f'Host has no full native JDK 17+: {exc}')
    assert result.major >= 17 and result.executable.is_file()
