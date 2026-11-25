"""Official legacy metadata, synthetic bytecode: no Minecraft code executes."""
import copy
from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
import struct
from unittest.mock import patch
import zipfile

import pytest

from minecraft_playden import arguments, entrypoints, java8
from minecraft_playden.compatibility import Status, assess, assess_metadata
from minecraft_playden.dependencies import resolve
from minecraft_playden.downloads import Downloader
from minecraft_playden.exporter import build
from minecraft_playden.metadata import normalize
from minecraft_playden.sources import ClientSource, verify_client
from minecraft_playden.validator import validate
from minecraft_playden.versions import MANIFEST
from runtime_fixtures import runtime
from test_packager import archive

FIXTURES = Path(__file__).parent / 'fixtures/legacy'


def java_class(name, *, flags=0x0009, directory=True, method='main', descriptor='([Ljava/lang/String;)V', body=b'\xb1'):
    """An owned minimal Java 8 class with a return-only main method."""
    def u2(n): return struct.pack('>H', n)
    def u4(n): return struct.pack('>I', n)
    strings = [name.replace('.', '/'), 'java/lang/Object', method, descriptor, 'Code', 'gameDirectory', 'Ljava/io/File;']
    pool = b''.join(b'\x01' + u2(len(s.encode())) + s.encode() for s in strings)
    pool += b'\x07' + u2(1) + b'\x07' + u2(2)
    field = u2(0x0008) + u2(6) + u2(7) + u2(0)
    code = u2(0) + u2(1) + u4(len(body)) + body + u2(0) + u2(0)
    member = u2(flags) + u2(3) + u2(4) + u2(1) + u2(5) + u4(len(code)) + code
    return (b'\xca\xfe\xba\xbe' + u2(0) + u2(52) + u2(10) + pool
            + u2(0x0021) + u2(8) + u2(9) + u2(0) + u2(int(directory))
            + (field if directory else b'') + u2(1) + member + u2(0))


def metadata(version='1.0'):
    return normalize(json.loads((FIXTURES / f'{version}.json').read_text()))


def client(tmp_path, doc, **kwargs):
    path = tmp_path / 'client.jar'
    path.write_bytes(archive({'net/minecraft/client/Minecraft.class': java_class(entrypoints.LEGACY_CLIENT, **kwargs)}))
    doc['downloads']['client'].update(sha1=hashlib.sha1(path.read_bytes()).hexdigest(), size=path.stat().st_size)
    return path


@pytest.mark.parametrize('version', ['1.0', '1.5.2'])
def test_official_legacy_metadata_names_a_dependency_entrypoint(version, tmp_path):
    doc = metadata(version)
    before = copy.deepcopy(doc)
    path = client(tmp_path, doc)
    verify_client(path, doc)
    assert entrypoints.client_main(doc) == entrypoints.LEGACY_CLIENT
    assert entrypoints.legacy_wrapper(doc) == 'libraries/net/minecraft/launchwrapper/1.5/launchwrapper-1.5.jar'
    assert doc['mainClass'] == before['mainClass'] == entrypoints.LEGACY_MAIN
    assert assess_metadata(doc).can_package
    assert assess(version, ()).status == (Status.PARTIAL if version == '1.0' else Status.UNTESTED)


def test_java2d_workaround_applies_only_to_physically_affected_legacy_version():
    options = ['-Dsun.java2d.d3d=false', '-Dsun.java2d.opengl=false']
    assert arguments.legacy_java2d_options(metadata('1.0')) == options
    assert arguments.legacy_java2d_options(metadata('1.5.2')) == []
    words_152 = arguments.parse_argfile(arguments.generate(metadata('1.5.2'), ['versions/client.jar'], '../../'))
    assert not any(word.startswith('-Dsun.java2d.') for word in words_152)
    words_old_10 = arguments.parse_argfile(arguments.generate(metadata('1.0'), ['versions/client.jar'], '../../', legacy_java2d=False))
    assert not any(word.startswith('-Dsun.java2d.') for word in words_old_10)
    other = metadata('1.0')
    other['mainClass'] = 'net.minecraft.client.main.Main'
    assert arguments.legacy_java2d_options(other) == []


@pytest.mark.parametrize('fault', ['missing-library', 'duplicate-library', 'unverified-library', 'other-assets', 'other-runtime'])
def test_wrapper_requires_an_unambiguous_declared_contract(tmp_path, fault):
    doc = metadata()
    path = client(tmp_path, doc)
    wrapper = next(x for x in doc['libraries'] if x['name'].startswith('net.minecraft:launchwrapper:'))
    if fault == 'missing-library': doc['libraries'].remove(wrapper)
    elif fault == 'duplicate-library': doc['libraries'].append(copy.deepcopy(wrapper))
    elif fault == 'unverified-library': del wrapper['downloads']['artifact']['sha1']
    elif fault == 'other-assets': doc['assetIndex']['id'] = 'unsupported'
    else: doc['javaVersion']['majorVersion'] = 17
    with pytest.raises(ValueError, match='Legacy LaunchWrapper'):
        verify_client(path, doc)


