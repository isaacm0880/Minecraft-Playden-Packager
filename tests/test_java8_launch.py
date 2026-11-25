"""Independent Java-8 adapter tests; only our probe code is executed."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import zipfile

import pytest

from minecraft_playden import arguments, java8, metadata
from minecraft_playden.downloads import Downloader
from minecraft_playden import runtime


def document():
    return {'id': 'probe', 'mainClass': 'net.minecraft.client.main.Main',
            'javaVersion': {'majorVersion': 8}, 'assetIndex': {'id': 'probe'},
            'arguments': {'jvm': ['-Xmx64M', '-Dprobe.setting=space value', '-cp', '${classpath}'],
                          'game': ['--username', '${auth_player_name}', '', 'quotes " and slash \\', 'Zoë 玩家']}}


def decode_plan(data):
    position = 4
    def integer():
        nonlocal position
        value = struct.unpack_from('>I', data, position)[0]
        position += 4
        return value
    def string():
        nonlocal position
        length = integer()
        value = data[position:position + length].decode('utf-8')
        position += length
        return value
    assert data[:4] == b'PDJ8'
    main = string()
    args = [string() for _ in range(integer())]
    assert position == len(data)
    return main, args


def test_adapter_preserves_game_argv_and_explicit_jvm_options(tmp_path):
    doc = document()
    cp = ['libraries/space # ü.jar', 'versions/client.jar']
    text = arguments.generate(doc, cp, '../../', 'Player "quoted" \\')
    archive, command = java8.artifacts(doc, cp, text)
    (tmp_path / java8.JAR).write_bytes(archive)
    with zipfile.ZipFile(tmp_path / java8.JAR) as jar:
        main, args = decode_plan(jar.read('playden-launch.bin'))
        assert main == doc['mainClass']
        assert args == ['--username', 'Player "quoted" \\', '', 'quotes " and slash \\', 'Zoë 玩家']
        assert jar.namelist() == ['META-INF/MANIFEST.MF', java8.CLASS, 'playden-launch.bin']
        assert hashlib.sha256(jar.read(java8.CLASS)).hexdigest() == java8.BOOTSTRAP_SHA256
        assert struct.unpack('>H', jar.read(java8.CLASS)[6:8])[0] == 52
        manifest = jar.read('META-INF/MANIFEST.MF')
        assert all(len(line) <= 72 for line in manifest.split(b'\r\n'))
        assert b'Class-Path: libraries/space%20%23%20%C3%BC.jar versions/client.jar' in manifest.replace(b'\r\n ', b'')
    assert '"-Xmx64M"' in command and '"-Dprobe.setting=space value"' in command
    assert '${' not in command and 'Player' not in command and '"-cp" "../../playden-java8.jar"' in command
    assert java8.artifacts(doc, cp, text) == (archive, command)


@pytest.mark.parametrize('words', [['x'] * 33, ['x' * 4096], ['bad"quote'], ['bad\ncontrol']])
def test_command_limits_and_unrepresentable_jvm_values_are_rejected(words):
    with pytest.raises(ValueError, match='Java 8'):
        java8.windows_command(words)


@pytest.mark.parametrize('path', ['../escape.jar', '/absolute.jar', 'https://bad/a.jar', 'libraries/wild*.jar', 'libraries/a;other.jar'])
def test_manifest_refuses_nonportable_paths(path):
    doc = document()
    with pytest.raises(ValueError):
        java8.artifacts(doc, [path], arguments.generate(doc, [path], '../../'))


def test_java8_jdk_local_layout_is_normalized_to_jre_without_source_changes(tmp_path):
    from runtime_fixtures import runtime as fake_runtime
    source = tmp_path / 'jdk8'
    fake_runtime(source / 'jre', 8)
    shutil.move(source / 'jre/release', source / 'release')
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()}
    result = runtime.install({'javaVersion': {'majorVersion': 8}},
                             Downloader(tmp_path / 'cache', runtime_paths=(source,)), tmp_path / 'out')
    assert result['major'] == 8
    assert runtime.inspect(tmp_path / 'out', 8)['target'] == 'windows-x64'
    assert not (tmp_path / 'out/jre').exists()
    assert before == {p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()}


def smoke_package(tmp_path):
    javac = os.environ.get('PLAYDEN_JAVA8_JAVAC') or shutil.which('javac')
    if not javac:
        pytest.skip('JDK compiler needed for the owned Java-8 probe')
    source = tmp_path / 'Main.java'
    source.write_text('''package net.minecraft.client.main;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
public class Main {
 public static void main(String[] args) {
  System.out.println("VERSION=" + System.getProperty("java.specification.version"));
  System.out.println("CP=" + Base64.getEncoder().encodeToString(System.getProperty("java.class.path").getBytes(StandardCharsets.UTF_8)));
  System.out.println("OPTION=" + System.getProperty("probe.setting"));
  for (String arg : args) System.out.println("ARG=" + Base64.getEncoder().encodeToString(arg.getBytes(StandardCharsets.UTF_8)));
 }
}''')
    classes = tmp_path / 'classes'
    subprocess.run([javac, '--release', '8', '-d', str(classes), str(source)], check=True, capture_output=True)
    root = tmp_path / 'Package spaces # ü'
    (root / 'java/bin').mkdir(parents=True)
    cp = ['libraries/space # ü.jar']
    jar = root / cp[0]
    jar.parent.mkdir()
    with zipfile.ZipFile(jar, 'w') as archive:
        archive.writestr('net/minecraft/client/main/Main.class', (classes / 'net/minecraft/client/main/Main.class').read_bytes())
    text = arguments.generate(document(), cp, '../../', 'Player "quoted" \\')
    (root / 'playden.args').write_text(text)
    command = java8.write(root, document(), cp)
    words = re.findall(r'"([^"]*)"', command)
    assert java8.windows_command(words) == command
    return root, words, jar


@pytest.mark.parametrize('platform', ['native', 'windows'])
def test_actual_java8_bootstrap_and_manifest_paths(tmp_path, platform):
    executable = os.environ.get('PLAYDEN_JAVA8' if platform == 'native' else 'PLAYDEN_JAVA8_WINDOWS')
    if not executable:
        pytest.skip('Set PLAYDEN_JAVA8 / PLAYDEN_JAVA8_WINDOWS for real Java 8 probes')
    root, words, jar = smoke_package(tmp_path)
    command = [executable, *words]
    if platform == 'windows' and os.name != 'nt':
        command.insert(0, 'wine')
    result = subprocess.run(command, cwd=root / 'java/bin', capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'VERSION=1.8' in result.stdout and 'OPTION=space value' in result.stdout
    decoded = [base64.b64decode(line[4:]).decode('utf-8') for line in result.stdout.splitlines() if line.startswith('ARG=')]
    assert decoded == ['--username', 'Player "quoted" \\', '', 'quotes " and slash \\', 'Zoë 玩家']
    cp = next(base64.b64decode(line[3:]).decode('utf-8') for line in result.stdout.splitlines() if line.startswith('CP='))
    assert 'space # ü.jar' in cp and 'playden-java8.jar' not in cp


def test_java8_never_selects_available_java17(tmp_path):
    from runtime_fixtures import runtime as fake_runtime
    source = fake_runtime(tmp_path / 'jdk17', 17)
    downloader = Downloader(tmp_path / 'cache', runtime_paths=(source,), offline=True)
    with pytest.raises(ValueError, match='Missing compatible Windows x64 Java 8'):
        runtime.install({'javaVersion': {'majorVersion': 8}}, downloader, tmp_path / 'out')


def test_downloaded_java8_jdk_extracts_verified_jre_layout(tmp_path):
    import io
    from unittest.mock import patch
    from runtime_fixtures import pe
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('jdk8/release', 'JAVA_VERSION="1.8.0_512"\nOS_NAME="Windows"\nOS_ARCH="amd64"')
        archive.writestr('jdk8/bin/java.exe', b'JDK tool launcher not used')
        archive.writestr('jdk8/jre/bin/java.exe', pe())
        archive.writestr('jdk8/jre/bin/server/jvm.dll', pe())
    data = output.getvalue()
    package = {'link': 'https://fixture/jdk8.zip', 'checksum': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    catalog = json.dumps([{'binary': {'package': package}}]).encode()
    requested = []
    def request(req, **kwargs):
        requested.append(req.full_url)
        return io.BytesIO(data if req.full_url == package['link'] else catalog)
    downloader = Downloader(tmp_path / 'cache', allow_runtime_download=True)
    with patch('urllib.request.urlopen', side_effect=request):
        result = runtime.install({'javaVersion': {'majorVersion': 8}}, downloader, tmp_path / 'out')
    assert result['major'] == 8 and (tmp_path / 'out/bin/java.exe').read_bytes() == pe()
    assert '/latest/8/hotspot' in requested[0]
    assert not (tmp_path / 'out/jre').exists()


def test_packaged_bootstrap_source_rebuild_is_reproducible(tmp_path):
    javac = os.environ.get('PLAYDEN_JAVA8_JAVAC')
    if not javac:
        pytest.skip('Set PLAYDEN_JAVA8_JAVAC to the documented compiler for reproducibility check')
    source = Path(__file__).resolve().parents[1] / 'minecraft_playden/resources/java8/Java8Launch.java'
    subprocess.run([javac, '--release', '8', '-encoding', 'UTF-8', '-g:none', '-d', str(tmp_path), str(source)],
                   check=True, capture_output=True)
    assert hashlib.sha256((tmp_path / java8.CLASS).read_bytes()).hexdigest() == java8.BOOTSTRAP_SHA256


@pytest.mark.parametrize('fault', ['jar', 'command-and-recipe', 'inventory', 'runtime'])
def test_rehashed_java8_package_tampering_cannot_bypass_validation(tmp_path, fault):
    from lan_fixtures import build_package
    from minecraft_playden.validator import validate
    root = build_package(tmp_path, legacy=True)
    inventory = json.loads((root / 'package.json').read_text())
    changed = []
    if fault == 'jar':
        (root / java8.JAR).write_bytes(b'forged adapter')
        changed.append(java8.JAR)
    elif fault == 'command-and-recipe':
        command = (root / java8.COMMAND).read_text().replace('-Xmx4G', '-Xmx8G')
        (root / java8.COMMAND).write_text(command)
        recipe = json.loads((root / 'recipe.json').read_text())
        recipe['launch_entries'][0]['arguments'] = command
        (root / 'recipe.json').write_text(json.dumps(recipe))
        changed += [java8.COMMAND, 'recipe.json']
    elif fault == 'inventory':
        del inventory['files'][java8.JAR]
    else:
        release = root / 'java/release'
        release.write_text(release.read_text().replace('1.8.0_472', '17.0.1'))
        changed.append('java/release')
    for name in changed:
        inventory['files'][name] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    (root / 'package.json').write_text(json.dumps(inventory))
    assert validate(root)


def test_java8_usb_copy_preserves_and_validates_adapter(tmp_path):
    from unittest.mock import patch
    from lan_fixtures import build_package
    from minecraft_playden.transfer import TransferRequest
    from minecraft_playden.volumes import DriveTarget, VolumeIdentity
    from minecraft_playden.validator import validate
    root = build_package(tmp_path / 'build', legacy=True)
    usb = tmp_path / 'USB'
    usb.mkdir()
    info = usb.stat()
    identity = VolumeIdentity('fixture', 'exfat', info.st_dev, info.st_ino)
    with patch('minecraft_playden.volumes._mounted_identity', return_value=identity), patch('minecraft_playden.transfer.flush_volume', side_effect=lambda drive: drive.check()):
        result = TransferRequest(root, DriveTarget.capture(usb)).run()
    assert validate(result.destination) == []
    assert (result.destination / java8.JAR).read_bytes() == (root / java8.JAR).read_bytes()
    assert (result.destination / java8.COMMAND).read_bytes() == (root / java8.COMMAND).read_bytes()


@pytest.mark.parametrize('version,modded', [('1.16', False), ('1.16', True), ('1.14.4', True)])
def test_java8_cli_builds_exact_runtime_and_preserves_controller_optout(tmp_path, monkeypatch, version, modded):
    import io
    from unittest.mock import patch
    from test_version_matrix import installation
    from minecraft_playden.cli import main
    from minecraft_playden.validator import validate
    instance, meta, blobs, _ = installation(tmp_path / 'source', version, modded, 'prism')
    output = tmp_path / 'CLI package'
    monkeypatch.setattr('sys.argv', ['packager', '--cache', str(tmp_path / 'cache'), 'build', '--source', 'local',
                                    '--instance', str(instance), '--output', str(output), '--no-controller-layout'])
    with patch('urllib.request.urlopen', side_effect=lambda req, **kwargs: io.BytesIO(blobs[req.full_url])):
        assert main() == 0
    assert validate(output) == []
    assert json.loads((output / 'metadata/runtime.json').read_text())['major'] == 8
    recipe = json.loads((output / 'recipe.json').read_text())
    assert 'defaults' not in recipe and recipe['launch_entries'][0]['arguments'] == (output / java8.COMMAND).read_text()


def test_actual_playden_parser_and_old_argument_budget(tmp_path):
    checker = os.environ.get('PLAYDEN_RECIPE_CHECK')
    if not checker:
        pytest.skip('Set PLAYDEN_RECIPE_CHECK for unmodified upstream integration')
    from lan_fixtures import build_package
    root = build_package(tmp_path, legacy=True)
    assert len((root / java8.COMMAND).read_bytes()) <= 512
    for flags in ([], ['--no-long-arguments'], ['--no-input-map']):
        result = subprocess.run([checker, str(root / 'recipe.json'), *flags], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert java8.MAIN in result.stdout
