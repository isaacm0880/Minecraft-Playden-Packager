"""Loader detection is distinct from supported vanilla client acquisition."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from minecraft_playden.desktop import LocalBuildRequest, build_readiness
from minecraft_playden.discovery import discover
from minecraft_playden.importers import MultiMCAdapter, OfficialAdapter, resolve_local

LOADERS = [
    ('Fabric', 'net.fabricmc.fabric-loader', 'net.fabricmc:fabric-loader:0.16.10', 'net.fabricmc.loader.impl.launch.knot.KnotClient'),
    ('Forge', 'net.minecraftforge', 'net.minecraftforge:forge:26.3-1', 'cpw.mods.bootstraplauncher.BootstrapLauncher'),
    ('NeoForge', 'net.neoforged', 'net.neoforged:neoforge:21.1.1', 'cpw.mods.bootstraplauncher.BootstrapLauncher'),
    ('Quilt', 'org.quiltmc.quilt-loader', 'org.quiltmc:quilt-loader:0.27.0', 'org.quiltmc.loader.impl.launch.knot.KnotClient'),
]


def vanilla(root, version='26.3', java=25):
    directory = root / 'versions' / version
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{version}.json').write_text(json.dumps({'id': version, 'mainClass': 'net.minecraft.client.main.Main', 'javaVersion': {'majorVersion': java}}))
    (directory / f'{version}.jar').write_bytes(b'local-client')
    return directory


class LoaderTests(unittest.TestCase):
    def test_prism_and_multimc_recognize_all_four_loaders_and_resolve_base_client(self):
        for name, uid, library, main in LOADERS:
            with self.subTest(loader=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                instance = root / 'instances/Modded'
                instance.mkdir(parents=True)
                (instance / 'mmc-pack.json').write_text(json.dumps({'components': [
                    {'uid': 'net.minecraft', 'version': '26.3'}, {'uid': uid, 'version': '1.2.3'}]}))
                jar = root / 'libraries/com/mojang/minecraft/26.3/minecraft-26.3-client.jar'
                jar.parent.mkdir(parents=True)
                jar.write_bytes(b'local-client')
                base = MultiMCAdapter().inspect(instance, None)
                self.assertEqual(base.version, '26.3')
                self.assertEqual(base.client, jar)
                self.assertEqual(base.loaders[0].name, name)
                row = discover([root], include_defaults=False).installations[0]
                self.assertEqual(row.version, '26.3')
                self.assertEqual(row.problem, '')
                self.assertEqual(row.base_problem, '')
                self.assertTrue(row.available)
                self.assertFalse(row.buildable)
                self.assertIn(name, row.packaging_problem)
                self.assertIn('main class', row.packaging_problem)
                request, reason = build_readiness(row, str(root / 'out'), str(root / 'cache'))
                self.assertIsNone(request)
                self.assertIn(name, reason)
                with patch('minecraft_playden.desktop.build') as engine:
                    with self.assertRaisesRegex(ValueError, name):
                        LocalBuildRequest(row, root / 'out', root / 'cache').run()
                    engine.assert_not_called()
                self.assertFalse((root / 'cache').exists())
                with self.assertRaisesRegex(ValueError, name):
                    resolve_local(instance, None)

    def test_official_loader_profiles_resolve_base_jar_not_profile_id(self):
        for name, uid, library, main in LOADERS:
            with self.subTest(loader=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                base = vanilla(root)
                profile = root / 'versions' / f'{name.lower()}-profile'
                profile.mkdir()
                (profile / f'{profile.name}.json').write_text(json.dumps({
                    'id': profile.name, 'inheritsFrom': '26.3', 'mainClass': main,
                    'libraries': [{'name': library}],
                }))
                found = discover([profile], include_defaults=False).installations[0]
                self.assertEqual(found.version, '26.3')
                self.assertEqual(found.client, base / '26.3.jar')
                self.assertEqual(found.loaders[0].name, name)
                self.assertTrue(found.available)
                self.assertFalse(found.buildable)
                self.assertIn(name, found.packaging_problem)
                with self.assertRaisesRegex(ValueError, name):
                    resolve_local(profile, None)

    def test_unsupported_base_version_is_not_reported_as_loader_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vanilla(root, '1.16.5', java=8)
            row = discover([root], include_defaults=False).installations[0]
            self.assertEqual(row.loader_label, 'Vanilla')
            self.assertEqual(row.packaging_problem, '')
            self.assertIn('Java 8', row.base_problem)
            self.assertFalse(row.buildable)

    def test_inherited_unsupported_base_and_loader_have_separate_reasons(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vanilla(root, '1.16.5', java=8)
            profile = root / 'versions/fabric-profile'
            profile.mkdir()
            (profile / 'fabric-profile.json').write_text(json.dumps({'id': 'fabric-profile', 'inheritsFrom': '1.16.5', 'mainClass': LOADERS[0][3]}))
            row = discover([profile], include_defaults=False).installations[0]
            self.assertIn('Java 8', row.base_problem)
            self.assertIn('Fabric', row.packaging_problem)
            self.assertIn('Java 8', row.build_problem)
            self.assertIn('Fabric', row.build_problem)

    def test_unknown_components_cannot_silently_become_vanilla(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            instance = root / 'instances/Custom'
            instance.mkdir(parents=True)
            (instance / 'mmc-pack.json').write_text(json.dumps({'components': [{'uid': 'net.minecraft', 'version': '26.3'}, {'uid': 'custom.loader', 'version': '1'}]}))
            jar = root / 'libraries/com/mojang/minecraft/26.3/minecraft-26.3-client.jar'
            jar.parent.mkdir(parents=True)
            jar.write_bytes(b'local-client')
            row = discover([root], include_defaults=False).installations[0]
            self.assertTrue(row.available)
            self.assertFalse(row.buildable)
            self.assertEqual(row.loader_label, 'Custom profile')
            self.assertIn('custom.loader', row.packaging_problem)

    def test_loader_libraries_in_prism_patches_are_detected_and_cannot_be_dropped(self):
        for name, uid, library, main in LOADERS:
            with self.subTest(loader=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                instance = root / 'instances/Patched'
                (instance / 'patches').mkdir(parents=True)
                (instance / 'mmc-pack.json').write_text(json.dumps({'components': [{'uid': 'net.minecraft', 'version': '26.3'}]}))
                (instance / 'patches/custom.json').write_text(json.dumps({'mainClass': main, '+libraries': [{'name': library}]}))
                jar = root / 'libraries/com/mojang/minecraft/26.3/minecraft-26.3-client.jar'
                jar.parent.mkdir(parents=True)
                jar.write_bytes(b'client')
                row = discover([root], include_defaults=False).installations[0]
                self.assertEqual(row.version, '26.3')
                self.assertEqual(row.loaders[0].name, name)
                self.assertTrue(row.available)
                self.assertFalse(row.buildable)
                self.assertIn('patches', row.packaging_problem)

    def test_profile_inheritance_cycle_and_missing_parent_are_errors(self):
        for parent in ['cycle', 'missing', '../escape']:
            with self.subTest(parent=parent), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp) / 'versions/cycle'
                directory.mkdir(parents=True)
                (directory / 'cycle.json').write_text(json.dumps({'id': 'cycle', 'inheritsFrom': parent}))
                row = discover([directory], include_defaults=False).installations[0]
                self.assertFalse(row.buildable)
                self.assertTrue(row.problem)

    def test_loader_added_after_discovery_cannot_be_stripped_by_desktop_handoff(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = vanilla(root)
            row = discover([root], include_defaults=False).installations[0]
            self.assertTrue(row.buildable)
            metadata = json.loads((directory / '26.3.json').read_text())
            metadata['libraries'] = [{'name': LOADERS[0][2]}]
            (directory / '26.3.json').write_text(json.dumps(metadata))
            with patch('minecraft_playden.desktop.build') as engine:
                with self.assertRaisesRegex(ValueError, 'Fabric'):
                    LocalBuildRequest(row, root / 'output', root / 'cache').run()
                engine.assert_not_called()
