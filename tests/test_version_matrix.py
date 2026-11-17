"""Actual upstream metadata, synthetic payloads; no PS5/runtime compatibility claim."""
import copy
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from minecraft_playden import arguments, assets, dependencies, fabric, runtime
from minecraft_playden.classpath import CRITICAL_CLASSES
from minecraft_playden.compatibility import Status, assess, assess_metadata
from minecraft_playden.desktop import LocalBuildRequest
from minecraft_playden.discovery import discover
from minecraft_playden.downloads import Downloader
from minecraft_playden.exporter import build
from minecraft_playden.metadata import java_major, normalize
from minecraft_playden.sources import ClientSource
from minecraft_playden.validator import validate
from minecraft_playden.versions import MANIFEST, VersionResolver
from runtime_fixtures import runtime as fake_runtime
from test_packager import archive

FIXTURES = Path(__file__).parent / 'fixtures/versions'
VERSIONS = ['1.18.2', '1.19.2', '1.20.1', '1.21.1', '1.21.11', '26.1.2', '26.2', '26.3']
MAJORS = [17, 17, 17, 21, 21, 25, 25, 25]


def put(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


def spec_payload(spec, payload, blobs):
    spec['sha1'] = hashlib.sha1(payload).hexdigest()
    spec['size'] = len(payload)
    blobs[spec['url']] = payload


def installation(root, version, modded, layout, legacy_assets=False, loader_override=None):
    """Keep upstream rules, coordinates, arguments, natives, inheritance and Java."""
    metadata = json.loads((FIXTURES / f'{version}.json').read_text())
    if legacy_assets:
        metadata.pop('arguments')
        metadata['minecraftArguments'] = '--username ${auth_player_name} --gameDir ${game_directory} --assetsDir ${game_assets}'
    blobs = {}
    client = archive({metadata['mainClass'].replace('.', '/') + '.class': b'synthetic Minecraft entry point'})
    spec_payload(metadata['downloads']['client'], client, blobs)
    for spec, native, _ in dependencies.resolve(metadata):
        if native:
            payload = archive({'windows/x64/' + Path(spec['path']).stem + '.dll': b'synthetic Windows native'})
        else:
            coordinate = next(':'.join(lib['name'].split(':')[:2]) for lib in metadata['libraries']
                              if lib.get('downloads', {}).get('artifact') is spec)
            payload = archive({name: b'synthetic class' for name in CRITICAL_CLASSES.get(coordinate, ('fixture.class',))})
        spec_payload(spec, payload, blobs)
        put(root, 'libraries/' + spec['path'], payload)
    sound = b'synthetic asset'
    sha = hashlib.sha1(sound).hexdigest()
    index = json.dumps({'virtual': legacy_assets, 'map_to_resources': legacy_assets,
                        'objects': {'minecraft/sounds/fixture.ogg': {'hash': sha, 'size': len(sound)}}}).encode()
    spec_payload(metadata['assetIndex'], index, blobs)
    put(root, f'assets/indexes/{metadata["assetIndex"]["id"]}.json', index)
    blobs[f'https://resources.download.minecraft.net/{sha[:2]}/{sha}'] = sound
    put(root, f'assets/objects/{sha[:2]}/{sha}', sound)
    spec_payload(metadata['logging']['client']['file'], b'<Configuration/>', blobs)
    put(root, 'assets/log_configs/' + metadata['logging']['client']['file']['id'], b'<Configuration/>')
    loader = '0.16.10' if version in ('1.18.2', '1.19.2', '1.20.1', '1.21.1') else '0.20.1'
    loader = loader_override or loader
    profile = json.loads((FIXTURES / f'fabric-{version}-{loader}.json').read_text())
    for raw in profile['libraries']:
        value = fabric.library(raw)
        path = value['downloads']['artifact']['path']
        if raw['name'].startswith('net.fabricmc:fabric-loader:'):
            members = {profile['mainClass'].replace('.', '/') + '.class': b'synthetic Knot',
                       'net/fabricmc/loader/api/FabricLoader.class': b'synthetic API'}
        elif raw['name'].startswith('net.fabricmc:intermediary:'):
            members = {'mappings/mappings.tiny': b'synthetic mappings'}
        else:
            members = {'fixture.class': b'synthetic loader dependency'}
        payload = archive(members)
        raw['sha1'] = hashlib.sha1(payload).hexdigest()
        raw['size'] = len(payload)
        put(root, 'libraries/' + path, payload)
    # Older metadata repeats the same Java artifact beside a native map.
    # Every copy must retain the same synthetic payload checksum.
    for lib in metadata['libraries']:
        for spec in [lib.get('downloads', {}).get('artifact'), *lib.get('downloads', {}).get('classifiers', {}).values()]:
            if spec is not None and spec.get('url') in blobs:
                spec_payload(spec, blobs[spec['url']], blobs)
    base_bytes = json.dumps(metadata).encode()
    if layout == 'official':
        put(root, f'versions/{version}/{version}.json', base_bytes)
        put(root, f'versions/{version}/{version}.jar', client)
        instance = root / 'versions' / (profile['id'] if modded else version)
        if modded:
            put(instance, f'{instance.name}.json', json.dumps(profile))
        game = root
    else:
        instance = root / 'instances/Matrix'
        components = [{'uid': 'net.minecraft', 'version': version}]
        if modded:
            components.append({'uid': fabric.FABRIC, 'version': loader})
            component = {'uid': fabric.FABRIC, 'version': loader, 'order': 10,
                         'mainClass': profile['mainClass'], 'libraries': profile['libraries'],
                         'arguments': profile.get('arguments', {}),
                         'requires': [{'uid': 'net.minecraft', 'equals': version}]}
            put(root, f'meta/{fabric.FABRIC}/{loader}.json', json.dumps(component))
        put(instance, 'mmc-pack.json', json.dumps({'components': components}))
        put(root, 'prismlauncher.cfg' if layout == 'prism' else 'multimc.cfg', '[General]\n')
        put(root, f'libraries/com/mojang/minecraft/{version}/minecraft-{version}-client.jar', client)
        game = instance / '.minecraft'
    put(game, 'saves/World/level.dat', b'world')
    put(game, 'options.txt', b'ordinary settings')
    if modded:
        put(game, 'mods/example.jar', archive({'fabric.mod.json': b'{"id":"fixture"}'}))
    manifest = {'versions': [{'id': version, 'type': metadata['type'], 'url': 'https://fixture/base',
                               'sha1': hashlib.sha1(base_bytes).hexdigest()}]}
    blobs[MANIFEST] = json.dumps(manifest).encode()
    blobs['https://fixture/base'] = base_bytes
    fake_runtime(root / f'runtime/component/windows-x64/jdk-{java_major(metadata)}', java_major(metadata))
    return instance, metadata, blobs, client


@pytest.mark.parametrize('layout', ['official', 'prism', 'multimc'])
@pytest.mark.parametrize('modded', [False, True], ids=['vanilla', 'fabric'])
@pytest.mark.parametrize('version,major', list(zip(VERSIONS, MAJORS)))
def test_packaging_matrix(tmp_path, layout, modded, version, major):
    launcher = tmp_path / 'source'
    instance, metadata, blobs, client = installation(launcher, version, modded, layout)
    requested = []
    def request(req, **kwargs):
        requested.append(req.full_url)
        assert req.full_url in blobs, f'Unexpected network access: {req.full_url}'
        assert req.full_url != metadata['downloads']['client']['url'], 'A local client must never be downloaded'
        return io.BytesIO(blobs[req.full_url])
    if layout == 'official':
        from minecraft_playden.discovery import Installation
        from minecraft_playden.modloaders import ModLoader
        loader = '0.16.10' if major in (17, 21) and version != '1.21.11' else '0.20.1'
        selected = Installation('Matrix', 'Official launcher', version, instance, launcher / f'versions/{version}/{version}.jar',
                                launcher_root=launcher, libraries_root=launcher / 'libraries',
                                loaders=(ModLoader('Fabric', loader),) if modded else ())
    else:
        selected = next(item for item in discover([launcher], include_defaults=False).installations if item.instance == instance)
    output = tmp_path / 'Package With Spaces'
    with patch('urllib.request.urlopen', side_effect=request):
        LocalBuildRequest(selected, output, tmp_path / 'cache').run()
    assert validate(output) == []
    record = json.loads((output / 'metadata/runtime.json').read_text())
    assert record['major'] == major and record['target'] == 'windows-x64'
    assert record['source'] == 'local-or-cache'
    tokens = arguments.parse_argfile((output / 'playden.args').read_text())
    cp = arguments.classpath_argument(tokens, 'net.fabricmc.loader.impl.launch.knot.KnotClient' if modded else metadata['mainClass'])
    assert ';' in cp and 'natives-linux' not in cp and 'natives-macos' not in cp
    assert any(t.startswith('-Dlog4j.configurationFile=../../assets/log_configs/') for t in tokens)
    assert (output / 'instances/default/minecraft/saves/World/level.dat').read_bytes() == b'world'
    assert (output / 'instances/default/minecraft/options.txt').read_bytes() == b'ordinary settings'
    assert all('adoptium' not in url for url in requested)
    # Local verified dependencies and even official version JSON seed the cache.
    assert requested == ([MANIFEST] if layout == 'official' else [MANIFEST, 'https://fixture/base'])
    # Populate manifest once; an entire second build must work with networking disabled.
    offline = Downloader(tmp_path / 'cache', offline=True)
    source = ClientSource('local', version=version, instance=instance,
                          launcher_root=launcher, libraries_root=launcher / 'libraries')
    with patch('urllib.request.urlopen', side_effect=AssertionError('Offline build attempted network')):
        build(tmp_path / 'Offline Package', offline, source)
    assert validate(tmp_path / 'Offline Package') == []
    status = selected.compatibility.status
    assert status == (Status.TESTED if version == '26.3' or (version == '1.20.1' and not modded) else Status.UNTESTED)
    # Rehashing an incorrect runtime cannot bypass independent target validation.
    runtime_file = output / 'java/release'
    runtime_file.write_text(runtime_file.read_text().replace('Windows', 'Linux'))
    inventory = json.loads((output / 'package.json').read_text())
    inventory['files']['java/release'] = hashlib.sha256(runtime_file.read_bytes()).hexdigest()
    (output / 'package.json').write_text(json.dumps(inventory))
    assert any('Windows x64' in error for error in validate(output))


def test_snapshot_provenance_and_real_java_requirements():
    provenance = json.loads((FIXTURES / 'provenance.json').read_text())
    for entry in provenance['sources']:
        assert hashlib.sha256((FIXTURES / entry['file']).read_bytes()).hexdigest() == entry['sha256']
    for version, major in zip(VERSIONS, MAJORS):
        raw = json.loads((FIXTURES / f'{version}.json').read_text())
        assert java_major(raw) == major
        assert assess_metadata(raw).can_package
        assert assess_metadata(raw).release_type == 'release'
    old = json.loads((FIXTURES / '1.16.5.json').read_text())
    assert java_major(old) == 8
    capability = assess_metadata(old)
    assert not capability.can_package and 'argument files require Java 9+' in capability.problem


def test_legacy_arguments_and_future_unknown_version_are_metadata_driven():
    raw = json.loads((FIXTURES / '1.18.2.json').read_text())
    raw['id'] = 'future-synthetic-snapshot'
    raw['type'] = 'snapshot'
    raw.pop('arguments')
    raw['minecraftArguments'] = '--username ${auth_player_name} --gameDir "${game_directory}" --assetsDir ${game_assets}'
    adapted = normalize(raw)
    assert assess_metadata(adapted).can_package
    assert assess(raw['id'], ()).status == Status.UNTESTED
    tokens = arguments.parse_argfile(arguments.generate(adapted, ['versions/client.jar'], '../../'))
    assert tokens[tokens.index('--username') + 1] == 'Player'
    assert tokens[tokens.index('--assetsDir') + 1] == '../../assets/virtual/' + raw['assetIndex']['id']


def test_bounded_version_inheritance_and_overrides(tmp_path):
    parent = json.loads((FIXTURES / '1.18.2.json').read_text())
    parent['id'] = 'parent'
    child = {'id': 'child', 'inheritsFrom': 'parent', 'type': 'snapshot',
             'javaVersion': {'majorVersion': 21}, 'arguments': {'game': ['--custom', 'yes']},
             'libraries': [{'name': parent['libraries'][0]['name'].rsplit(':', 1)[0] + ':replacement',
                            'downloads': {'artifact': {'path': 'replacement.jar', 'url': 'https://fixture/replacement'}}}]}
    docs = {'parent': parent, 'child': child}
    downloader = Downloader(tmp_path)
    def resolve_json(spec, **kwargs):
        return {'versions': [{'id': key, 'url': key} for key in docs]} if spec['url'] == MANIFEST else copy.deepcopy(docs[spec['url']])
    with patch.object(downloader, 'json', side_effect=resolve_json):
        resolved = VersionResolver(downloader).resolve('child')
        assert java_major(resolved) == 21
        assert resolved['id'] == 'child' and resolved['type'] == 'snapshot'
        assert resolved['arguments']['game'][-2:] == ['--custom', 'yes']
        assert resolved['downloads']['client'] == parent['downloads']['client']
        assert resolved['libraries'][0]['downloads']['artifact']['path'] == 'replacement.jar'
        docs['parent']['inheritsFrom'] = 'child'
        with pytest.raises(ValueError, match='Cyclic'):
            VersionResolver(downloader).resolve('child')


def test_virtual_resource_assets_are_materialized_and_validated(tmp_path):
    data = b'synthetic asset'
    sha = hashlib.sha1(data).hexdigest()
    index = {'virtual': True, 'map_to_resources': True, 'objects': {'sounds/test.ogg': {'hash': sha, 'size': len(data)}}}
    put(tmp_path, f'assets/objects/{sha[:2]}/{sha}', data)
    assets.materialize(tmp_path, index, 'legacy', 'instances/default/minecraft')
    assert assets.validate(tmp_path, index, 'legacy', 'instances/default/minecraft') == []
    put(tmp_path, 'assets/virtual/legacy/sounds/test.ogg', b'tampered')
    assert assets.validate(tmp_path, index, 'legacy', 'instances/default/minecraft')
    for name in ('../../escape', '/absolute', 'C:/drive', '..\\escape'):
        index['objects'] = {name: {'hash': sha, 'size': len(data)}}
        with pytest.raises(ValueError, match='Unsafe'):
            assets.materialize(tmp_path, index, 'legacy', 'instances/default/minecraft')


def test_legacy_metadata_and_asset_views_pass_final_package_validation(tmp_path):
    launcher = tmp_path / 'source'
    instance, metadata, blobs, _ = installation(launcher, '1.18.2', False, 'official', legacy_assets=True)
    def request(req, **kwargs):
        return io.BytesIO(blobs[req.full_url])
    output = tmp_path / 'legacy-assets'
    with patch('urllib.request.urlopen', side_effect=request):
        build(output, Downloader(tmp_path / 'cache'), ClientSource('local', instance=instance))
    assert validate(output) == []
    inventory = json.loads((output / 'package.json').read_text())
    mapped = 'instances/default/minecraft/resources/minecraft/sounds/fixture.ogg'
    assert mapped in inventory['files']
    (output / mapped).unlink()
    assert any('asset' in error.lower() or mapped in error for error in validate(output))


def test_pre_adapter_packages_keep_their_original_arguments_and_logging_policy(tmp_path):
    launcher = tmp_path / 'source'
    instance, metadata, blobs, _ = installation(launcher, '26.3', False, 'official')
    output = tmp_path / 'old-format'
    with patch('urllib.request.urlopen', side_effect=lambda req, **kw: io.BytesIO(blobs[req.full_url])):
        build(output, Downloader(tmp_path / 'cache'), ClientSource('local', instance=instance))
    inventory = json.loads((output / 'package.json').read_text())
    old = arguments.generate(metadata, inventory['classpath'], '../../', metadata_adapter=False)
    (output / 'playden.args').write_text(old)
    inventory['files']['playden.args'] = hashlib.sha256(old.encode()).hexdigest()
    # Earlier versions did not include Vanilla logging or runtime target evidence.
    logging = 'assets/log_configs/' + metadata['logging']['client']['file']['id']
    (output / logging).unlink()
    del inventory['files'][logging]
    (output / 'metadata/runtime.json').write_text('{"major":25}')
    inventory['files']['metadata/runtime.json'] = hashlib.sha256((output / 'metadata/runtime.json').read_bytes()).hexdigest()
    (output / 'package.json').write_text(json.dumps(inventory))
    assert validate(output) == []


def test_unknown_placeholders_and_malformed_runtime_fail_capability_preflight():
    metadata = json.loads((FIXTURES / '1.20.1.json').read_text())
    metadata['arguments']['game'].append('${unimplemented_future_feature}')
    result = assess_metadata(metadata)
    assert not result.can_package and 'Unsupported launch placeholder' in result.problem
    for major in (True, '17', None, 7):
        metadata['javaVersion']['majorVersion'] = major
        assert not assess_metadata(metadata).can_package


def test_fabric_java_override_and_mapping_requirements_follow_profile_metadata():
    metadata = normalize(json.loads((FIXTURES / '1.20.1.json').read_text()))
    profile = json.loads((FIXTURES / 'fabric-1.20.1-0.16.10.json').read_text())
    profile['javaVersion'] = {'majorVersion': 21}
    data = {'schema': 1, 'minecraft': metadata['id'], 'loader': '0.16.10', 'profiles': [profile]}
    effective = fabric.derive(metadata, data)
    assert java_major(effective) == 21 and java_major(metadata) == 17
    profile['javaVersion']['majorVersion'] = 8
    assert java_major(fabric.derive(metadata, data)) == 17
    # An unknown future identity with no declared mapping dependency must not
    # acquire an invented intermediary JAR from a Minecraft version-name rule.
    metadata['id'] = '1.future-unobfuscated-synthetic'
    profile['inheritsFrom'] = metadata['id']
    profile['libraries'] = [lib for lib in profile['libraries'] if not lib['name'].startswith('net.fabricmc:intermediary:')]
    data['minecraft'] = metadata['id']
    assert not any(lib['name'].startswith('net.fabricmc:intermediary:') for lib in fabric.derive(metadata, data)['libraries'])
    profile['requires'] = [{'uid': fabric.INTERMEDIARY}]
    with pytest.raises(ValueError, match='Required component'):
        fabric.derive(metadata, data)
