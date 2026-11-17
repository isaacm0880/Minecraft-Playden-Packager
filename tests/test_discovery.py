"""Read-only launcher discovery and GUI-independent build request regression tests."""
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from minecraft_playden.desktop import LocalBuildRequest, build_readiness, suggest_output
from minecraft_playden.discovery import Installation, discover
from minecraft_playden.downloads import default_cache


def official(root, version='26.3', missing=False, **extra):
    directory = root / 'versions' / version
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{version}.json').write_text(json.dumps({'id': version, **extra}))
    if not missing:
        (directory / f'{version}.jar').write_bytes(b'local-client')
    return directory


def prism(root, name='Test', version='26.3', instances=None, libraries=None, missing=False, modded=False):
    directory = (instances or root / 'instances') / name
    directory.mkdir(parents=True, exist_ok=True)
    components = [{'uid': 'net.minecraft', 'version': version}, {'uid': 'org.lwjgl3', 'version': '3'}]
    if modded:
        components.append({'uid': 'net.fabricmc.fabric-loader', 'version': '1'})
    (directory / 'mmc-pack.json').write_text(json.dumps({'components': components}))
    (directory / 'instance.cfg').write_text('[General]\nname=My 100% Minecraft World\n')
    jar = (libraries or root / 'libraries') / 'com/mojang/minecraft' / version / f'minecraft-{version}-client.jar'
    if not missing:
        jar.parent.mkdir(parents=True, exist_ok=True)
        jar.write_bytes(b'local-client')
    return directory, jar