@pytest.mark.parametrize('kwargs', [dict(flags=0x0001), dict(flags=0x0008), dict(directory=False),
                                   dict(method='notMain'), dict(descriptor='()V'), dict(body=b'')])
def test_legacy_client_contract_is_checked_without_loading_classes(tmp_path, kwargs):
    doc = metadata()
    path = client(tmp_path, doc, **kwargs)
    with pytest.raises(ValueError, match='Legacy'):
        verify_client(path, doc)


def test_named_class_cannot_hide_malformed_or_duplicate_entrypoints():
    correct = java_class(entrypoints.LEGACY_CLIENT)
    for data in [b'not bytecode', correct[:-1], java_class('other.Main'), correct + b'trailing']:
        with pytest.raises(ValueError):
            entrypoints.class_contract(data, entrypoints.LEGACY_CLIENT, game_directory=True)
    with zipfile.ZipFile(io.BytesIO(archive({'other/Main.class': correct}))) as jar:
        with pytest.raises(ValueError, match='missing'):
            entrypoints.verify_legacy_class(jar, entrypoints.LEGACY_CLIENT)


def test_duplicate_client_member_and_unknown_main_are_never_guessed(tmp_path):
    doc = metadata()
    path = client(tmp_path, doc)
    with pytest.warns(UserWarning, match='Duplicate name'):
        with zipfile.ZipFile(path, 'a') as jar:
            jar.writestr('net/minecraft/client/Minecraft.class', java_class(entrypoints.LEGACY_CLIENT))
    doc['downloads']['client'].update(sha1=hashlib.sha1(path.read_bytes()).hexdigest(), size=path.stat().st_size)
    with pytest.raises(ValueError, match='duplicated'):
        verify_client(path, doc)
    doc['mainClass'] = 'unknown.Launcher'
    with pytest.raises(ValueError, match='missing from the client'):
        verify_client(path, doc)


@pytest.mark.parametrize('mapped', [False, None, 1, 'true'])
def test_legacy_asset_view_must_actually_be_mapped(mapped):
    with pytest.raises(ValueError, match='mapped pre-1.6'):
        entrypoints.check_assets(metadata(), {'map_to_resources': mapped})


def build_fixture(tmp_path, *, pre_workaround=False):
    doc = metadata()
    source = client(tmp_path, doc)
    blobs = {}
    for spec, native, _ in resolve(doc):
        if native: data = archive({'legacy.dll': b'synthetic Windows native'})
        elif spec['path'].startswith('net/minecraft/launchwrapper/'):
            data = archive({entrypoints.LEGACY_CLASSES[0]: java_class(entrypoints.LEGACY_MAIN, directory=False),
                            **{name: b'owned fixture class' for name in entrypoints.LEGACY_CLASSES[1:]}})
        else: data = archive({'fixture.class': b'owned fixture class'})
        blobs[spec['url']] = data
    # Update every repeated metadata reference to the same synthetic object.
    for lib in doc['libraries']:
        specs = [lib.get('downloads', {}).get('artifact'), *lib.get('downloads', {}).get('classifiers', {}).values()]
        for spec in filter(None, specs):
            if spec['url'] in blobs:
                data = blobs[spec['url']];spec.update(sha1=hashlib.sha1(data).hexdigest(), size=len(data))
    asset = b'owned asset'
    asset_sha = hashlib.sha1(asset).hexdigest()
    sound = b'owned synthetic Ogg fixture'
    sound_sha = hashlib.sha1(sound).hexdigest()
    index = json.dumps({'map_to_resources': True, 'objects': {
        'icons/icon_16x16.png': {'hash': asset_sha, 'size': len(asset)},
        'sound3/random/click.ogg': {'hash': sound_sha, 'size': len(sound)}}}).encode()
    doc['assetIndex'].update(sha1=hashlib.sha1(index).hexdigest(), size=len(index))
    blobs[doc['assetIndex']['url']] = index
    blobs[f'https://resources.download.minecraft.net/{asset_sha[:2]}/{asset_sha}'] = asset
    blobs[f'https://resources.download.minecraft.net/{sound_sha[:2]}/{sound_sha}'] = sound
    data = json.dumps(doc).encode()
    blobs['https://fixture/legacy'] = data
    blobs[MANIFEST] = json.dumps({'versions': [{'id': '1.0', 'url': 'https://fixture/legacy', 'sha1': hashlib.sha1(data).hexdigest()}]}).encode()
    java = runtime(tmp_path / 'java8', 8)
    output = tmp_path / 'Legacy Package'
    workaround = patch('minecraft_playden.arguments.legacy_java2d_options', return_value=[]) if pre_workaround else nullcontext()
    with patch('urllib.request.urlopen', side_effect=lambda request, **_: io.BytesIO(blobs[request.full_url])), workaround:
        build(output, Downloader(tmp_path / 'cache', runtime_paths=(java,)), ClientSource('local', version='1.0', client_jar=source), 'Legacy Player')
    return output


