"""Phase 2A preserves instance content without claiming a working modded launch."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from minecraft_playden.cli import main
from minecraft_playden.instance_content import import_instance
from minecraft_playden.sources import ClientSource
from minecraft_playden.validator import validate
from minecraft_playden.volumes import publish_without_replace


class InstanceContentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.instance = self.root / 'prism/instances/My Instance'
        self.game = self.instance / '.minecraft'
        self.game.mkdir(parents=True)
        self.pack = {'components': [{'uid': 'net.minecraft', 'version': '26.3'},
                                    {'uid': 'net.fabricmc.fabric-loader', 'version': '0.20.1'}]}
        self.put('mmc-pack.json', json.dumps(self.pack), self.instance)
        self.output = self.root / 'Imported Package'

    def put(self, name, data='fixture', root=None):
        path = (root or self.game) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        return path

    def run_cli(self, arguments):
        with patch('sys.argv', ['minecraft-playden', *arguments]), contextlib.redirect_stdout(io.StringIO()):
            return main()

    def test_default_preserves_worlds_datapacks_options_and_unknown_mod_files(self):
        paths = ['mods/mod.jar', 'mods/mod.jar.disabled', 'config/custom/account.json',
                 'config/token', 'config/logs/history.txt', 'resourcepacks/pack.zip',
                 'shaderpacks/shader.zip', 'saves/My World/level.dat',
                 'saves/My World/datapacks/custom/data/x.json', 'options.txt', 'optionsof.txt',
                 'custom-mod-data/unknown.extension', '.custom-state/account/token',
                 'cache/needed-by-mod.dat', 'accounts.json', 'token', 'account',
                 'custom-config/saves/data.bin']
        for name in paths:
            self.put(name, b'\x00unaltered\xff\r\n')
        (self.game / 'empty-directory').mkdir()
        self.put('custom-wrapper-data/token', 'wrapper', self.instance)
        self.put('patches/custom.json', '{"custom": true}', self.instance)
        before = {p.relative_to(self.instance): p.read_bytes() for p in self.instance.rglob('*') if p.is_file()}
        with patch('urllib.request.urlopen', side_effect=AssertionError('File import must be offline')):
            import_instance(self.instance, self.output)
        for name in paths:
            self.assertEqual((self.output / 'instances/default/minecraft' / name).read_bytes(), b'\x00unaltered\xff\r\n')
        self.assertTrue((self.output / 'instances/default/minecraft/empty-directory').is_dir())
        self.assertEqual((self.output / 'instances/default/instance-files/custom-wrapper-data/token').read_text(), 'wrapper')
        self.assertEqual((self.output / 'metadata/local-instance/mmc-pack.json').read_bytes(), before[Path('mmc-pack.json')])
        self.assertEqual((self.output / 'metadata/local-instance/patches/custom.json').read_bytes(), before[Path('patches/custom.json')])
        self.assertEqual({p.relative_to(self.instance): p.read_bytes() for p in self.instance.rglob('*') if p.is_file()}, before)
        self.assertEqual(validate(self.output), [])
        manifest = json.loads((self.output / 'package.json').read_text())
        self.assertFalse(any(name.startswith('instances/') for name in manifest['files']))
        self.assertEqual(manifest['launch_status'], 'unverified')
        self.assertFalse((self.output / 'playden.args').exists())
        self.assertFalse((self.output / 'playden/recipe.json').exists())
        self.assertFalse((self.output / 'launch.args').exists())
        self.assertFalse((self.output / 'java').exists())
        # Playing/changing imported data never invalidates immutable metadata.
        (self.output / 'instances/default/minecraft/options.txt').write_text('changed')
        self.assertEqual(validate(self.output), [])

    def test_exclusions_are_exact_and_root_scoped(self):
        excluded_game = ['launcher_accounts.json', 'LAUNCHER_ACCOUNTS.JSON', 'launcher_accounts_microsoft_store.json',
                         'launcher_profiles.json', 'launcher_profiles_microsoft_store.json',
                         'launcher_log.txt', 'launcher_log.txt.2', 'logs/latest.log',
                         'crash-reports/crash.txt', '.cache/temp', 'webcache/file', 'tmp/file',
                         '.tmp/file', 'launcher.exe']
        excluded_wrapper = ['accounts.json', 'instance.cfg', 'instance.cfg.bak', 'metacache/file', 'logs/log']
        for name in excluded_game:
            self.put(name, 'PRIVATE-CREDENTIAL-OR-TEMP')
            self.put('mod-files/' + name, 'legitimate-mod-content')
        for name in excluded_wrapper:
            self.put(name, 'PRIVATE-LAUNCHER', self.instance)
        # Excluded account databases are skipped without reading their contents.
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path in [self.game / name for name in excluded_game] + [self.instance / name for name in excluded_wrapper]:
                raise AssertionError('Excluded file was opened')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            import_instance(self.instance, self.output)
        for name in excluded_game:
            self.assertFalse((self.output / 'instances/default/minecraft' / name).exists())
            self.assertEqual((self.output / 'instances/default/minecraft/mod-files' / name).read_text(), 'legitimate-mod-content')
        for path in self.output.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'PRIVATE-', path.read_bytes())

    def test_cli_defaults_to_saves_and_exclude_saves_only_omits_root_worlds(self):
        self.put('saves/world/level.dat', 'world')
        self.put('config/mod/saves/state', 'custom')
        for exclude in (False, True):
            output = self.root / ('without-worlds' if exclude else 'with-worlds')
            arguments = ['import-instance', '--instance', str(self.instance), '--output', str(output)]
            if exclude:
                arguments.append('--exclude-saves')
            self.assertEqual(self.run_cli(arguments), 0)
            self.assertEqual((output / 'instances/default/minecraft/saves').exists(), not exclude)
            self.assertEqual((output / 'instances/default/minecraft/config/mod/saves/state').read_text(), 'custom')
            self.assertEqual(self.run_cli(['validate', str(output)]), 0)
        self.assertTrue((self.game / 'saves/world/level.dat').is_file())
        with patch('sys.argv', ['minecraft-playden', 'import-instance', '--instance', str(self.instance),
                                '--output', str(self.output), '--include-saves']), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main()

    def test_build_flag_is_forwarded_without_include_saves(self):
        for exclude in (False, True):
            argv = ['minecraft-playden', 'build', '--source', 'local', '--client-jar', 'client.jar',
                    '--version', '26.3', '--output', str(self.output)]
            if exclude:
                argv.append('--exclude-saves')
            with patch('sys.argv', argv), patch('minecraft_playden.cli.build') as build, \
                    patch('minecraft_playden.cli.Downloader'), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
                self.assertEqual(build.call_args.kwargs['exclude_saves'], exclude)

    def test_all_loaders_detected_and_metadata_preserved_without_launch_resolution(self):
        for loader, uid in [('Vanilla', None), ('Fabric', 'net.fabricmc.fabric-loader'),
                            ('Forge', 'net.minecraftforge'), ('NeoForge', 'net.neoforged.neoforge')]:
            components = [{'uid': 'net.minecraft', 'version': '26.3'}]
            if uid:
                components.append({'uid': uid, 'version': '1.2.3'})
            self.put('mmc-pack.json', json.dumps({'components': components}), self.instance)
            output = self.root / loader
            import_instance(self.instance, output)
            report = json.loads((output / 'metadata/instance-import.json').read_text())
            self.assertIn(loader, report['loader'])
            self.assertEqual(report['launch_status'], 'unverified')
            self.assertEqual((output / 'metadata/local-instance/mmc-pack.json').read_bytes(), (self.instance / 'mmc-pack.json').read_bytes())

    def test_official_inherited_loader_metadata_and_parent_files_preserved(self):
        official = self.root / 'official'
        self.put('versions/base/base.json', json.dumps({'id': 'base', 'mainClass': 'net.minecraft.client.main.Main'}), official)
        self.put('versions/base/base.jar', 'client', official)
        self.put('versions/forge/forge.json', json.dumps({'id': 'forge', 'inheritsFrom': 'base',
                 'libraries': [{'name': 'net.minecraftforge:forge:1.2.3'}]}), official)
        self.put('saves/world/level.dat', 'world', official)
        import_instance(official / 'versions/forge', self.output)
        report = json.loads((self.output / 'metadata/instance-import.json').read_text())
        self.assertEqual(report['version'], 'base')
        self.assertIn('Forge', report['loader'])
        self.assertTrue((self.output / 'instances/default/minecraft/versions/base/base.json').is_file())
        self.assertTrue((self.output / 'metadata/local-instance/versions/forge/forge.json').is_file())

    def test_escaping_links_and_cycles_fail_without_publication(self):
        outside = self.put('outside', 'private', self.root)
        for target in (outside, self.game):
            link = self.game / 'link'
            try:
                link.symlink_to(target, target_is_directory=target.is_dir())
            except OSError:
                self.skipTest('Symlinks unavailable on this host')
            with self.assertRaises((ValueError, OSError)):
                import_instance(self.instance, self.output)
            self.assertFalse(self.output.exists())
            self.assertEqual(list(self.root.glob('.playden-import-*')), [])
            link.unlink()
        self.assertEqual(outside.read_text(), 'private')

    def test_links_cannot_smuggle_excluded_credentials(self):
        secret = self.put('launcher_accounts.json', 'PRIVATE-CREDENTIAL')
        try:
            (self.game / 'alias').symlink_to(secret)
        except OSError:
            self.skipTest('Symlinks unavailable on this host')
        with self.assertRaisesRegex(ValueError, 'excluded file'):
            import_instance(self.instance, self.output)
        self.assertFalse(self.output.exists())

    def test_metadata_links_cannot_escape_or_read_account_databases(self):
        original = self.instance / 'mmc-pack.json'
        original.unlink()
        private = self.put('accounts.json', json.dumps(self.pack), self.instance)
        try:
            original.symlink_to(private)
        except OSError:
            self.skipTest('Symlinks unavailable on this host')
        with self.assertRaisesRegex(ValueError, 'excluded launcher data'):
            import_instance(self.instance, self.output)
        self.assertFalse(self.output.exists())

    def test_contained_symlinks_copy_contents(self):
        self.put('custom/data', 'unaltered')
        try:
            (self.game / 'alias').symlink_to(self.game / 'custom', target_is_directory=True)
        except OSError:
            self.skipTest('Symlinks unavailable on this host')
        import_instance(self.instance, self.output)
        target = self.output / 'instances/default/minecraft/alias'
        self.assertFalse(target.is_symlink())
        self.assertEqual((target / 'data').read_text(), 'unaltered')

    def test_refuses_existing_overlapping_outputs_and_dangling_links(self):
        self.output.mkdir()
        self.put('keep', 'existing', self.output)
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            import_instance(self.instance, self.output)
        self.assertEqual((self.output / 'keep').read_text(), 'existing')
        for output in [self.instance / 'new', self.instance.parent]:
            with self.assertRaisesRegex(ValueError, 'separate'):
                import_instance(self.instance, output)
        self.assertFalse((self.instance / 'new').exists())

    def test_publication_race_preserves_newly_existing_output(self):
        def concurrent_output(source, destination):
            destination.mkdir()
            (destination / 'keep').write_text('other process')
            publish_without_replace(source, destination)
        with patch('minecraft_playden.instance_content.publish_without_replace', side_effect=concurrent_output):
            with self.assertRaises(FileExistsError):
                import_instance(self.instance, self.output)
        self.assertEqual((self.output / 'keep').read_text(), 'other process')
        self.assertEqual(list(self.root.glob('.playden-import-*')), [])

    def test_dangling_output_link_is_refused(self):
        try:
            self.output.symlink_to(self.root / 'missing', target_is_directory=True)
        except OSError:
            self.skipTest('Symlinks unavailable on this host')
        with self.assertRaisesRegex(ValueError, 'overwrite'):
            import_instance(self.instance, self.output)
        self.assertTrue(self.output.is_symlink())

    def test_inheritance_traversal_is_rejected(self):
        official = self.root / 'official'
        self.put('versions/custom/custom.json', json.dumps({'id': 'custom', 'inheritsFrom': '../outside'}), official)
        with self.assertRaises(ValueError):
            import_instance(official, self.output)
        self.assertFalse(self.output.exists())

    def test_cli_refuses_a_cache_inside_source_before_creating_it(self):
        cache = self.game / 'new-cache'
        arguments = ['build', '--source', 'local', '--instance', str(self.instance), '--output', str(self.output)]
        with patch('sys.argv', ['minecraft-playden', '--cache', str(cache), *arguments]), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 1)
        self.assertFalse(cache.exists())
        self.assertFalse(self.output.exists())

    def test_file_only_import_rejects_a_misleading_launch_recipe(self):
        import_instance(self.instance, self.output)
        from minecraft_playden.recipe import write
        write(self.output, 'Minecraft 26.3')
        self.assertTrue(any('file-only instance import' in error for error in validate(self.output)))

    def test_user_recipe_file_is_preserved_as_data_without_root_launch_recipe(self):
        self.put('recipe.json', '{"custom mod data":true}')
        import_instance(self.instance, self.output)
        self.assertFalse((self.output / 'playden/recipe.json').exists())
        self.assertEqual((self.output / 'instances/default/minecraft/recipe.json').read_bytes(),
                         (self.game / 'recipe.json').read_bytes())
        self.assertEqual(validate(self.output), [])

    def test_metadata_tamper_and_status_claim_fail_validation(self):
        import_instance(self.instance, self.output)
        report = self.output / 'metadata/instance-import.json'
        report.write_text('{}')
        self.assertTrue(validate(self.output))

    def test_build_rejects_loader_even_with_explicit_client_jar(self):
        client = self.put('client.jar', 'client', self.root)
        with self.assertRaisesRegex(ValueError, 'Phase 2B'):
            ClientSource('local', '26.3', self.instance, client).prepare()

    def test_launcher_application_root_is_not_imported_as_an_instance(self):
        launcher = self.instance.parent.parent
        self.put('prismlauncher.cfg', '[General]', launcher)
        self.put('accounts.json', 'PRIVATE-CREDENTIAL', launcher)
        with self.assertRaisesRegex(ValueError, 'launcher application'):
            import_instance(launcher, self.output)
        self.assertFalse(self.output.exists())

    def test_unknown_layout_preserves_every_unfamiliar_file(self):
        source = self.root / 'unknown'
        self.put('mod-data/token', 'keep', source)
        import_instance(source, self.output)
        self.assertEqual(json.loads((self.output / 'metadata/instance-import.json').read_text())['loader'], 'Unknown')
        self.assertEqual((self.output / 'instances/default/minecraft/mod-data/token').read_text(), 'keep')

    @unittest.skipUnless(os.name != 'nt', 'Colon filename cannot be created on Windows')
    def test_nonportable_path_fails_explicitly_instead_of_silently_omitting(self):
        self.put('bad:name', 'keep')
        with self.assertRaisesRegex(ValueError, 'relocated safely'):
            import_instance(self.instance, self.output)
        self.assertFalse(self.output.exists())