class DiscoveryTests(unittest.TestCase):
    def test_empty_cache_environment_does_not_use_working_directory(self):
        with patch('minecraft_playden.downloads.sys.platform', 'linux'), patch.dict(os.environ, {'LOCALAPPDATA': '', 'XDG_CACHE_HOME': ''}), patch('pathlib.Path.home', return_value=Path('/test-home')):
            self.assertEqual(default_cache(), Path('/test-home/.cache/minecraft-playden'))

    def test_windows_portable_launchers_and_empty_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / 'User With Spaces'
            official(home / 'AppData/Roaming/.minecraft')
            prism(home / 'Downloads/PrismLauncher')
            prism(home / 'Desktop/MultiMC')
            prism(home / 'AppData/Local/Programs/MultiMC')
            result = discover(home=home, platform='win32', environ={'APPDATA': '', 'LOCALAPPDATA': ''})
            self.assertEqual(len(result.installations), 4)
            self.assertTrue(all(i.available for i in result.installations))
            self.assertEqual(result.warnings, ())

    def test_linux_official_multiple_versions_prism_multimc_flatpak(self):
        with tempfile.TemporaryDirectory() as temp, patch('urllib.request.urlopen') as network:
            home = Path(temp)
            official(home / '.minecraft', '26.3')
            official(home / '.minecraft', '1.21.1')
            prism(home / '.local/share/PrismLauncher')
            prism(home / '.local/share/multimc')
            prism(home / '.var/app/org.prismlauncher.PrismLauncher/data/PrismLauncher')
            result = discover(home=home, platform='linux', environ={})
            self.assertEqual(len(result.installations), 5)
            self.assertTrue(all(i.available for i in result.installations))
            self.assertEqual({i.version for i in result.installations}, {'26.3', '1.21.1'})
            self.assertIn('Prism Launcher (Flatpak)', {i.launcher for i in result.installations})
            self.assertIn('MultiMC', {i.launcher for i in result.installations})
            self.assertTrue(any(i.name == 'My 100% Minecraft World' for i in result.installations))
            network.assert_not_called()

    def test_windows_environment_locations(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            roaming, local = home / 'Roaming', home / 'Local'
            official(roaming / '.minecraft')
            prism(roaming / 'PrismLauncher')
            prism(local / 'MultiMC')
            result = discover(home=home, platform='win32', environ={'APPDATA': str(roaming), 'LOCALAPPDATA': str(local)})
            self.assertEqual(len(result.installations), 3)
            self.assertEqual({i.launcher for i in result.installations}, {'Official launcher', 'Prism Launcher', 'MultiMC'})
            self.assertTrue(all(i.available for i in result.installations))

    def test_xdg_override(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            data = home / 'custom-data'
            prism(data / 'PrismLauncher')
            result = discover(home=home, platform='linux', environ={'XDG_DATA_HOME': str(data)})
            self.assertEqual(len(result.installations), 1)
            self.assertTrue(result.installations[0].available)

    def test_manual_root_instance_game_directory_and_version_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            version = official(root / 'custom minecraft')
            instance, jar = prism(root / 'portable launcher')
            (instance / '.minecraft').mkdir()
            result = discover([root / 'custom minecraft', version, root / 'portable launcher', instance, instance / '.minecraft'], include_defaults=False)
            self.assertEqual(len(result.installations), 2)
            self.assertTrue(all(i.available for i in result.installations))
            self.assertEqual(result.warnings, ())
            self.assertIn(jar, [i.client for i in result.installations])

    def test_custom_instance_and_libraries_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'launcher'
            root.mkdir()
            instance, jar = prism(root, instances=root / 'my games', libraries=root / 'my libraries')
            (root / 'prismlauncher.cfg').write_text('[General]\nInstanceDir=my games\nLibrariesDir=my libraries\n')
            result = discover([root], include_defaults=False)
            self.assertEqual(len(result.installations), 1)
            self.assertEqual(result.installations[0].client, jar)
            self.assertEqual(result.installations[0].instance, instance)
            self.assertEqual(result.installations[0].launcher, 'Prism Launcher')
            self.assertTrue(result.installations[0].available)
            direct = discover([instance], include_defaults=False)
            self.assertEqual(direct.installations[0].client, jar)

    def test_absolute_custom_instance_location_and_libraries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'MultiMC'
            root.mkdir()
            instances, libraries = root.parent / 'external instances', root.parent / 'external libraries'
            instance, jar = prism(root, instances=instances, libraries=libraries)
            (root / 'multimc.cfg').write_text(f'[General]\nInstanceDir={instances.as_posix()}\nLibrariesDir={libraries.as_posix()}\n')
            result = discover([instance, root], include_defaults=False)
            self.assertEqual(len(result.installations), 1)
            self.assertEqual(result.installations[0].client, jar)
            self.assertTrue(result.installations[0].available)

    def test_missing_malformed_and_modded_instances_are_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            official(root, 'missing', missing=True)
            directory = official(root, 'broken')
            (directory / 'broken.json').write_text('{')
            official(root, 'fabric', inheritsFrom='26.3')
            result = discover([root], include_defaults=False)
            self.assertEqual(len(result.installations), 3)
            self.assertTrue(all(not i.available and i.problem for i in result.installations))
            other = root / 'PrismLauncher'
            prism(other, modded=True)
            result = discover([other], include_defaults=False)
            self.assertEqual(result.installations[0].version, '26.3')
            self.assertEqual(result.installations[0].problem, '')
            self.assertTrue(result.installations[0].available)
            self.assertFalse(result.installations[0].buildable)
            self.assertEqual(result.installations[0].loader_label, 'Fabric 1')
            self.assertIn('Modded packaging', result.installations[0].packaging_problem)

    def test_missing_prism_client_and_detached_instance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            instance, _ = prism(root, missing=True)
            result = discover([instance], include_defaults=False)
            self.assertIn('missing', result.installations[0].problem)
            self.assertFalse(result.installations[0].available)
            detached = root / 'detached'
            detached.mkdir()
            (detached / 'mmc-pack.json').write_text(json.dumps({'components': [{'uid': 'net.minecraft', 'version': '26.3'}]}))
            result = discover([detached], include_defaults=False)
            self.assertIn('Detached', result.installations[0].problem)

    def test_portable_package_and_disappearing_jar(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'metadata').mkdir()
            (root / 'metadata/version.json').write_text('{"id":"26.3"}')
            (root / 'versions').mkdir()
            jar = root / 'versions/client.jar'
            jar.write_bytes(b'client')
            result = discover([root], include_defaults=False)
            self.assertEqual(result.installations[0].launcher, 'Portable package')
            self.assertTrue(result.installations[0].available)
            jar.unlink()
            self.assertFalse(result.installations[0].available)

    def test_no_matches_errors_and_unreadable_root_do_not_abort(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = discover([root / 'missing', root], include_defaults=False)
            self.assertEqual(result.installations, ())
            self.assertEqual(len(result.warnings), 2)
            (root / 'versions').mkdir()
            with patch('pathlib.Path.iterdir', side_effect=PermissionError('denied')):
                result = discover([root], include_defaults=False)
                self.assertTrue(result.warnings)

    def test_malformed_launcher_config_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'prismlauncher.cfg').write_text('malformed config')
            result = discover([root], include_defaults=False)
            self.assertEqual(result.installations, ())
            self.assertTrue(result.warnings)

    def test_discovery_preserves_sources_and_never_reads_account_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            official(root / 'official')
            prism(root / 'PrismLauncher')
            account = root / 'official/launcher_accounts.json'
            account.write_text('PRIVATE-ACCOUNT-DATA')
            before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
            original_open = Path.open
            def checked_open(path, *args, **kwargs):
                self.assertNotIn(path.name, ('accounts.json', 'launcher_accounts.json', 'launcher_profiles.json'))
                return original_open(path, *args, **kwargs)
            with patch('pathlib.Path.open', checked_open):
                result = discover([root / 'official', root / 'PrismLauncher'], include_defaults=False)
            self.assertEqual(len(result.installations), 2)
            self.assertEqual(before, {p: p.read_bytes() for p in root.rglob('*') if p.is_file()})
            self.assertNotIn('PRIVATE-ACCOUNT-DATA', repr(result))

    def test_does_not_scan_arbitrary_descendants_or_account_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            official(root / 'unrelated/deep/game')
            (root / 'accounts.json').write_text('PRIVATE-ACCOUNT-DATA')
            result = discover([root], include_defaults=False)
            self.assertFalse(result.installations)
            self.assertNotIn('PRIVATE-ACCOUNT-DATA', repr(result))

    @unittest.skipIf(os.name == 'nt', 'Windows symlinks may require privileges')
    def test_symlink_roots_deduplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            official(root / 'game')
            (root / 'alias').symlink_to(root / 'game', target_is_directory=True)
            result = discover([root / 'game', root / 'alias'], include_defaults=False)
            self.assertEqual(len(result.installations), 1)


class DesktopLogicTests(unittest.TestCase):
    def installation(self, root):
        instance = official(root / 'source')
        return Installation('Minecraft', 'Official', '26.3', instance, instance / '26.3.jar')

    def test_request_calls_only_existing_local_engine(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installation = self.installation(root)
            request = LocalBuildRequest(installation, root / 'new package', root / 'cache')
            callback = Mock()
            with patch('minecraft_playden.desktop.build') as exporter, patch('minecraft_playden.desktop.package_status', return_value=None):
                request.run(callback)
                call = exporter.call_args
                self.assertEqual(call.args[0], request.output)
                self.assertEqual(call.args[2].mode, 'local')
                self.assertEqual(call.args[2].client_jar, installation.client)
                self.assertEqual(call.args[2].version, '26.3')
                self.assertIs(call.kwargs['progress'], callback)
                self.assertEqual(call.kwargs['prepare_fabric'], 'auto')

    def test_existing_output_source_overlap_cache_overlap_and_missing_client(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installation = self.installation(root)
            existing = root / 'successful build'
            existing.mkdir()
            (existing / 'sentinel').write_text('keep')
            for output, cache in [(existing, root / 'cache'), (root / 'source/new', root / 'cache'),
                                  (root / 'output', installation.instance / 'cache'), (root / 'cache/output', root / 'cache')]:
                with self.subTest(output=output, cache=cache), self.assertRaises(ValueError):
                    LocalBuildRequest(installation, output, cache).validate()
            self.assertEqual((existing / 'sentinel').read_text(), 'keep')
            installation.client.unlink()
            with self.assertRaises(ValueError):
                LocalBuildRequest(installation, root / 'out', root / 'cache').validate()

    def test_custom_launcher_root_is_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installation = replace(self.installation(root), launcher_root=root / 'custom-launcher')
            with self.assertRaisesRegex(ValueError, 'source installation'):
                LocalBuildRequest(installation, root / 'custom-launcher/output', root / 'cache').validate()
            installation = replace(installation, libraries_root=root / 'external libraries')
            with self.assertRaisesRegex(ValueError, 'source installation'):
                LocalBuildRequest(installation, root / 'external libraries/output', root / 'cache').validate()

    def test_output_suggestion_skips_existing_packages_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installation = self.installation(root)
            first = suggest_output(installation, root)
            self.assertFalse(first.exists())
            first.mkdir(parents=True)
            (first / 'world').write_text('keep')
            second = suggest_output(installation, root)
            self.assertNotEqual(first, second)
            self.assertFalse(second.exists())
            self.assertEqual((first / 'world').read_text(), 'keep')

    def test_readiness_reports_busy_missing_fields_and_path_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installation = self.installation(root)
            request, reason = build_readiness(None, '', '')
            self.assertIsNone(request)
            self.assertIn('Select', reason)
            request, reason = build_readiness(installation, '', '')
            self.assertIsNone(request)
            self.assertIn('output folder', reason)
            self.assertIn('cache folder', reason)
            request, reason = build_readiness(installation, str(root / 'out'), str(root / 'cache'), busy=True)
            self.assertIsNone(request)
            self.assertIn('in progress', reason)
            request, reason = build_readiness(installation, str(root / 'out'), str(root / 'cache'))
            self.assertIsNotNone(request)
            self.assertEqual(reason, '')

    def test_problem_instance_cannot_build_or_create_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installation = replace(self.installation(root), problem='Unsupported instance')
            with patch('minecraft_playden.desktop.build') as engine:
                with self.assertRaisesRegex(ValueError, 'Unsupported'):
                    LocalBuildRequest(installation, root / 'out', root / 'cache').run()
                engine.assert_not_called()
            self.assertFalse((root / 'cache').exists())


if __name__ == '__main__':
    unittest.main()
