"""Captured classifier metadata; no Minecraft binaries or Java execution."""
import copy
import json
from pathlib import Path

import pytest

from minecraft_playden import dependencies, fabric, metadata

FIXTURES = Path(__file__).parent / 'fixtures/fabric/native-maps'


def captured(kind, version):
    return json.loads((FIXTURES / f'{kind}-{version}.json').read_text())


@pytest.mark.parametrize('version', ['1.14.4', '1.16', '1.18.2'])
def test_captured_profiles_resolve_windows_natives(version):
    base = metadata.normalize(captured('mojang', version))
    component = captured('prism', version)
    profile = captured('fabric', version)
    data = {'schema': 1, 'minecraft': version, 'loader': '0.20.1', 'profiles': [component, profile]}
    effective = fabric.derive(base, data)
    resolved = dependencies.resolve(effective)
    speech = [(spec, native) for spec, native, _ in resolved if '/text2speech/' in spec['path']]
    assert len(speech) == 2
    assert [native for _, native in speech] == [False, True]
    assert speech[1][0]['path'].endswith('-natives-windows.jar')
    official = {spec['path']: spec for spec, _, _ in dependencies.resolve(base)}
    assert all(spec == official[spec['path']] for spec, _ in speech)
    assert not any(tag in spec['path'] for spec, _, _ in resolved for tag in ('natives-linux', 'natives-macos', 'arm64'))
    if version in ('1.14.4', '1.16'):
        with pytest.raises(ValueError, match='Java 8'):
            metadata.check_runtime_requirement(effective)
    else:
        assert metadata.check_runtime_requirement(effective) == 17


def native_library():
    return copy.deepcopy(next(lib for lib in captured('prism', '1.14.4')['libraries']
                              if lib.get('natives', {}).get('windows')))


def test_arch_substitution_and_native_only_artifacts():
    raw = native_library()
    raw['downloads'].pop('artifact')
    spec = raw['downloads']['classifiers'].pop('natives-windows')
    spec['url'] = spec['url'].replace('natives-windows', 'natives-windows-64')
    raw['downloads']['classifiers']['natives-windows-64'] = spec
    raw['natives']['windows'] = 'natives-windows-${arch}'
    result = dependencies.resolve({'libraries': [fabric.library(raw)]})
    assert len(result) == 1 and result[0][1]
    assert result[0][0]['path'].endswith('-natives-windows-64.jar')


@pytest.mark.parametrize('mutation', [
    lambda lib: lib.update(natives=[]),
    lambda lib: lib['natives'].update(windows='missing'),
    lambda lib: lib['natives'].update(windows='natives-linux'),
    lambda lib: lib['downloads'].update(classifiers=[]),
    lambda lib: lib['downloads']['classifiers'].update({'../escape': {}}),
    lambda lib: lib['downloads']['classifiers'].update({'natives-windows': None}),
    lambda lib: lib['downloads']['classifiers']['natives-windows'].update(path='../escape.jar'),
    lambda lib: lib['downloads']['classifiers']['natives-windows'].update(url='https://libraries.minecraft.net/other.jar'),
    lambda lib: lib['downloads']['classifiers']['natives-windows'].update(url='http://libraries.minecraft.net/other.jar'),
    lambda lib: lib['downloads']['classifiers']['natives-windows'].update(sha1='bad'),
    lambda lib: lib['downloads']['classifiers']['natives-windows'].update(size=True),
    lambda lib: lib.update(extract={'exclude': [42]}),
])
def test_reject_invalid_classifier_metadata(mutation):
    raw = native_library()
    mutation(raw)
    with pytest.raises(ValueError):
        fabric.library(raw)


def test_duplicate_maps_coalesce_but_conflicts_fail():
    raw = native_library()
    plain = copy.deepcopy(raw)
    plain.pop('natives')
    plain['downloads'].pop('classifiers')
    merged = fabric.merge_libraries([[], [plain, raw]])
    assert len(merged) == 1
    assert len(dependencies.resolve({'libraries': merged})) == 2
    conflict = copy.deepcopy(raw)
    conflict['downloads']['classifiers']['natives-windows']['sha1'] = '0' * 40
    with pytest.raises(ValueError, match='Conflicting native classifier'):
        fabric.merge_libraries([[], [raw, conflict]])


def installation(tmp_path, raw):
    loader = {'name': 'net.fabricmc:fabric-loader:0.20.1'}
    profiles = [{'mainClass': 'net.fabricmc.loader.impl.launch.knot.KnotClient', 'libraries': [loader, raw]}]
    _, loader_path = fabric.coordinate(loader['name'])
    path = tmp_path / loader_path
    path.parent.mkdir(parents=True)
    path.write_bytes(b'loader')
    return fabric.FabricInstallation('test', '0.20.1', tmp_path / 'client.jar', tmp_path,
                                     profiles, (tmp_path / 'component.json',), '')


def test_preflight_checks_selected_native_not_other_platforms(tmp_path):
    raw = native_library()
    raw['downloads'].pop('artifact')
    spec = raw['downloads']['classifiers']['natives-windows']
    spec.pop('sha1')  # Unpinned dependencies must already be installed.
    source = installation(tmp_path, raw)
    with pytest.raises(ValueError, match='Fabric library missing: .*natives-windows.jar'):
        source.check_local_requirements()
    normalized = fabric.library(raw)
    path = tmp_path / normalized['downloads']['classifiers']['natives-windows']['path']
    path.parent.mkdir(parents=True)
    path.write_bytes(b'native')
    source.check_local_requirements()  # No Linux file is required.


def test_plan_rejects_native_checksum_mismatch(tmp_path):
    from minecraft_playden.downloads import Downloader
    raw = native_library()
    raw['downloads'].pop('artifact')
    source = installation(tmp_path, raw)
    spec = fabric.library(raw)['downloads']['classifiers']['natives-windows']
    path = tmp_path / spec['path']
    path.parent.mkdir(parents=True)
    path.write_bytes(b'corrupt native')
    base = {'id': 'test', 'mainClass': 'net.minecraft.client.main.Main',
            'libraries': [], 'arguments': {'jvm': [], 'game': []}}
    with pytest.raises(ValueError, match='Fabric library checksum mismatch: .*natives-windows.jar'):
        source.plan(base, Downloader(tmp_path / 'cache'))
