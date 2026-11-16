"""Local client policy and adapters; first-release CLI boundary regressions."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from minecraft_playden.cli import main
from minecraft_playden.downloads import Downloader
from minecraft_playden.exporter import build
from minecraft_playden.importers import resolve_local
from minecraft_playden.sources import ClientSource

FIXTURE_DIR = Path(__file__).parent / 'fixtures'
METADATA = json.loads((FIXTURE_DIR / 'version.json').read_text())
class SourceTests(unittest.TestCase):
    def test_cli_requires_explicit_source(self):
        with patch('sys.argv', ['minecraft_playden', 'build', '--version', '26.3', '--output', 'unused']), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as failure:
            main()
        self.assertEqual(failure.exception.code, 2)

    def test_missing_local_client_never_accesses_network_or_publishes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch('urllib.request.urlopen') as network:
                with self.assertRaisesRegex(ValueError, 'missing'):
                    build(root / 'output', Downloader(root / 'cache'), ClientSource('local', version='fixture', client_jar=root / 'missing.jar'))
                network.assert_not_called()
            self.assertFalse((root / 'output').exists())

    def test_local_cannot_acquire_without_prepare_or_download_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            downloader = Mock()
            source = ClientSource('local', version='fixture', client_jar=root / 'absent.jar')
            with self.assertRaises(ValueError):
                source.acquire(METADATA, downloader, root / 'client.jar')
            downloader.fetch.assert_not_called()

    def test_local_mismatch_and_disappearing_client_fail_without_download(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jar = root / 'client.jar'
            jar.write_bytes(b'wrong version')
            source = ClientSource('local', version='fixture', client_jar=jar)
            source.prepare()
            with self.assertRaisesRegex(ValueError, 'does not match'):
                source.check(METADATA)
            jar.unlink()
            downloader = Mock()
            with self.assertRaisesRegex(ValueError, 'missing'):
                source.acquire(METADATA, downloader, root / 'copied.jar')
            downloader.fetch.assert_not_called()

    def test_official_and_portable_adapters(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / 'versions/fixture'
            directory.mkdir(parents=True)
            (directory / 'fixture.json').write_text(json.dumps(METADATA))
            jar = directory / 'fixture.jar'
            jar.write_bytes((FIXTURE_DIR / 'client.jar').read_bytes())
            self.assertEqual(resolve_local(root, None).client, jar)
            self.assertEqual(resolve_local(directory, None).version, 'fixture')
            with self.assertRaisesRegex(ValueError, 'does not match'):
                resolve_local(directory, 'different')
            (root / 'metadata').mkdir()
            (root / 'metadata/version.json').write_text(json.dumps(METADATA))
            with self.assertRaisesRegex(ValueError, 'missing'):
                resolve_local(root, None)
            (root / 'versions/client.jar').write_bytes(jar.read_bytes())
            self.assertEqual(resolve_local(root, None).adapter, 'portable')

    def test_prism_multimc_adapters_without_launcher(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            instance = root / 'instances/Test'
            instance.mkdir(parents=True)
            pack = {'components': [{'uid': 'net.minecraft', 'version': 'fixture'}, {'uid': 'org.lwjgl3', 'version': '3'}]}
            (instance / 'mmc-pack.json').write_text(json.dumps(pack))
            with self.assertRaisesRegex(ValueError, 'missing'):
                resolve_local(instance, None)
            jar = root / 'libraries/com/mojang/minecraft/fixture/minecraft-fixture-client.jar'
            jar.parent.mkdir(parents=True)
            jar.write_bytes((FIXTURE_DIR / 'client.jar').read_bytes())
            for adapter in ['auto', 'prism', 'multimc']:
                self.assertEqual(resolve_local(instance, None, adapter=adapter).client, jar)
            pack['components'].append({'uid': 'net.fabricmc.fabric-loader', 'version': '1'})
            (instance / 'mmc-pack.json').write_text(json.dumps(pack))
            with self.assertRaisesRegex(ValueError, 'Modded'):
                resolve_local(instance, None)

    def test_removed_online_cli_options_are_rejected_without_network(self):
        for removed in (['--source', 'microsoft'], ['--client-id', 'example'], ['--auth-method', 'browser']):
            with self.subTest(removed=removed), patch('urllib.request.urlopen') as network:
                argv = ['packager', 'build', '--source', 'local', '--output', 'unused', *removed]
                with patch('sys.argv', argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    main()
                self.assertEqual(error.exception.code, 2)
                network.assert_not_called()

    def test_online_source_rejected_even_outside_cli(self):
        with patch('urllib.request.urlopen') as network:
            with self.assertRaisesRegex(ValueError, 'Only local'):
                ClientSource('microsoft')
            network.assert_not_called()

    def test_authentication_modules_are_not_distributed(self):
        from importlib.util import find_spec
        for module in ('auth', 'browser_auth', 'config'):
            self.assertIsNone(find_spec('minecraft_playden.' + module))
