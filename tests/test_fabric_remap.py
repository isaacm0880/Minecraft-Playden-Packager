"""Cache preparation and real loader integration, without Minecraft/mod execution."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from minecraft_playden import fabric_remap
from minecraft_playden.compatibility import Status, assess
from minecraft_playden.downloads import Downloader
from minecraft_playden.exporter import build
from minecraft_playden.modloaders import ModLoader
from minecraft_playden.sources import ClientSource
from minecraft_playden.validator import validate
from test_packager import archive
from test_version_matrix import installation, put, spec_payload


def fixture_package(tmp_path, version='1.20.1', prepared=False, mapping_format='v1'):
    source = tmp_path / 'source'
    instance, metadata, blobs, _ = installation(source, version, True, 'official', loader_override='0.20.1')
    if version != '26.3':
        client = archive({'net/minecraft/client/main/Main.class': b'fixture Main', 'a.class': b'fixture a'})
        spec_payload(metadata['downloads']['client'], client, blobs)
        put(source, f'versions/{version}/{version}.jar', client)
        base = json.dumps(metadata).encode()
        put(source, f'versions/{version}/{version}.json', base)
        blobs['https://fixture/base'] = base
        manifest_url = next(url for url in blobs if url.endswith('version_manifest_v2.json'))
        manifest = json.loads(blobs[manifest_url])
        manifest['versions'][0]['sha1'] = hashlib.sha1(base).hexdigest()
        blobs[manifest_url] = json.dumps(manifest).encode()
        mappings = archive({'mappings/mappings.tiny': (
            b'v1\tofficial\tintermediary\nCLASS\ta\tnet/minecraft/class_1\n' if mapping_format == 'v1'
            else b'tiny\t2\t0\tofficial\tintermediary\nc\ta\tnet/minecraft/class_1\n')})
        put(source, f'libraries/net/fabricmc/intermediary/{version}/intermediary-{version}.jar', mappings)
        path = instance / f'{instance.name}.json'
        profile = json.loads(path.read_text())
        lib = next(lib for lib in profile['libraries'] if lib['name'].startswith('net.fabricmc:intermediary:'))
        lib.update(sha1=hashlib.sha1(mappings).hexdigest(), size=len(mappings))
        path.write_text(json.dumps(profile))
    output = tmp_path / 'Built Package'
    with patch('urllib.request.urlopen', side_effect=lambda req, **kw: io.BytesIO(blobs[req.full_url])), patch.object(fabric_remap, '_run', side_effect=fake_remap):
        build(output, Downloader(tmp_path / 'cache'), ClientSource('local', instance=instance),
              prepare_fabric=prepared, remap_java=Path(sys.executable))
    assert validate(output) == []
    return output, instance


def fake_remap(java, arguments, work, cancellation, progress=None):
    classpath, client, game, receipt, mappings, namespace, version = arguments
    assert namespace == 'official'
    assert Path(mappings).name == f'intermediary-{version}.jar'
    assert all('/libraries/' in p.replace('\\', '/') for p in Path(classpath).read_text().splitlines())
    relative = f'.fabric/remappedJars/minecraft-{version}-0.20.1/client-intermediary.jar'
    assert not (Path(game) / relative).exists(), 'Preparation must not trust an old cache'
    with zipfile.ZipFile(client) as jar:
        contents = {('net/minecraft/class_1.class' if name == 'a.class' else name): jar.read(name) for name in jar.namelist()}
    put(Path(game), relative, archive(contents))
    Path(receipt).write_text(json.dumps({'normalized_version': version, 'minecraft': version,
                                        'loader': '0.20.1', 'relative_output': relative}))


def test_new_copy_is_prepared_and_validated_without_modifying_original(tmp_path):
    source, _ = fixture_package(tmp_path)
    before = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob('*') if p.is_file()}
    # Copy an incomplete runtime cache, which must not reach the prepared result.
    put(source, 'instances/default/minecraft/.fabric/remappedJars/minecraft-1.20.1-0.20.1/client-intermediary.jar.tmp', b'stale')
    prepared = tmp_path / 'Prepared Copy'
    with patch.object(fabric_remap, '_run', side_effect=fake_remap), patch('urllib.request.urlopen', side_effect=AssertionError('Preparation must be offline')):
        fabric_remap.prepare_package(source, prepared, Path(sys.executable))
    assert validate(prepared) == []
    record = json.loads((prepared / fabric_remap.RECORD).read_text())
    output = next(iter(record['outputs']))
    inventory = json.loads((prepared / 'package.json').read_text())
    assert inventory['files'][output] == record['outputs'][output]
    assert all((source / name).read_bytes() == data for name, data in before.items())
    assert (source / 'instances/default/minecraft/.fabric/remappedJars/minecraft-1.20.1-0.20.1/client-intermediary.jar.tmp').read_bytes() == b'stale'
    assert not list(prepared.rglob('*.tmp'))
    assert 'saves/' not in ' '.join(record['inputs']) and 'mods/' not in ' '.join(record['inputs'])
    assert str(tmp_path) not in (prepared / fabric_remap.RECORD).read_text()
    with pytest.raises(ValueError, match='never overwritten'):
        fabric_remap.prepare_package(source, prepared, Path(sys.executable))


def test_build_time_preparation_and_validated_cache_reuse(tmp_path):
    source, _ = fixture_package(tmp_path, prepared=True)
    assert json.loads((source / fabric_remap.RECORD).read_text())['mode'] == 'loader-cache'
    with patch.object(fabric_remap, '_run', side_effect=fake_remap) as worker:
        fabric_remap.prepare_package(source, tmp_path / 'Again', Path(sys.executable))
    assert worker.call_count == 0 and validate(tmp_path / 'Again') == []


@pytest.mark.parametrize('changed', ['loader', 'minecraft', 'client', 'mapping', 'classpath', 'temporary'])
def test_changed_inputs_or_incomplete_output_invalidate_cache(tmp_path, changed):
    root, _ = fixture_package(tmp_path, prepared=True)
    inventory = json.loads((root / 'package.json').read_text())
    record = json.loads((root / fabric_remap.RECORD).read_text())
    if changed in ('loader', 'minecraft'):
        record[changed] = 'different-version'
        put(root, fabric_remap.RECORD, json.dumps(record))
        inventory['files'][fabric_remap.RECORD] = hashlib.sha256((root / fabric_remap.RECORD).read_bytes()).hexdigest()
    elif changed == 'temporary':
        output = next(iter(record['outputs']))
        put(root, output + '.tmp', b'incomplete')
    else:
        selected = ('versions/client.jar' if changed == 'client' else next(
            name for name in record['inputs'] if ('intermediary-' in name if changed == 'mapping' else '/asm/' in name)))
        put(root, selected, b'changed input')
        inventory['files'][selected] = hashlib.sha256((root / selected).read_bytes()).hexdigest()
    (root / 'package.json').write_text(json.dumps(inventory))
    assert fabric_remap.validate(root, inventory)


def test_missing_or_truncated_cache_rejected_even_after_inventory_rehash(tmp_path):
    root, _ = fixture_package(tmp_path, prepared=True)
    record = json.loads((root / fabric_remap.RECORD).read_text())
    name = next(iter(record['outputs']))
    put(root, name, archive({'net/minecraft/client/main/Main.class': b'Main'}))
    record['outputs'][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    put(root, fabric_remap.RECORD, json.dumps(record))
    inventory = json.loads((root / 'package.json').read_text())
    for path in (name, fabric_remap.RECORD):
        inventory['files'][path] = hashlib.sha256((root / path).read_bytes()).hexdigest()
    (root / 'package.json').write_text(json.dumps(inventory))
    assert any('incomplete' in error for error in validate(root))
    (root / name).unlink()
    assert validate(root)


def test_no_mappings_26_3_needs_neither_java_nor_wine(tmp_path):
    root, _ = fixture_package(tmp_path, '26.3')
    with patch.object(fabric_remap, '_run', side_effect=AssertionError('Unobfuscated client must not be remapped')):
        fabric_remap.prepare_package(root, tmp_path / 'Prepared', tmp_path / 'missing-java')
    assert json.loads((tmp_path / 'Prepared' / fabric_remap.RECORD).read_text())['mode'] == 'not-required'
    assert validate(tmp_path / 'Prepared') == []
    assert assess('26.3', (ModLoader('Fabric', '0.20.1'),)).status == Status.TESTED


def test_failure_or_cancel_never_publishes_a_partial_package(tmp_path):
    root, _ = fixture_package(tmp_path)
    with patch.object(fabric_remap, '_run', side_effect=ValueError('remapper rejected inputs')):
        with pytest.raises(ValueError, match='rejected inputs'):
            fabric_remap.prepare_package(root, tmp_path / 'No Output', Path(sys.executable))
    assert not (tmp_path / 'No Output').exists()
    from minecraft_playden.operations import CancellationToken, OperationCancelled
    token = CancellationToken()
    token.cancel()
    with pytest.raises(OperationCancelled):
        fabric_remap.prepare_package(root, tmp_path / 'Cancelled', Path(sys.executable), token)
    assert not (tmp_path / 'Cancelled').exists()


def test_physical_evidence_distinguishes_vanilla_success_from_fabric_retest():
    assert assess('1.20.1', ()).status == Status.TESTED
    result = assess('1.20.1', (ModLoader('Fabric', '0.20.1'),))
    assert result.status == Status.TESTED and result.permits_build
    assert 'main menu' in result.evidence and 'native pre-remapping' in result.evidence and 'startup only' in result.evidence


@pytest.mark.skipif(not os.environ.get('PLAYDEN_FABRIC_LIBRARIES'), reason='Opt-in real Fabric loader/ASM/mapping inputs; no network acquisition in tests')
@pytest.mark.parametrize('loader_version', ['0.20.1', '0.14.23'])
def test_real_loader_remaps_and_reuses_cache_without_starting_game(tmp_path, loader_version):
    libraries = Path(os.environ['PLAYDEN_FABRIC_LIBRARIES'])
    java = Path(shutil.which('java') or '')
    client = Path(os.environ.get('PLAYDEN_FABRIC_CLIENT', ''))
    version = '1.20.1'
    mapping = libraries / 'net/fabricmc/intermediary/1.20.1/intermediary-1.20.1.jar'
    loader = libraries / f'net/fabricmc/fabric-loader/{loader_version}/fabric-loader-{loader_version}.jar'
    if not loader.is_file() and loader_version != '0.20.1':
        pytest.skip('Optional older loader API integration requires its installed dependencies')
    assert mapping.is_file() and loader.is_file() and client.is_file()
    from minecraft_playden.fabric import coordinate
    with zipfile.ZipFile(loader) as jar:
        declared = json.loads(jar.read('fabric-installer.json'))['libraries']['common']
    paths = [loader, mapping, *[libraries / coordinate(lib['name'])[1] for lib in declared]]
    assert all(path.is_file() for path in paths), 'Complete the exact selected loader installation before integration testing'
    put(tmp_path, 'classpath.txt', '\n'.join(str(p.resolve()) for p in paths))
    game = tmp_path / 'game'
    game.mkdir()
    arguments = [str(tmp_path / 'classpath.txt'), str(client.resolve()), str(game), str(tmp_path / 'receipt.json'), str(mapping.resolve()), 'official', version]
    fabric_remap._run(java, arguments, tmp_path, None)
    receipt = json.loads((tmp_path / 'receipt.json').read_text())
    output = game / receipt['relative_output']
    assert receipt['loader'] == loader_version and receipt['normalized_version'] == version
    with zipfile.ZipFile(output) as jar:
        assert jar.testzip() is None
        assert 'net/minecraft/client/main/Main.class' in jar.namelist()
        assert sum(name.endswith('.class') for name in jar.namelist()) > 7000
    # Exercise Python's production mapping/completeness verifier against the real
    # client and real remapper output as well, without executing either game JAR.
    put(tmp_path, 'versions/client.jar', client.read_bytes())
    relative_mapping = 'libraries/net/fabricmc/intermediary/1.20.1/intermediary-1.20.1.jar'
    put(tmp_path, relative_mapping, mapping.read_bytes())
    _, _, classes, _ = fabric_remap._mapping(tmp_path, [relative_mapping], version)
    fabric_remap._verify_jar(tmp_path, output.relative_to(tmp_path).as_posix(), classes)
    original_hash, modified = hashlib.sha256(output.read_bytes()).hexdigest(), output.stat().st_mtime_ns
    fabric_remap._run(java, arguments, tmp_path, None)
    assert output.stat().st_mtime_ns == modified and hashlib.sha256(output.read_bytes()).hexdigest() == original_hash
    assert not output.with_name(output.name + '.tmp').exists()
    assert 'preparing JARs' not in (tmp_path / 'remap.log').read_text()


def test_prepared_rebuild_works_offline_and_keeps_unrelated_user_files(tmp_path):
    original, instance = fixture_package(tmp_path)
    put(original, 'instances/default/minecraft/.fabric/remappedJars-personal/note.txt', b'keep this')
    with patch.object(fabric_remap, '_run', side_effect=fake_remap), patch('urllib.request.urlopen', side_effect=AssertionError('Offline rebuild attempted network')):
        build(tmp_path / 'Offline Prepared', Downloader(tmp_path / 'cache', offline=True),
              ClientSource('local', instance=instance), prepare_fabric=True, remap_java=Path(sys.executable))
        fabric_remap.prepare_package(original, tmp_path / 'Preserved', Path(sys.executable))
    assert validate(tmp_path / 'Offline Prepared') == []
    assert (tmp_path / 'Preserved/instances/default/minecraft/.fabric/remappedJars-personal/note.txt').read_bytes() == b'keep this'


def test_cli_prepares_existing_package_to_a_fresh_output_offline(tmp_path, capsys):
    from minecraft_playden.cli import main
    source, _ = fixture_package(tmp_path)
    output = tmp_path / 'CLI Prepared'
    argv = ['packager', 'prepare-fabric', str(source), '--output', str(output), '--remap-java', sys.executable]
    with patch('sys.argv', argv), patch.object(fabric_remap, '_run', side_effect=fake_remap), patch('urllib.request.urlopen', side_effect=AssertionError('Preparation is offline')):
        assert main() == 0
    assert validate(output) == []
    assert not (source / fabric_remap.RECORD).exists()
    assert 'PS5 launch still requires physical testing' in capsys.readouterr().out


@pytest.mark.parametrize('override', ['-Dfabric.development', '-Dfabric.runtimeMappingNamespace=named', '-Dfabric.gameJarPath.client=elsewhere.jar'])
def test_custom_namespace_or_client_override_is_not_silently_prepared(tmp_path, override):
    from minecraft_playden import arguments, dependencies, fabric
    root, _ = fixture_package(tmp_path)
    data = json.loads((root / 'metadata/fabric.json').read_text())
    data['profiles'][-1].setdefault('arguments', {}).setdefault('jvm', []).append(override)
    metadata = json.loads((root / 'metadata/version.json').read_text())
    effective = fabric.derive(metadata, data)
    put(root, 'metadata/fabric.json', json.dumps(data))
    put(root, 'playden.args', arguments.generate(effective, dependencies.classpath(effective), '../../'))
    inventory = json.loads((root / 'package.json').read_text())
    for name in ('playden.args', 'metadata/fabric.json'):
        inventory['files'][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    (root / 'package.json').write_text(json.dumps(inventory))
    assert validate(root) == []
    with patch.object(fabric_remap, '_run', side_effect=AssertionError('No worker for unsupported override')):
        with pytest.raises(ValueError, match='Custom Fabric'):
            fabric_remap.prepare_package(root, tmp_path / 'Unsupported', Path(sys.executable))
    assert not (tmp_path / 'Unsupported').exists()


@pytest.mark.parametrize('version', ['1.14.4', '1.16', '1.20.1'])
@pytest.mark.parametrize('mapping_format', ['v1', 'v2'])
def test_shared_exporter_automatically_prepares_required_mappings(tmp_path, version, mapping_format):
    original, instance = fixture_package(tmp_path, version, mapping_format=mapping_format)
    output = tmp_path / 'Automatic Package'
    with patch.object(fabric_remap, '_run', side_effect=fake_remap) as worker, patch('urllib.request.urlopen', side_effect=AssertionError('Offline')):
        build(output, Downloader(tmp_path / 'cache', offline=True), ClientSource('local', instance=instance))
    assert worker.call_count == 1
    assert validate(output) == []
    assert fabric_remap.package_status(output, validated=True).prepared
    # Preparation never changes either launch mechanism or the game runtime.
    for name in ['playden.args', 'playden/recipe.json', 'metadata/runtime.json'] + (
            ['playden-java8.jar', 'playden.java8-command'] if version != '1.20.1' else []):
        assert (output / name).read_bytes() == (original / name).read_bytes()


@pytest.mark.parametrize('version', ['1.14.4', '1.16'])
@pytest.mark.parametrize('option', [None, '--prepare-fabric', '--no-prepare-fabric'])
def test_cli_preparation_default_opt_in_and_explicit_opt_out(tmp_path, capsys, version, option):
    from minecraft_playden.cli import main
    _, instance = fixture_package(tmp_path, version)
    output = tmp_path / 'CLI Default'
    argv = ['packager', '--cache', str(tmp_path / 'cache'), '--offline', 'build', '--source', 'local',
            '--instance', str(instance), '--output', str(output)] + ([option] if option else [])
    with patch('sys.argv', argv), patch.object(fabric_remap, '_run', side_effect=fake_remap) as worker:
        assert main() == 0
    state = fabric_remap.package_status(output, validated=True)
    assert state.required and state.prepared is (option != '--no-prepare-fabric')
    assert worker.call_count == (0 if option == '--no-prepare-fabric' else 1)
    text = capsys.readouterr().out
    assert ('explicitly disabled' if option == '--no-prepare-fabric' else 'Matching Fabric preparation cache validated') in text
    with patch('sys.argv', ['packager', 'validate', str(output)]):
        assert main() == 0
    assert state.message in capsys.readouterr().out


def test_default_preparation_failure_never_publishes_unprepared_fallback(tmp_path):
    _, instance = fixture_package(tmp_path, '1.16')
    with patch('minecraft_playden.host_java.find', side_effect=ValueError('No host-native JDK')):
        with pytest.raises(ValueError, match='No host-native JDK'):
            build(tmp_path / 'Failed Default', Downloader(tmp_path / 'cache', offline=True), ClientSource('local', instance=instance))
    assert not (tmp_path / 'Failed Default').exists()
    assert not list(tmp_path.glob('.playden-*'))


@pytest.mark.parametrize('version', ['1.14.4', '1.16'])
@pytest.mark.parametrize('platform', ['native', 'windows'])
def test_real_java8_loader_reuses_prepared_relative_cache(tmp_path, version, platform):
    """Real loader cache lookup only; never starts Knot, Minecraft or mods."""
    packages = os.environ.get('PLAYDEN_FABRIC_PREPARED')
    java = os.environ.get('PLAYDEN_JAVA8' if platform == 'native' else 'PLAYDEN_JAVA8_WINDOWS')
    javac = os.environ.get('PLAYDEN_JAVA8_JAVAC') or shutil.which('javac')
    if not packages or not java or not javac:
        pytest.skip('Set PLAYDEN_FABRIC_PREPARED and Java 8 probe runtimes for real prepared-cache reuse')
    root = Path(packages) / ('Fabric-' + version)
    assert fabric_remap.package_status(root).prepared
    _, _, _, classpath = fabric_remap._context(root)
    mapping, namespace, classes, _ = fabric_remap._mapping(root, classpath, version)
    record = json.loads((root / fabric_remap.RECORD).read_text())
    output = next(iter(record['outputs']))
    target = tmp_path / output
    target.parent.mkdir(parents=True)
    shutil.copyfile(root / output, target)
    target.chmod(0o444)
    target.parent.chmod(0o555)
    before = (hashlib.sha256(target.read_bytes()).hexdigest(), target.stat().st_mtime_ns)
    # Compile our existing host-tool adapter for this test JVM only. Its sole
    # Java 9 API is platform-loader selection; use the equivalent Java 8 parent.
    # Production preparation continues to require a native JDK 17+.
    source = tmp_path / 'FabricRemap.java'
    source.write_text(fabric_remap.HELPER.read_text().replace(
        'ClassLoader.getPlatformClassLoader()', 'ClassLoader.getSystemClassLoader().getParent()'))
    subprocess.run([javac, '--release', '8', '-d', str(tmp_path), str(source)], check=True, capture_output=True)
    def runtime_path(path):
        path = path.resolve()
        return 'Z:' + str(path).replace('/', '\\') if platform == 'windows' and os.name != 'nt' else str(path)
    listing = tmp_path / 'classpath.txt'
    listing.write_text('\n'.join(runtime_path(root / name) for name in classpath if name.startswith('libraries/')))
    cwd = tmp_path / 'java/bin'
    cwd.mkdir(parents=True)
    command = [java, '-cp', runtime_path(tmp_path), 'FabricRemap', runtime_path(listing),
               runtime_path(root / 'versions/client.jar'), '../../instances/default/minecraft',
               runtime_path(tmp_path / 'receipt.json'), runtime_path(root / mapping), namespace, version]
    if platform == 'windows' and os.name != 'nt':
        command.insert(0, 'wine')
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        receipt = json.loads((tmp_path / 'receipt.json').read_text())
        assert receipt['relative_output'] == output.removeprefix('instances/default/minecraft/')
        assert receipt['loader'] == '0.20.1' and receipt['normalized_version'] == version
        assert before == (hashlib.sha256(target.read_bytes()).hexdigest(), target.stat().st_mtime_ns)
        assert not target.with_name(target.name + '.tmp').exists()
        assert 'preparing JARs' not in result.stdout + result.stderr
        # Exercise production completeness/CRC validation on the real cache too.
        fabric_remap._verify_jar(root, output, classes)
        # Compare fresh remapping with cache reuse. Desktop Wine may reject
        # ZIP writes through its access checks too; the prepared-cache path
        # above must succeed even in that environment.
        (tmp_path / 'fresh-game').mkdir()
        fresh = command.copy()
        fresh[fresh.index('../../instances/default/minecraft')] = '../../fresh-game'
        result = subprocess.run(fresh, cwd=cwd, capture_output=True, text=True, timeout=90)
        if platform == 'windows' and result.returncode:
            assert "can't be written" in result.stderr and 'OutputConsumerPath.java:102' in result.stderr, result.stdout + result.stderr
            incomplete = list((tmp_path / 'fresh-game').rglob('client-intermediary.jar.tmp'))
            assert len(incomplete) == 1  # Creation worked before the ZIP access check.
            with zipfile.ZipFile(incomplete[0]) as jar:
                assert jar.testzip() is None
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            receipt = json.loads((tmp_path / 'receipt.json').read_text())
            produced = tmp_path / 'fresh-game' / receipt['relative_output']
            with zipfile.ZipFile(produced) as jar:
                assert jar.testzip() is None and 'net/minecraft/client/main/Main.class' in jar.namelist()
            assert not produced.with_name(produced.name + '.tmp').exists()
    finally:
        target.parent.chmod(0o755)
        target.chmod(0o644)
