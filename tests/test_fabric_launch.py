"""Modern Fabric package fixtures; no game, loader, scripts or network executed."""
from runtime_fixtures import install_runtime
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

from minecraft_playden import arguments, fabric
from minecraft_playden.desktop import LocalBuildRequest
from minecraft_playden.discovery import discover
from minecraft_playden.downloads import Downloader
from minecraft_playden.exporter import build
from minecraft_playden.instance_content import import_instance
from minecraft_playden.sources import ClientSource
from minecraft_playden.validator import validate

FIXTURES = Path(__file__).parent / 'fixtures'
MATRIX = [('1.20.1', '0.16.10'), ('26.3', '0.20.1')]


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as jar:
        for name, data in entries.items():
            jar.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), data)
    return stream.getvalue()


def save(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    return path


def installed(root, game='1.20.1', loader='0.16.10', layout='prism', full_components=False, minimal_manifest=False, installed_lwjgl_version=None):
    # 26.2 is a synthetic version-generalisation regression, not captured metadata.
    fixture_game = '26.3' if game == '26.2' else game
    profile = json.loads((FIXTURES / f'fabric/official-{fixture_game}-{loader}.json').read_text().replace(fixture_game, game))
    component = json.loads((FIXTURES / f'fabric/prism-{fixture_game}-{loader}.json').read_text())
    # Real upstream metadata, synthetic JAR payloads; local hashes fit payloads.
    metadata = json.loads((FIXTURES / 'version.json').read_text())
    metadata['id'] = game
    metadata['javaVersion']['majorVersion'] = 17 if game == '1.20.1' else 25
    client = (FIXTURES / 'client.jar').read_bytes()
    metadata['arguments']['game'] += ['--version', '${version_name}', '--userType', '${user_type}']
    if layout in ('prism', 'multimc'):
        instance = root / 'instances/Test'
        save(instance, 'mmc-pack.json', json.dumps({'components': [
            {'uid': 'net.minecraft', 'version': game}, {'uid': fabric.FABRIC, 'version': loader}]}))
        save(root, 'prismlauncher.cfg' if layout == 'prism' else 'multimc.cfg', '[General]\n')
        save(root, f'meta/{fabric.FABRIC}/{loader}.json', json.dumps(component))
        save(root, f'meta/{fabric.INTERMEDIARY}/{game}.json', json.dumps({
            'uid': fabric.INTERMEDIARY, 'version': game, 'order': 11,
            'libraries': [{'name': f'net.fabricmc:intermediary:{game}'}]}))
        # Prism applies installed component JVM additions alongside the base.
        save(root, f'meta/net.minecraft/{game}.json', json.dumps({
            'uid': 'net.minecraft', 'version': game, 'order': -1,
            'mainClass': metadata['mainClass'], '+jvmArgs': ['-XX:StackShadowPages=32']}))
        save(root, f'libraries/com/mojang/minecraft/{game}/minecraft-{game}-client.jar', client)
        game_root = instance / '.minecraft'
    else:
        name = profile['id']
        instance = root / 'versions' / name
        save(instance, f'{name}.json', json.dumps(profile))
        save(root, f'versions/{game}/{game}.json', json.dumps(metadata))
        save(root, f'versions/{game}/{game}.jar', client)
        game_root = root
    save(game_root, 'saves/World/level.dat', b'world')
    save(game_root, 'mods/custom.jar', archive({'fabric.mod.json': b'{"id":"custom"}'}))
    save(game_root, 'custom-config/account/token', b'custom-data')
    if full_components:
        suggested_revision = '3.3.3' if game == '1.20.1' else '3.4.3'
        revision = installed_lwjgl_version or suggested_revision
        manifest = json.loads((instance / 'mmc-pack.json').read_text())
        if not minimal_manifest:
            manifest['components'].append({'uid': 'org.lwjgl3', 'version': revision})
        save(instance, 'mmc-pack.json', json.dumps(manifest))
        logging = b'<Configuration/>'
        component_libraries = []
        for classifier, rules in [('', None), ('natives-windows', [{'action': 'allow', 'os': {'name': 'windows'}}]),
                                  ('natives-linux', [{'action': 'allow', 'os': {'name': 'linux'}}]),
                                  ('natives-windows-arm64', [{'action': 'allow', 'os': {'name': 'windows-arm64'}}])]:
            suffix = '-' + classifier if classifier else ''
            payload = archive({'lwjgl.dll': b'MZwindows'}) if classifier else archive({'org/lwjgl/system/MemoryUtil.class': b'class'})
            lib = {'name': f'org.lwjgl:lwjgl{suffix}:{revision}', 'downloads': {'artifact': {
                'url': f'https://libraries.minecraft.net/org/lwjgl/lwjgl/{revision}/lwjgl-{revision}{suffix}.jar',
                'size': len(payload), 'sha1': hashlib.sha1(payload).hexdigest()}}}
            if rules:
                lib['rules'] = rules
            component_libraries.append(lib)
        save(root, f'meta/org.lwjgl3/{revision}.json', json.dumps({'uid': 'org.lwjgl3', 'version': revision,
             'order': -1, 'libraries': component_libraries}))
        jtracy = archive({'jtracy.dll': b'MZjtracy'})
        save(root, f'meta/net.minecraft/{game}.json', json.dumps({
            'uid': 'net.minecraft', 'version': game, 'order': 0, 'mainClass': metadata['mainClass'],
            'libraries': [{'name': 'com.mojang:jtracy-natives-windows:1.14.38',
                'rules': [{'action': 'allow', 'os': {'name': 'windows'}}],
                'downloads': {'artifact': {'url': 'https://libraries.minecraft.net/com/mojang/jtracy/1.14.38/jtracy-1.14.38-natives-windows.jar',
                    'sha1': hashlib.sha1(jtracy).hexdigest(), 'size': len(jtracy)}}}] if game == '26.3' else [],
            'requires': [{'uid': 'org.lwjgl3', 'suggests': suggested_revision}],
            'mainJar': {'name': f'com.mojang:minecraft:{game}:client', 'downloads': {'artifact': metadata['downloads']['client']}},
            'assetIndex': metadata['assetIndex'],
            'minecraftArguments': '--username ${auth_player_name} --version ${version_name} --gameDir ${game_directory} --assetsDir ${assets_root} --assetIndex ${assets_index_name} --uuid ${auth_uuid} --accessToken ${auth_access_token} --userType ${user_type}',
            '+jvmArgs': ['-XX:StackShadowPages=32'],
            'logging': {'argument': '-Dlog4j.configurationFile=${path}', 'file': {
                'id': 'client.xml', 'url': 'https://piston-data.mojang.com/fixture/client.xml',
                'sha1': hashlib.sha1(logging).hexdigest(), 'size': len(logging)}}}))
    libs = list(profile['libraries'])
    if layout in ('prism', 'multimc') and not any(lib['name'].startswith('net.fabricmc:intermediary:') for lib in libs):
        libs.append({'name': f'net.fabricmc:intermediary:{game}'})
    for lib in libs:
        name = lib['name']
        _, path = fabric.coordinate(name)
        entries = {'fixture': b'library'}
        if name.startswith('net.fabricmc:fabric-loader:'):
            entries = {profile['mainClass'].replace('.', '/') + '.class': b'Knot',
                       'net/fabricmc/loader/api/FabricLoader.class': b'Loader'}
        elif name.startswith('net.fabricmc:intermediary:'):
            entries = {'mappings/mappings.tiny': b'v1\tofficial\tintermediary\n'}
        data = archive(entries)
        save(root, 'libraries/' + path, data)
        # Fixture official checksums describe fixture data, not redistributable binaries.
        if layout == 'official':
            lib.update(sha1=hashlib.sha1(data).hexdigest(), size=len(data))
    if layout == 'official':
        save(instance, f'{instance.name}.json', json.dumps(profile))
    return instance, metadata, profile


class FabricLaunchTests(unittest.TestCase):
    def build_fixture(self, root, game, loader, layout, exclude_saves=False, desktop=False, fallback=False, full_components=False, mutation=None, official_root=False, cli=False, minimal_manifest=False, installed_lwjgl_version=None, uncached_lwjgl=False):
        launcher = root / 'source'
        instance, base, profile = installed(launcher, game, loader, layout, full_components, minimal_manifest, installed_lwjgl_version)
        if uncached_lwjgl:
            (launcher / 'meta/org.lwjgl3/3.4.3.json').unlink()
        if fallback:
            for path in (launcher / 'meta').rglob('*.json'):
                path.unlink()
            # Hashes for our exact-version metadata fallback must match fixture JARs.
            for lib in profile['libraries']:
                for field in ('sha1', 'size'):
                    lib.pop(field, None)
        blobs = {
            'https://fixture/netty': archive({'io/netty/channel/unix/Errors.class': b'class'}),
            'https://fixture/fastutil': archive({'it/unimi/dsi/fastutil/floats/FloatList.class': b'class'}),
            'https://fixture/native': archive({'lwjgl.dll': b'MZnative'}),
        }
        requested = []
        def request(req, **kwargs):
            url = req.full_url
            requested.append(url)
            if url.endswith('version_manifest_v2.json'):
                value = json.dumps({'versions': [{'id': game, 'url': 'https://fixture/base'}]}).encode()
            elif url == 'https://fixture/base':
                value = json.dumps(base).encode()
            elif url == 'https://fixture/index':
                value = b'{"objects":{}}'
            elif url.startswith('https://libraries.minecraft.net/org/lwjgl/'):
                value = archive({'lwjgl.dll': b'MZwindows'}) if 'natives-windows.jar' in url else archive({'org/lwjgl/system/MemoryUtil.class': b'class'})
            elif url == 'https://libraries.minecraft.net/com/mojang/jtracy/1.14.38/jtracy-1.14.38-natives-windows.jar':
                value = archive({'jtracy.dll': b'MZjtracy'})
            elif url == 'https://piston-data.mojang.com/fixture/client.xml':
                value = b'<Configuration/>'
            elif url.startswith('https://meta.fabricmc.net/'):
                self.assertEqual(url, f'https://meta.fabricmc.net/v2/versions/loader/{game}/{loader}/profile/json')
                value = json.dumps(profile).encode()
            else:
                self.assertNotIn('client', url, 'Never download a local client')
                value = blobs[url]
            return io.BytesIO(value)
        def runtime(metadata, downloader, java):
            self.assertEqual(metadata['javaVersion']['majorVersion'], 17 if game == '1.20.1' else 25)
            return install_runtime(metadata, downloader, java)
        selected = launcher if official_root else instance
        selected_version = profile['id'] if official_root else None
        output = root / 'Package With Spaces'
        before = {str(p.relative_to(launcher)): p.read_bytes() for p in launcher.rglob('*') if p.is_file()}
        def progress(event):
            if mutation is not None and event.stage == 'validation':
                mutation(instance)
        with patch('urllib.request.urlopen', side_effect=request), patch('minecraft_playden.runtime.install', side_effect=runtime):
            if desktop:
                rows = discover([launcher], include_defaults=False).installations
                row = next(row for row in rows if row.loaders)
                self.assertTrue(row.buildable, row.build_problem)
                self.assertEqual(row.version, game)
                self.assertEqual(row.loader_label, f'Fabric {loader}')
                LocalBuildRequest(row, output, root / 'cache').run()
            elif cli:
                from minecraft_playden.cli import main
                argv = ['minecraft-playden', '--cache', str(root / 'cache'), 'build', '--source', 'local',
                        '--instance', str(selected), '--output', str(output)]
                if selected_version:
                    argv += ['--version', selected_version]
                if exclude_saves:
                    argv += ['--exclude-saves']
                with patch('sys.argv', argv):
                    self.assertEqual(main(), 0)
            else:
                build(output, Downloader(root / 'cache'), ClientSource('local', instance=selected, version=selected_version), exclude_saves=exclude_saves, progress=progress)
        self.assertEqual({str(p.relative_to(launcher)): p.read_bytes() for p in launcher.rglob('*') if p.is_file()}, before)
        self.assertEqual(validate(output), [])
        data = json.loads((output / 'metadata/fabric.json').read_text())
        self.assertEqual(data['loader'], loader)
        cp = json.loads((output / 'package.json').read_text())['classpath']
        self.assertEqual(len(cp), len(set(cp)))
        self.assertTrue(cp[-1].endswith('versions/client.jar'))
        self.assertTrue(any(f'fabric-loader-{loader}.jar' in path for path in cp))
        self.assertEqual(any('intermediary' in path for path in cp), game.startswith('1.') or (layout != 'official' and not fallback))
        self.assertFalse(any(any(tag in path for tag in ('natives-linux', 'natives-macos', 'arm64')) for path in cp))
        words = arguments.parse_argfile((output / 'playden.args').read_text())
        self.assertIn('net.fabricmc.loader.impl.launch.knot.KnotClient', words)
        self.assertNotIn('net.minecraft.client.main.Main', words)
        for flag, relative in [('--gameDir', 'instances/default/minecraft'), ('--assetsDir', 'assets')]:
            self.assertEqual((output / 'java/bin' / words[words.index(flag) + 1]).resolve(), (output / relative).resolve())
        game_dir = output / 'instances/default/minecraft'
        self.assertEqual((game_dir / 'saves/World/level.dat').exists(), not exclude_saves)
        self.assertEqual((game_dir / 'custom-config/account/token').read_bytes(), b'custom-data')
        (game_dir / 'options.txt').write_text('changed by game')
        self.assertEqual(validate(output), [])
        if full_components:
            if game == '26.3':
                self.assertEqual((output / 'natives/jtracy.dll').read_bytes(), b'MZjtracy')
            self.assertEqual((output / 'natives/lwjgl.dll').read_bytes(), b'MZnative' if uncached_lwjgl else b'MZwindows')
            self.assertEqual((output / 'assets/log_configs/client.xml').read_bytes(), b'<Configuration/>')
            self.assertIn('-Dlog4j.configurationFile=../../assets/log_configs/client.xml', words)
            self.assertEqual(any('libraries.minecraft.net/org/lwjgl/' in url for url in requested), not uncached_lwjgl)
        self.assertFalse((output / 'launch.args').exists())
        self.assertTrue((output / 'recipe.json').is_file())
        if checker := os.environ.get('PLAYDEN_RECIPE_CHECK'):
            result = subprocess.run([checker, str(output / 'recipe.json')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(any('meta.fabricmc.net/' in url for url in requested), fallback)
        return output, instance, data, base

    def test_installed_profiles_build_and_validate_across_layouts_versions_and_save_policy(self):
        for game, loader in MATRIX:
            for layout in ('prism', 'multimc', 'official'):
                for exclude in (False, True):
                    with self.subTest(game=game, loader=loader, layout=layout, exclude_saves=exclude), tempfile.TemporaryDirectory() as temp:
                        self.build_fixture(Path(temp), game, loader, layout, exclude)

    def test_desktop_builds_each_installed_layout(self):
        for game, loader in MATRIX:
            for layout in ('prism', 'multimc', 'official'):
                with self.subTest(layout=layout, game=game, loader=loader), tempfile.TemporaryDirectory() as temp:
                    self.build_fixture(Path(temp), game, loader, layout, desktop=True)

    def test_cli_builds_official_roots_and_selected_profile_ids(self):
        for game, loader in MATRIX:
            with self.subTest(game=game, loader=loader), tempfile.TemporaryDirectory() as temp:
                self.build_fixture(Path(temp), game, loader, 'official', official_root=True, cli=True)

    def test_prism_minimal_manifest_suggestions_and_implicit_intermediary(self):
        for desktop, exclude_saves in [(False, False), (False, True), (True, False)]:
            with self.subTest(desktop=desktop, exclude_saves=exclude_saves), tempfile.TemporaryDirectory() as temp:
                output, instance, data, _ = self.build_fixture(
                    Path(temp), '26.3', '0.20.1', 'prism', full_components=True,
                    minimal_manifest=True, exclude_saves=exclude_saves, cli=not desktop, desktop=desktop)
                manifest = json.loads((instance / 'mmc-pack.json').read_text())
                self.assertEqual([item['uid'] for item in manifest['components']], ['net.minecraft', fabric.FABRIC])
                by_uid = {doc['uid']: doc for doc in data['profiles']}
                self.assertEqual(by_uid['net.minecraft']['requires'], [{'uid': 'org.lwjgl3', 'suggests': '3.4.3'}])
                self.assertEqual(by_uid[fabric.FABRIC]['requires'], [{'uid': fabric.INTERMEDIARY}])
                self.assertEqual(by_uid['org.lwjgl3']['version'], '3.4.3')
                self.assertEqual(by_uid[fabric.INTERMEDIARY]['version'], '26.3')
                _, mappings = fabric.coordinate('net.fabricmc:intermediary:26.3')
                self.assertTrue((output / 'libraries' / mappings).is_file())

    def test_uncached_lwjgl_suggestion_is_optional_and_keeps_base_windows_resolution(self):
        with tempfile.TemporaryDirectory() as temp:
            _, _, data, _ = self.build_fixture(
                Path(temp), '26.3', '0.20.1', 'prism', full_components=True,
                minimal_manifest=True, uncached_lwjgl=True)
            self.assertNotIn('org.lwjgl3', [doc.get('uid') for doc in data['profiles']])

    def test_selected_lwjgl_version_wins_over_minecraft_suggestion(self):
        with tempfile.TemporaryDirectory() as temp:
            output, _, data, _ = self.build_fixture(
                Path(temp), '26.3', '0.20.1', 'prism', full_components=True,
                installed_lwjgl_version='3.3.3')
            by_uid = {doc['uid']: doc for doc in data['profiles']}
            self.assertEqual(by_uid['net.minecraft']['requires'][0]['suggests'], '3.4.3')
            self.assertEqual(by_uid['org.lwjgl3']['version'], '3.3.3')
            cp = json.loads((output / 'package.json').read_text())['classpath']
            lwjgl = [path for path in cp if path.startswith('libraries/org/lwjgl/')]
            self.assertTrue(lwjgl)
            self.assertTrue(all('/3.3.3/' in path for path in lwjgl))

    def test_mandatory_component_failures_identify_the_component_and_versions(self):
        for requirement, versions in [
            ({'uid': 'org.lwjgl3'}, {'net.minecraft': '26.3'}),
            ({'uid': 'org.lwjgl3', 'equals': '3.4.3'}, {'net.minecraft': '26.3'}),
            ({'uid': 'org.lwjgl3', 'equals': '3.4.3'}, {'org.lwjgl3': '3.3.3'}),
            ({'uid': 'org.lwjgl3', 'equals': '3.4.3', 'suggests': '3.3.3'}, {'org.lwjgl3': '3.3.3'}),
        ]:
            with self.subTest(requirement=requirement, versions=versions):
                with self.assertRaisesRegex(ValueError, 'org.lwjgl3') as error:
                    fabric.check_component_requirements({'uid': 'net.minecraft', 'requires': [requirement]}, versions)
                self.assertIn('net.minecraft', str(error.exception))
                if 'org.lwjgl3' in versions:
                    self.assertIn('3.3.3', str(error.exception))
                    self.assertIn('3.4.3', str(error.exception))
        selected = {'org.lwjgl3': '3.3.3'}
        fabric.check_component_requirements({'uid': 'net.minecraft', 'requires': [
            {'uid': 'org.lwjgl3', 'suggests': '3.4.3'}]}, selected)
        self.assertEqual(selected, {'org.lwjgl3': '3.3.3'})

    def test_explicit_and_cached_intermediary_versions_must_match_minecraft(self):
        for location in ('manifest', 'cache', 'requirement'):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                instance, _, _ = installed(root, '26.3', '0.20.1')
                if location == 'manifest':
                    path = instance / 'mmc-pack.json'
                    data = json.loads(path.read_text())
                    data['components'].append({'uid': fabric.INTERMEDIARY, 'version': '26.2'})
                elif location == 'cache':
                    path = root / f'meta/{fabric.INTERMEDIARY}/26.3.json'
                    data = json.loads(path.read_text())
                    data['version'] = '26.2'
                else:
                    path = root / f'meta/{fabric.FABRIC}/0.20.1.json'
                    data = json.loads(path.read_text())
                    data['requires'] = [{'uid': fabric.INTERMEDIARY, 'equals': '26.2'}]
                path.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, fabric.INTERMEDIARY):
                    fabric.inspect(instance)

    def test_implicit_intermediary_fallback_is_exact_and_requires_the_mapping_jar(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            instance, base, _ = installed(root, '26.3', '0.20.1')
            (root / f'meta/{fabric.INTERMEDIARY}/26.3.json').unlink()
            resolved = fabric.inspect(instance)
            document = next(doc for doc in resolved.profiles if doc.get('uid') == fabric.INTERMEDIARY)
            self.assertEqual(document['version'], '26.3')
            self.assertEqual(document['libraries'][0]['name'], 'net.fabricmc:intermediary:26.3')
            plan = resolved.plan(base, Downloader(root / 'cache'))
            self.assertIn('net.fabricmc:intermediary:26.3', [lib['name'] for lib in fabric.derive(base, plan)['libraries']])
            _, path = fabric.coordinate('net.fabricmc:intermediary:26.3')
            (root / 'libraries' / path).unlink()
            with self.assertRaisesRegex(ValueError, 'intermediary'):
                resolved.plan(base, Downloader(root / 'cache'))

    def test_selected_lwjgl_cache_and_component_versions_cannot_be_dropped_or_changed(self):
        for failure in ('missing-cache', 'wrong-cache-version', 'duplicate-pin', 'mandatory-missing'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                instance, _, _ = installed(root, '26.3', '0.20.1', full_components=True)
                if failure == 'missing-cache':
                    (root / 'meta/org.lwjgl3/3.4.3.json').unlink()
                elif failure == 'wrong-cache-version':
                    path = root / 'meta/org.lwjgl3/3.4.3.json'
                    data = json.loads(path.read_text())
                    data['version'] = '3.3.3'
                    path.write_text(json.dumps(data))
                elif failure == 'duplicate-pin':
                    path = instance / 'mmc-pack.json'
                    data = json.loads(path.read_text())
                    data['components'].append({'uid': 'org.lwjgl3', 'version': '3.3.3'})
                    path.write_text(json.dumps(data))
                else:
                    path = instance / 'mmc-pack.json'
                    data = json.loads(path.read_text())
                    data['components'] = [item for item in data['components'] if item['uid'] != 'org.lwjgl3']
                    path.write_text(json.dumps(data))
                    path = root / 'meta/net.minecraft/26.3.json'
                    data = json.loads(path.read_text())
                    data['requires'] = [{'uid': 'org.lwjgl3'}]
                    path.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, 'org.lwjgl3'):
                    fabric.inspect(instance)

    def test_saved_component_requirements_are_revalidated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            instance, base, _ = installed(root, '26.3', '0.20.1', full_components=True)
            resolved = fabric.inspect(instance)
            plan = {'schema': 1, 'minecraft': resolved.minecraft, 'loader': resolved.loader,
                    'profiles': copy.deepcopy(resolved.profiles)}
            for doc in plan['profiles']:
                if doc.get('uid') == 'net.minecraft':
                    doc['requires'] = [{'uid': 'org.lwjgl3', 'equals': '3.3.3'}]
            with self.assertRaisesRegex(ValueError, 'org.lwjgl3'):
                fabric.derive(base, plan)

    def test_missing_component_cache_uses_exact_version_official_metadata(self):
        for game, loader in MATRIX:
            with self.subTest(game=game, loader=loader), tempfile.TemporaryDirectory() as temp:
                self.build_fixture(Path(temp), game, loader, 'prism', fallback=True)

    def test_prism_component_shape_logging_and_windows_natives_from_non_windows_install(self):
        for game, loader in MATRIX:
            for layout in ('prism', 'multimc'):
                with self.subTest(game=game, loader=loader, layout=layout), tempfile.TemporaryDirectory() as temp:
                    self.build_fixture(Path(temp), game, loader, layout, full_components=True)

    def test_metadata_change_during_build_prevents_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def mutate(instance):
                manifest = json.loads((instance / 'mmc-pack.json').read_text())
                manifest['components'][1]['version'] = '0.99'
                (instance / 'mmc-pack.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                self.build_fixture(root, '1.20.1', '0.16.10', 'prism', mutation=mutate)
            self.assertFalse((root / 'Package With Spaces').exists())

    def test_missing_library_fails_before_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            instance, base, _ = installed(root)
            _, path = fabric.coordinate('org.ow2.asm:asm:9.7.1')
            (root / 'libraries' / path).unlink()
            source = fabric.inspect(instance)
            with self.assertRaisesRegex(ValueError, 'library missing'):
                source.plan(base, Downloader(root / 'cache'))
            self.assertFalse((root / 'package').exists())

    def test_dependency_order_deduplication_override_and_conflicts(self):
        a = {'name': 'test:lib:1'}
        b = {'name': 'test:lib:2'}
        c = {'name': 'test:other:1'}
        result = fabric.merge_libraries([[a, c], [b, b]])
        self.assertEqual([v['name'] for v in result], ['test:lib:2', 'test:other:1'])
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            fabric.merge_libraries([[], [a, b]])

    def test_invalid_metadata_and_nonportable_paths_fail(self):
        for main, jvm in [('custom.Main', []), (next(iter(fabric.MAINS)), ['-Droot=/home/user']),
                          (next(iter(fabric.MAINS)), ['-javaagent:agent.jar']),
                          (next(iter(fabric.MAINS)), ['-cp', '${classpath}'])]:
            with self.subTest(main=main, jvm=jvm), tempfile.TemporaryDirectory() as temp:
                instance, base, _ = installed(Path(temp))
                resolved = fabric.inspect(instance)
                docs = copy.deepcopy(resolved.profiles)
                docs[-1]['mainClass'] = main
                docs[-1]['arguments'] = {'jvm': jvm}
                with self.assertRaises(ValueError):
                    fabric.derive(base, {'schema': 1, 'minecraft': base['id'], 'loader': resolved.loader, 'profiles': docs})
        for coordinate in ['../../escape:x:1', 'test:lib:../escape', 'test:lib:..']:
            with self.assertRaises(ValueError):
                fabric.coordinate(coordinate)

    def test_exported_plan_and_artifacts_are_semantically_validated(self):
        with tempfile.TemporaryDirectory() as temp:
            output, instance, plan, base = self.build_fixture(Path(temp), '1.20.1', '0.16.10', 'official')
            for field, value in [('loader', '0.99'), ('minecraft', 'other')]:
                altered = {**plan, field: value}
                self.assertTrue(fabric.validate(output, base, altered))
            artifact = output / 'libraries' / plan['artifacts'][0]['path']
            artifact.write_bytes(b'corrupt')
            self.assertTrue(validate(output))
            self.assertTrue(fabric.validate(output, base, plan))

    def test_missing_knot_class_and_wrong_loader_version_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output, _, plan, base = self.build_fixture(root, '1.20.1', '0.16.10', 'official')
            _, path = fabric.coordinate('net.fabricmc:fabric-loader:0.16.10')
            (output / 'libraries' / path).write_bytes(archive({'wrong/Main.class': b'bad'}))
            self.assertTrue(any('Fabric launch plan' in error for error in fabric.validate(output, base, plan)))

    def test_inherited_official_arguments_and_profile_id_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parent, base, _ = installed(root, layout='official')
            child = root / 'versions/custom-fabric'
            save(child, 'custom-fabric.json', json.dumps({'id': 'custom-fabric', 'inheritsFrom': parent.name,
                'arguments': {'jvm': ['-Dfabric.test=${game_directory}/custom-data',
                    {'rules': [{'action': 'allow', 'os': {'name': 'linux'}}], 'value': '-Dlinux=/home/user'}],
                    'game': ['--custom', 'enabled']}}))
            installed_fabric = fabric.inspect(child)
            plan = installed_fabric.plan(base, Downloader(root / 'cache'))
            effective = fabric.derive(base, plan)
            text = arguments.generate(effective, ['versions/client.jar'], '../../')
            self.assertIn('custom-fabric', text)
            self.assertIn('../../instances/default/minecraft/custom-data', text)
            self.assertNotIn('/home/user', text)
            with self.assertRaisesRegex(ValueError, 'does not match'):
                fabric.inspect(child, 'other-version')

    def test_modified_installed_checksum_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            instance, base, _ = installed(root, layout='official')
            _, relative = fabric.coordinate('org.ow2.asm:asm:9.7.1')
            save(root, 'libraries/' + relative, archive({'changed': b'bad'}))
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                fabric.inspect(instance).plan(base, Downloader(root / 'cache'))

    def test_rehashed_manifest_cannot_hide_fabric_main_class_or_missing_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            output, _, data, base = self.build_fixture(Path(temp), '1.20.1', '0.16.10', 'official')
            inventory = json.loads((output / 'package.json').read_text())
            _, path = fabric.coordinate('net.fabricmc:fabric-loader:0.16.10')
            jar = output / 'libraries' / path
            jar.write_bytes(archive({'wrong.class': b'wrong'}))
            inventory['files']['libraries/' + path] = hashlib.sha256(jar.read_bytes()).hexdigest()
            (output / 'package.json').write_text(json.dumps(inventory))
            self.assertTrue(any('Fabric' in error for error in validate(output)))
            del inventory['files']['metadata/fabric.json']
            (output / 'package.json').write_text(json.dumps(inventory))
            self.assertIn('Required file absent from inventory: metadata/fabric.json', validate(output))

    def test_profile_credentials_cannot_replace_offline_placeholder_identity(self):
        for game in [['--accessToken', 'PRIVATE-TOKEN'], [
                {'rules': [{'action': 'allow', 'os': {'name': 'windows'}}], 'value': ['--accessToken', 'PRIVATE-TOKEN']}]]:
            with self.subTest(game=game), self.assertRaisesRegex(ValueError, 'credential'):
                fabric.argument_lists({'arguments': {'game': game}})

    def test_malformed_artifact_shapes_are_clear_errors(self):
        for value in [None, {'name': 'test:lib:1', 'downloads': []},
                      {'name': 'test:lib:1', 'downloads': {'artifact': []}},
                      {'name': 'test:lib:1', 'sha1': None}, {'name': 'test:lib:1', 'url': None},
                      {'name': 'test:lib:1', 'downloads': {'artifact': {'url': None}}}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                fabric.library(value)

    def test_official_versions_directory_case_alias_cannot_strip_fabric(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            instance, _, _ = installed(root, layout='official')
            name = instance.name
            if os.name != 'nt':
                (root / 'versions').rename(root / 'Versions')
                try:
                    (root / 'versions').symlink_to(root / 'Versions', target_is_directory=True)
                except OSError:
                    self.skipTest('Case alias needs symlink support on this host')
            source = ClientSource('local', instance=root / 'Versions' / name)
            self.assertEqual(source.prepare(), '1.20.1')
            self.assertIsNotNone(source.fabric)
            self.assertEqual(source.fabric.loader, '0.16.10')

    def test_forge_and_neoforge_remain_file_only(self):
        for uid in ['net.minecraftforge', 'net.neoforged.neoforge']:
            with self.subTest(uid=uid), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                instance, _, _ = installed(root)
                manifest = json.loads((instance / 'mmc-pack.json').read_text())
                manifest['components'][1]['uid'] = uid
                (instance / 'mmc-pack.json').write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'Broken' if uid == 'net.minecraftforge' else 'file-only'):
                    ClientSource('local', instance=instance).prepare()
                import_instance(instance, root / 'data-only')
                self.assertFalse((root / 'data-only/recipe.json').exists())
                self.assertEqual(validate(root / 'data-only'), [])
