"""Reference reproduction must be explicit, portable and strictly validated."""
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

from minecraft_playden import arguments, dependencies, profiles, recipe
from minecraft_playden.downloads import Downloader
from minecraft_playden.exporter import build
from minecraft_playden.sources import ClientSource
from minecraft_playden.importers import resolve_local
from minecraft_playden.validator import validate

FIXTURES = Path(__file__).parent / 'fixtures'
MINECRAFT = json.loads((FIXTURES / '26.3.json').read_text())


def zipped(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return out.getvalue()


class ProfileTests(unittest.TestCase):
    def test_reference_arguments_match_supplied_working_build_byte_for_byte(self):
        cp = profiles.classpath(MINECRAFT, profiles.REFERENCE_PROFILE)
        result = arguments.generate(MINECRAFT, cp, '../../', 'Player', profiles.REFERENCE_PROFILE)
        self.assertEqual(result, (FIXTURES / 'reference-26.3-playden.args').read_text())
        self.assertEqual(len(cp), 55)
        self.assertEqual(profiles.client_path(MINECRAFT, profiles.REFERENCE_PROFILE), 'libraries/com/mojang/minecraft/26.3/minecraft-26.3-client.jar')
        self.assertTrue(cp[-1].endswith('netty-transport-native-unix-common-4.2.16.Final.jar'))
        self.assertFalse(any('natives-windows' in path for path in cp))

    def test_default_profile_is_unchanged_and_does_not_invent_spng_requirement(self):
        cp = profiles.classpath(MINECRAFT)
        self.assertEqual(arguments.generate(MINECRAFT, cp, '../../'), (FIXTURES / 'official-26.3-playden.args').read_text())
        self.assertEqual(len(cp), 65)
        self.assertEqual(profiles.client_path(MINECRAFT), 'versions/client.jar')
        self.assertFalse(any('lwjgl-spng' in path for path in cp))

    def test_profiles_do_not_mutate_official_metadata_or_apply_to_other_versions(self):
        original = copy.deepcopy(MINECRAFT)
        effective = profiles.effective_metadata(MINECRAFT, profiles.REFERENCE_PROFILE)
        self.assertEqual(MINECRAFT, original)
        self.assertTrue(any(lib['name'] == 'org.lwjgl:lwjgl-spng:3.4.3:natives-windows' for lib in effective['libraries']))
        other = copy.deepcopy(MINECRAFT)
        other['id'] = '1.21.1'
        with self.assertRaisesRegex(ValueError, '26.3'):
            profiles.classpath(other, profiles.REFERENCE_PROFILE)
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            profiles.classpath(MINECRAFT, 'unknown')

    def test_reference_profile_keeps_quoting_when_a_value_requires_it(self):
        cp = profiles.classpath(MINECRAFT, profiles.REFERENCE_PROFILE)
        result = arguments.generate(MINECRAFT, cp, '../../', 'Player Name', profiles.REFERENCE_PROFILE)
        self.assertIn('"Player Name"', result)
        self.assertIn('Player Name', arguments.parse_argfile(result))

    def build_fixture(self, root, omit_spng=False, profile=profiles.REFERENCE_PROFILE, exclude_saves=False):
        metadata = json.loads((FIXTURES / 'version.json').read_text())
        metadata['id'] = '26.3'
        metadata['libraries'].append({'name': 'org.lwjgl:lwjgl:3.4.3', 'downloads': {'artifact': {'path': 'org/lwjgl/lwjgl/3.4.3/lwjgl-3.4.3.jar', 'url': 'https://fixture/core'}}})
        blobs = {
            'https://fixture/netty': zipped({'io/netty/channel/unix/Errors.class': b'class'}),
            'https://fixture/fastutil': zipped({'it/unimi/dsi/fastutil/floats/FloatList.class': b'class'}),
            'https://fixture/native': zipped({'lwjgl.dll': b'MZlwjgl'}),
            'https://fixture/core': zipped({'org/lwjgl/system/MemoryUtil.class': b'class'}),
        }
        supplements = copy.deepcopy(profiles.SPNG_LIBRARIES)
        for supplement, entries in zip(supplements, [{'org/lwjgl/util/spng/SPNG.class': b'class'}, {'lwjgl_spng.dll': b'MZspng'}]):
            spec = supplement['downloads']['artifact']
            data = zipped(entries)
            url = 'https://fixture/' + Path(spec['path']).name
            spec.update(url=url, size=len(data), sha1=hashlib.sha1(data).hexdigest())
            blobs[url] = data
        def request(req, **kwargs):
            url = req.full_url
            if url.endswith('version_manifest_v2.json'):
                data = json.dumps({'versions': [{'id': '26.3', 'url': 'https://fixture/version'}]}).encode()
            elif url == 'https://fixture/version':
                data = json.dumps(metadata).encode()
            elif url == 'https://fixture/index':
                data = b'{"objects":{}}'
            else:
                self.assertNotEqual(url, 'https://fixture/client', 'Local build must not download the client')
                data = blobs[url]
            return io.BytesIO(data)
        def runtime(metadata, downloader, java):
            for filename in ['bin/java.exe', 'bin/server/jvm.dll', 'release']:
                path = java / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            return {'release': 'fixture'}
        original_extract = dependencies.extract_natives
        def extract(jar, natives, excludes):
            if not omit_spng or 'spng' not in jar.name:
                original_extract(jar, natives, excludes)
        instance = root / 'source-instance'
        (instance / 'saves/world').mkdir(parents=True)
        (instance / 'saves/world/level.dat').write_bytes(b'world')
        (instance / 'custom-mod-data').mkdir()
        (instance / 'custom-mod-data/token').write_bytes(b'custom')
        output = root / 'new-package'
        events = []
        with patch('urllib.request.urlopen', side_effect=request), patch('minecraft_playden.runtime.install', side_effect=runtime), patch('minecraft_playden.profiles.SPNG_LIBRARIES', supplements), patch('minecraft_playden.dependencies.extract_natives', side_effect=extract):
            if omit_spng:
                with self.assertRaisesRegex(ValueError, 'lwjgl_spng.dll'):
                    build(output, Downloader(root / 'cache'), ClientSource('local', version='26.3', instance=instance, client_jar=FIXTURES / 'client.jar'), progress=events.append, launch_profile=profile, exclude_saves=exclude_saves)
                self.assertFalse(output.exists())
                self.assertNotIn('complete', [event.stage for event in events])
                return
            build(output, Downloader(root / 'cache'), ClientSource('local', version='26.3', instance=instance, client_jar=FIXTURES / 'client.jar'), progress=events.append, launch_profile=profile, exclude_saves=exclude_saves)
            self.assertEqual(validate(output), [])
            self.assertEqual(json.loads((output / 'recipe.json').read_text()), recipe.generate('Minecraft 26.3'))
            self.assertFalse((output / 'launch.args').exists())
            inventory = json.loads((output / 'package.json').read_text())
            self.assertIn('recipe.json', inventory['files'])
            self.assertNotIn('launch.args', inventory['files'])
            game = output / profiles.game_directory(profile)
            self.assertEqual((game / 'saves/world/level.dat').exists(), not exclude_saves)
            self.assertEqual((game / 'custom-mod-data/token').read_bytes(), b'custom')
            tokens = arguments.parse_argfile((output / 'playden.args').read_text())
            self.assertEqual((output / 'java/bin' / tokens[tokens.index('--gameDir') + 1]).resolve(), game.resolve())
            if checker := os.environ.get('PLAYDEN_RECIPE_CHECK'):
                result = subprocess.run([checker, str(output / 'recipe.json')], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            # Runtime user data stays mutable for both launch profiles.
            (game / 'custom-mod-data/token').write_bytes(b'changed')
            self.assertEqual(validate(output), [])
            self.assertEqual(resolve_local(output, None).client, output / profiles.client_path(metadata, profile))
            self.assertEqual((output / profiles.client_path(metadata, profile)).read_bytes(), (FIXTURES / 'client.jar').read_bytes())
            # Omitting one native DLL from an otherwise self-consistent inventory
            # must fail even while other native DLLs still exist.
            if profile != profiles.REFERENCE_PROFILE:
                return
            (output / 'natives/lwjgl_spng.dll').unlink()
            inventory = json.loads((output / 'package.json').read_text())
            del inventory['files']['natives/lwjgl_spng.dll']
            (output / 'package.json').write_text(json.dumps(inventory))
            self.assertTrue(any('Required native DLL missing' in error and 'lwjgl_spng.dll' in error for error in validate(output)))

    def test_reference_build_imports_local_client_and_validates_each_native(self):
        with tempfile.TemporaryDirectory() as temp:
            self.build_fixture(Path(temp))

    def test_both_launch_profiles_include_recipes_and_preserve_save_policy(self):
        for profile in profiles.PROFILES:
            for exclude_saves in (False, True):
                with self.subTest(profile=profile, exclude_saves=exclude_saves), tempfile.TemporaryDirectory() as temp:
                    self.build_fixture(Path(temp), profile=profile, exclude_saves=exclude_saves)

    def test_missing_spng_extraction_prevents_publication_and_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            self.build_fixture(Path(temp), omit_spng=True)