def test_legacy_package_keeps_official_main_runtime_assets_and_java8_adapter(tmp_path):
    root = build_fixture(tmp_path)
    assert validate(root) == []
    doc = json.loads((root / 'metadata/version.json').read_text())
    assert doc['mainClass'] == entrypoints.LEGACY_MAIN
    assert json.loads((root / 'metadata/runtime.json').read_text())['major'] == 8
    words = arguments.parse_argfile((root / 'playden.args').read_text())
    assert words[words.index(entrypoints.LEGACY_MAIN) + 1:words.index('--gameDir')] == ['Legacy Player', '0']
    assert words[words.index('--assetsDir') + 1] == '../../instances/default/minecraft/resources'
    assert words[:2] == ['-Dsun.java2d.d3d=false', '-Dsun.java2d.opengl=false']
    assert json.loads((root / 'package.json').read_text())['legacy_java2d'] == 1
    assert (root / 'instances/default/minecraft/resources/icons/icon_16x16.png').read_bytes() == b'owned asset'
    assert (root / 'instances/default/minecraft/resources/sound3/random/click.ogg').read_bytes() == b'owned synthetic Ogg fixture'
    with zipfile.ZipFile(root / java8.JAR) as jar:
        assert hashlib.sha256(jar.read(java8.CLASS)).hexdigest() == java8.BOOTSTRAP_SHA256
    command = (root / java8.COMMAND).read_text()
    assert all(option in command for option in ('-Dsun.java2d.d3d=false', '-Dsun.java2d.opengl=false'))
    assert java8.MAIN in command
    assert '${' not in (root / 'playden.args').read_text()


def test_pre_workaround_legacy_package_keeps_its_original_validated_launch(tmp_path):
    root = build_fixture(tmp_path, pre_workaround=True)
    assert 'legacy_java2d' not in json.loads((root / 'package.json').read_text())
    assert '-Dsun.java2d.d3d=false' not in (root / 'playden.args').read_text()
    assert validate(root) == []


@pytest.mark.parametrize('fault', ['missing-wrapper', 'wrapper-hash', 'bad-wrapper-main', 'missing-client-target'])
def test_legacy_rehashed_package_cannot_bypass_entrypoint_validation(tmp_path, fault):
    root = build_fixture(tmp_path)
    doc = json.loads((root / 'metadata/version.json').read_text())
    wrapper = entrypoints.legacy_wrapper(doc)
    assert wrapper is not None
    inventory = json.loads((root / 'package.json').read_text())
    if fault == 'missing-wrapper':
        (root / wrapper).unlink()
    else:
        target = root / ('versions/client.jar' if fault == 'missing-client-target' else wrapper)
        if fault == 'wrapper-hash': target.write_bytes(b'corrupt wrapper')
        else:
            # Rehash metadata too: independently check class signatures/locations.
            members = {'other/Main.class': b'owned fixture'} if fault == 'missing-client-target' else {
                entrypoints.LEGACY_CLASSES[0]: java_class(entrypoints.LEGACY_MAIN, flags=0x0001),
                **{name: b'owned fixture class' for name in entrypoints.LEGACY_CLASSES[1:]}}
            target.write_bytes(archive(members))
            spec = doc['downloads']['client'] if fault == 'missing-client-target' else next(
                lib['downloads']['artifact'] for lib in doc['libraries'] if lib['name'].startswith('net.minecraft:launchwrapper:'))
            spec.update(sha1=hashlib.sha1(target.read_bytes()).hexdigest(), size=target.stat().st_size)
            (root / 'metadata/version.json').write_text(json.dumps(doc))
            # Rebuild the deterministic launch adapter for changed metadata.
            archive_bytes, command = java8.artifacts(doc, inventory['classpath'], (root / 'playden.args').read_text())
            (root / java8.JAR).write_bytes(archive_bytes);(root / java8.COMMAND).write_text(command)
        for name in inventory['files']:
            file = root / name
            if file.is_file(): inventory['files'][name] = hashlib.sha256(file.read_bytes()).hexdigest()
        (root / 'package.json').write_text(json.dumps(inventory))
    assert validate(root)
