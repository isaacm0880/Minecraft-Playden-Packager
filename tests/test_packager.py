import copy
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from minecraft_playden.arguments import generate
from minecraft_playden.dependencies import allowed, resolve, extract_natives
from minecraft_playden.downloads import Downloader, safe_path
from minecraft_playden.exporter import build
from minecraft_playden.validator import validate
from minecraft_playden.sources import ClientSource
from minecraft_playden.progress import Progress
from minecraft_playden.operations import CancellationToken, OperationCancelled
from minecraft_playden.transfer import TransferRequest
from minecraft_playden.volumes import DriveTarget, VolumeIdentity
from minecraft_playden.desktop import LocalBuildRequest
from minecraft_playden.discovery import Installation, discover

from runtime_fixtures import pe

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/version.json').read_text())


def archive(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as zip:
        for name, data in files.items():
            zip.writestr(name, data)
    return out.getvalue()


class PackagerTests(unittest.TestCase):
    def test_windows_rules_and_netty(self):
        selected = resolve(FIXTURE)
        self.assertEqual([s[0]['path'] for s in selected], ['netty/common.jar', 'it/unimi/dsi/fastutil/8.5.18/fastutil-8.5.18.jar', 'lwjgl/native.jar'])
        self.assertFalse(allowed({'rules': [{'action': 'allow', 'os': {'name': 'linux'}}]}))
        self.assertFalse(allowed({'rules': [{'action': 'allow', 'os': {'arch': 'x86'}}]}))
        self.assertFalse(allowed({'rules': [{'action': 'allow'}, {'action': 'disallow', 'os': {'name': 'windows'}}]}))

    def test_legacy_native_classifier(self):
        metadata = copy.deepcopy(FIXTURE)
        metadata['libraries'] = [{'name': 'x:y:1', 'natives': {'windows': 'natives-windows-${arch}'},
                                  'downloads': {'classifiers': {'natives-windows-64': {'path': 'native.jar'}}}}]
        self.assertEqual(resolve(metadata), [({'path': 'native.jar'}, True, ['META-INF/'])])

    def test_portable_args_and_unknown_placeholder(self):
        args = generate(FIXTURE, ['libraries/file with space.jar'], '../../', 'Test Player')
        self.assertIn('"../../libraries/file with space.jar"', args)
        self.assertIn('"../../instances/default/minecraft"', args)
        self.assertNotIn('--demo', args)
        metadata = copy.deepcopy(FIXTURE)
        metadata['arguments']['game'].append('${unknown}')
        with self.assertRaisesRegex(ValueError, 'placeholder'):
            generate(metadata, [], '')

    def test_paths_and_archive_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ['../escape', '/absolute', 'C:/windows', '..\\escape']:
                with self.assertRaises(ValueError):
                    safe_path(root, name)
            jar = root / 'bad.jar'
            jar.write_bytes(archive({'../escape.dll': b'bad'}))
            with self.assertRaises(ValueError):
                extract_natives(jar, root / 'natives', [])

    def test_download_cache_integrity_and_repair(self):
        with tempfile.TemporaryDirectory() as temp:
            downloader = Downloader(Path(temp))
            data = b'expected'
            spec = {'url': 'https://fixture/file', 'size': len(data), 'sha1': hashlib.sha1(data).hexdigest()}
            with patch('urllib.request.urlopen', side_effect=lambda *a, **k: io.BytesIO(data)) as request:
                path = downloader.fetch(spec)
                downloader.fetch(spec)
                self.assertEqual(request.call_count, 1)
                path.write_bytes(b'corrupted')
                downloader.fetch(spec)
                self.assertEqual(request.call_count, 2)
            with patch('urllib.request.urlopen', side_effect=lambda *a, **k: io.BytesIO(b'bad')), patch('time.sleep'):
                with self.assertRaisesRegex(ValueError, 'mismatch'):
                    downloader.fetch(spec, refresh=True)

    def test_full_build_validate_tamper_and_preserve(self):
        self._full_build_validate_tamper_and_preserve('local')

    def test_disabled_controller_vanilla_package_and_usb_copy(self):
        self._full_build_validate_tamper_and_preserve('desktop-usb', controller_layout=False)

    def test_build_preserves_instance_data_and_saves_by_default(self):
        self._full_build_validate_tamper_and_preserve('local-instance')

    def test_build_exclude_saves_preserves_other_custom_data(self):
        self._full_build_validate_tamper_and_preserve('local-instance', exclude_saves=True)

    def test_desktop_request_builds_with_shared_engine(self):
        self._full_build_validate_tamper_and_preserve('desktop')

    def test_discovered_vanilla_launcher_installations_build_with_shared_engine(self):
        for launcher in ('official', 'prism', 'multimc'):
            with self.subTest(launcher=launcher):
                self._full_build_validate_tamper_and_preserve('desktop-' + launcher)

    def test_exporter_cancellation_before_commit_does_not_publish(self):
        for stage in ('libraries', 'validation', 'export'):
            with self.subTest(stage=stage):
                self._full_build_validate_tamper_and_preserve('local', cancel_stage=stage)

    def test_cancellation_after_publication_keeps_validated_success(self):
        self._full_build_validate_tamper_and_preserve('local', cancel_stage='complete')

    def test_verified_usb_transfer_of_an_actual_fixture_export(self):
        self._full_build_validate_tamper_and_preserve('desktop-usb')

    def test_incomplete_fastutil_prevents_export_and_complete_event(self):
        for mode in ('local', 'desktop'):
            with self.subTest(mode=mode):
                self._full_build_validate_tamper_and_preserve(mode, missing_floatlist=True)

    def _full_build_validate_tamper_and_preserve(self, mode, missing_floatlist=False, cancel_stage=None, exclude_saves=False, controller_layout=True):
        native = archive({'windows/x64/lwjgl.dll': b'MZnative', 'META-INF/test': b'ignore'})
        java = archive({'jdk/bin/java.exe': pe(), 'jdk/bin/server/jvm.dll': pe(),
                        'jdk/release': b'JAVA_VERSION="25"\nOS_NAME="Windows"\nOS_ARCH="x86_64"'})
        asset = b'sound'
        sha = hashlib.sha1(asset).hexdigest()
        index = {'objects': {'sound/test': {'hash': sha, 'size': len(asset)}}}
        blobs = {'https://fixture/netty': archive({'io/netty/channel/unix/Errors.class': b'fixture-class'}),
                 'https://fixture/fastutil': archive({'it/unimi/dsi/fastutil/floats/FloatList.class': b'fixture-class'}),
                 'https://fixture/native': native, 'https://fixture/index': json.dumps(index).encode(),
                 f'https://resources.download.minecraft.net/{sha[:2]}/{sha}': asset,
                 'https://fixture/java': java}
        if missing_floatlist:
            blobs['https://fixture/fastutil'] = archive({'wrong/FloatList.class': b'fixture-class'})
        catalog = [{'binary': {'package': {'link': 'https://fixture/java', 'checksum': hashlib.sha256(java).hexdigest(), 'size': len(java)}}, 'release_name': 'jdk-25'}]
        def request(req, **kwargs):
            url = req.full_url
            if url.endswith('version_manifest_v2.json'):
                data = json.dumps({'versions': [{'id': 'fixture', 'url': 'https://fixture/version'}]}).encode()
            elif url == 'https://fixture/version':
                data = json.dumps(FIXTURE).encode()
            elif url.startswith('https://api.adoptium.net/'):
                data = json.dumps(catalog).encode()
            elif url == 'https://fixture/client':
                self.fail('Local mode requested a client download')
            else:
                data = blobs[url]
            return io.BytesIO(data)
        secrets = ['PRIVATE-MS', 'PRIVATE-XBOX', 'PRIVATE-XSTS', 'PRIVATE-MC']
        with tempfile.TemporaryDirectory() as temp, patch('urllib.request.urlopen', side_effect=request):
            root = Path(temp)
            output = root / 'Package With Spaces'
            source = ClientSource('local', version='fixture', client_jar=Path(__file__).parent / 'fixtures/client.jar')
            if mode == 'local-instance':
                instance = root / 'Existing Minecraft'
                (instance / 'saves/world').mkdir(parents=True)
                (instance / 'saves/world/level.dat').write_bytes(b'original world')
                (instance / 'custom-mod/token').mkdir(parents=True)
                (instance / 'custom-mod/token/account.bin').write_bytes(b'custom mod data')
                source = ClientSource('local', version='fixture', instance=instance,
                                      client_jar=Path(__file__).parent / 'fixtures/client.jar')
            events = []
            token = CancellationToken()
            def report(event):
                events.append(event)
                if event.stage == cancel_stage:
                    token.cancel()
            if mode.startswith('desktop'):
                fixture = Path(__file__).parent / 'fixtures'
                installation = Installation('Fixture', 'Portable', 'fixture', fixture, fixture / 'client.jar')
                if mode != 'desktop':
                    launcher_root = root / 'source'
                    if mode == 'desktop-official':
                        directory = launcher_root / 'versions/fixture'
                        directory.mkdir(parents=True)
                        local_metadata = copy.deepcopy(FIXTURE)
                        local_metadata['libraries'] = []  # Fresh Mojang metadata must supply dependencies.
                        (directory / 'fixture.json').write_text(json.dumps(local_metadata))
                        (directory / 'fixture.jar').write_bytes((fixture / 'client.jar').read_bytes())
                    else:
                        directory = launcher_root / 'instances/Example'
                        directory.mkdir(parents=True)
                        config = 'prismlauncher.cfg' if mode == 'desktop-prism' else 'multimc.cfg'
                        (launcher_root / config).write_text('[General]\n')
                        (directory / 'mmc-pack.json').write_text(json.dumps({'components': [{'uid': 'net.minecraft', 'version': 'fixture'}]}))
                        jar = launcher_root / 'libraries/com/mojang/minecraft/fixture/minecraft-fixture-client.jar'
                        jar.parent.mkdir(parents=True)
                        jar.write_bytes((fixture / 'client.jar').read_bytes())
                    installation = discover([launcher_root], include_defaults=False).installations[0]
                action = lambda: LocalBuildRequest(installation, output, root / 'cache', allow_runtime_download=True, controller_layout=controller_layout).run(report, token)
            else:
                action = lambda: build(output, Downloader(root / 'cache', token, allow_runtime_download=True), source, progress=report, cancellation=token, exclude_saves=exclude_saves, controller_layout=controller_layout)
            if cancel_stage and cancel_stage != 'complete':
                with self.assertRaises(OperationCancelled):
                    action()
                self.assertFalse(output.exists())
                self.assertNotIn('complete', [e.stage for e in events])
                return
            if missing_floatlist:
                with self.assertRaisesRegex(ValueError, 'FloatList'):
                    action()
                self.assertFalse(output.exists())
                self.assertNotIn('complete', [event.stage for event in events])
                self.assertNotIn('export', [event.stage for event in events])
                return
            action()
            self.assertEqual(validate(output), [])
            self.assertTrue((output / 'playden/recipe.json').is_file())
            document = json.loads((output / 'playden/recipe.json').read_text())
            self.assertEqual('defaults' in document, controller_layout)
            self.assertFalse((output / 'launch.args').exists())
            self.assertIn('Minecraft fixture', (output / 'PLAYDEN.txt').read_text())
            inventory = json.loads((output / 'package.json').read_text())
            self.assertIn('playden/recipe.json', inventory['files'])
            self.assertNotIn('launch.args', inventory['files'])
            # Even a rehashed recipe must match the package's version and paths.
            from minecraft_playden.recipe import generate as generate_recipe
            recipe_path = output / 'playden/recipe.json'
            original_recipe = recipe_path.read_bytes()
            manifest_path = output / 'package.json'
            original_manifest = manifest_path.read_bytes()
            invalid = generate_recipe('Minecraft other')
            recipe_path.write_text(json.dumps(invalid))
            inventory['files']['playden/recipe.json'] = hashlib.sha256(recipe_path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(inventory))
            self.assertTrue(any('Recipe differs' in error for error in validate(output)))
            recipe_path.write_bytes(original_recipe)
            manifest_path.write_bytes(original_manifest)
            for missing in ('playden/recipe.json', 'playden.args', 'java/bin/java.exe'):
                path = output / missing
                original = path.read_bytes()
                path.unlink()
                self.assertTrue(validate(output), missing)
                path.write_bytes(original)
            inventory = json.loads(original_manifest)
            del inventory['files']['playden/recipe.json']
            manifest_path.write_text(json.dumps(inventory))
            self.assertIn('Required file absent from inventory: playden/recipe.json', validate(output))
            manifest_path.write_bytes(original_manifest)
            self.assertEqual(validate(output), [])
            if checker := os.environ.get('PLAYDEN_RECIPE_CHECK'):
                result = subprocess.run([checker, str(output / 'playden/recipe.json')], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            if mode == 'local-instance':
                game = output / 'instances/default/minecraft'
                self.assertEqual((game / 'saves/world/level.dat').exists(), not exclude_saves)
                self.assertEqual((game / 'custom-mod/token/account.bin').read_bytes(), b'custom mod data')
                self.assertEqual((instance / 'saves/world/level.dat').read_bytes(), b'original world')
                inventory = json.loads((output / 'package.json').read_text())
                self.assertFalse(any(name.startswith('instances/') for name in inventory['files']))
                (game / 'custom-mod/token/account.bin').write_bytes(b'changed by game')
                self.assertEqual(validate(output), [])
            self.assertEqual(events[-1], Progress('complete', 'Package validated and ready for console testing.', 1, 1))
            for stage in ('libraries', 'assets'):
                updates = [e for e in events if e.stage == stage]
                self.assertEqual(updates[0].completed, 0)
                self.assertEqual(updates[-1].completed, updates[-1].total)
                self.assertEqual([e.completed for e in updates], sorted(e.completed for e in updates))
            classpath = json.loads((output / 'package.json').read_text())['classpath']
            self.assertEqual((output / 'playden.args').read_text(), generate(FIXTURE, classpath, '../../'))
            if mode == 'desktop-usb':
                usb = root / 'mounted USB'
                usb.mkdir()
                info = usb.stat()
                identity = VolumeIdentity('fixture', 'exfat', info.st_dev, info.st_ino)
                with patch('minecraft_playden.volumes._mounted_identity', return_value=identity), patch('minecraft_playden.transfer.flush_volume', side_effect=lambda drive: drive.check()):
                    transfer = TransferRequest(output, DriveTarget.capture(usb)).run()
                    self.assertEqual(validate(transfer.destination), [])
                    self.assertEqual({p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob('*') if p.is_file()},
                                     {p.relative_to(transfer.destination).as_posix(): p.read_bytes() for p in transfer.destination.rglob('*') if p.is_file()})

            for file in root.rglob('*'):
                if file.is_file():
                    for secret in secrets:
                        self.assertNotIn(secret.encode(), file.read_bytes(), str(file))
            # Reproduce the former validation blind spot: all inventory/metadata/
            # argfile records agree, but the required dependency was omitted.
            originals = {name: (output / name).read_bytes() for name in ('package.json', 'metadata/version.json', 'playden.args')}
            broken_metadata = copy.deepcopy(FIXTURE)
            broken_metadata['libraries'] = [lib for lib in broken_metadata['libraries'] if not lib['name'].startswith('it.unimi.dsi:fastutil:')]
            broken_cp = [entry for entry in classpath if 'fastutil' not in entry]
            (output / 'metadata/version.json').write_text(json.dumps(broken_metadata))
            for name, prefix in [('playden.args', '../../')]:
                (output / name).write_text(generate(broken_metadata, broken_cp, prefix))
            broken_inventory = json.loads(originals['package.json'])
            broken_inventory['classpath'] = broken_cp
            broken_inventory['files'] = {name: value for name, value in broken_inventory['files'].items() if 'fastutil' not in name}
            for name in ('metadata/version.json', 'playden.args'):
                broken_inventory['files'][name] = hashlib.sha256((output / name).read_bytes()).hexdigest()
            (output / 'package.json').write_text(json.dumps(broken_inventory))
            self.assertTrue(any('FloatList' in error for error in validate(output)))
            for name, data in originals.items():
                (output / name).write_bytes(data)
            self.assertEqual(validate(output), [])
            (output / 'instances/default/minecraft/saves').mkdir(exist_ok=True)
            (output / 'instances/default/minecraft/saves/new-world').write_text('keep')
            self.assertEqual(validate(output), [])
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                build(output, Downloader(root / 'cache'), source)
            manifest_path = output / 'package.json'
            manifest_text = manifest_path.read_text()
            manifest = json.loads(manifest_text)
            del manifest['files'][f'assets/objects/{sha[:2]}/{sha}']
            manifest_path.write_text(json.dumps(manifest))
            self.assertTrue(any('Required file absent' in x for x in validate(output)))
            manifest_path.write_text(manifest_text)
            (output / 'libraries/netty/common.jar').write_bytes(b'corrupt')
            self.assertTrue(any('Hash mismatch' in x for x in validate(output)))
            self.assertEqual((output / 'instances/default/minecraft/saves/new-world').read_text(), 'keep')

    def test_failed_build_does_not_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch('minecraft_playden.exporter.VersionResolver.resolve', side_effect=ValueError('bad metadata')):
                events = []
                with self.assertRaises(ValueError):
                    build(root / 'output', Downloader(root / 'cache'), ClientSource('local', version='fixture', client_jar=Path(__file__).parent / 'fixtures/client.jar'), progress=events.append)
                self.assertNotIn('complete', [e.stage for e in events])
            self.assertFalse((root / 'output').exists())
            self.assertEqual(list(root.glob('.playden-*')), [])

    def test_missing_manifest_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertTrue(validate(Path(temp)))


if __name__ == '__main__':
    unittest.main()
