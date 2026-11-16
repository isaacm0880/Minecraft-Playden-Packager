"""Release provenance and exclusion gates, without building Minecraft packages."""
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from release.audit import audit_tree
from release.provenance import BASELINE, REQUIRED, check_source, application_hash
from release.verify_pair import verify


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, markers in REQUIRED.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('\n'.join(markers))
        (self.root / 'release/source-policy.json').write_text(json.dumps({
            'schema': 1, 'reviewed_gui_snapshot': 'a' * 40,
            'application_tree_sha256': application_hash(self.root)}))

    def git(self, root, *args):
        return {'rev-parse': BASELINE, 'status': ' M release/desktop.spec',
                'ls-files': '\0'.join(REQUIRED), 'show': '1700000000', 'branch': 'release/v0.1.0-prep'}[args[0]]

    def test_dirty_source_is_not_labelled_as_an_exact_commit_release(self):
        with patch('release.provenance.git', side_effect=self.git), patch('release.provenance.subprocess.run'):
            with self.assertRaisesRegex(ValueError, 'clean committed'):
                check_source(self.root)
            info = check_source(self.root, BASELINE, preview=True)
            self.assertTrue(info['source_dirty'])
            self.assertTrue(info['preview'])
            self.assertEqual(info['source_commit'], BASELINE)
            first_digest = info['source_tree_sha256']
            with (self.root / 'release/desktop.spec').open('a') as stream:
                stream.write('\nchanged')
            self.assertNotEqual(check_source(self.root, preview=True)['source_tree_sha256'], first_digest)

    def test_wrong_commit_and_incomplete_cli_source_are_refused(self):
        with patch('release.provenance.git', side_effect=self.git), patch('release.provenance.subprocess.run'):
            with self.assertRaisesRegex(ValueError, 'exact source'):
                check_source(self.root, '0' * 40, preview=True)
            (self.root / 'minecraft_playden/gui_app.py').write_text('older CLI')
            with self.assertRaisesRegex(ValueError, 'Complete desktop'):
                check_source(self.root, preview=True)

    def test_application_change_requires_review_even_when_markers_remain(self):
        with (self.root / 'minecraft_playden/gui_app.py').open('a') as stream:
            stream.write('\n# modified application')
        with patch('release.provenance.git', side_effect=self.git), patch('release.provenance.subprocess.run'):
            with self.assertRaisesRegex(ValueError, 'reviewed GUI snapshot'):
                check_source(self.root, preview=True)

    def test_interpreter_cache_does_not_change_reviewed_hash(self):
        before = application_hash(self.root)
        cache = self.root / 'minecraft_playden/__pycache__'
        cache.mkdir()
        (cache / 'gui.pyc').write_bytes(b'generated cache')
        self.assertEqual(application_hash(self.root), before)


    def test_reviewed_hash_uses_portable_case_sensitive_filename_order(self):
        # Windows Path compares case-insensitively, unlike Linux Path. Mixed-case
        # resource names must still hash in the same order on all native runners.
        directory = self.root / 'minecraft_playden/resources'
        directory.mkdir()
        (directory / 'README.md').write_text('notice')
        (directory / 'icons.png').write_bytes(b'synthetic icon')
        expected = application_hash(self.root)
        with patch.object(Path, '__lt__', lambda left, right: str(left).casefold() < str(right).casefold()):
            self.assertEqual(application_hash(self.root), expected)


class ArtifactTests(unittest.TestCase):
    def test_private_key_material_is_rejected_without_flagging_tls_delimiter_constants(self):
        from release.audit import SECRET_PATTERNS
        import re
        marker = b'-----BEGIN PRIVATE KEY-----'
        self.assertFalse(any(re.search(pattern, marker) for pattern in SECRET_PATTERNS))
        # Synthetic text, not an actual cryptographic key.
        material = marker + b'\n' + b'A' * 100 + b'\n-----END PRIVATE KEY-----'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'data.txt').write_bytes(material)
            with self.assertRaisesRegex(ValueError, 'Potential credential'):
                audit_tree(root, root / 'unused', {})

    def test_game_and_private_files_are_refused_before_binary_inspection(self):
        for name in ('versions/client.jar', 'assets/object', 'saves/world.dat', '.venv/python.exe', 'launcher_accounts.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
                with self.assertRaisesRegex(ValueError, 'Forbidden'):
                    audit_tree(root, root / 'unused', {})

    def test_release_pair_refuses_preview_and_different_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = {'filename': 'Minecraft-Playden-Packager-v0.1.0-Windows-x64.zip', 'preview': True}
            metadata = {'source_dirty': True, 'preview': True}
            for name, data in [('artifact.json', record), ('build-metadata.json', metadata), ('content-audit.json', {})]:
                (root / name).write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'preview'):
                verify(root, root, BASELINE)

    def test_release_pair_checks_exact_commit_and_tree_for_both_formats(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directories = [root / 'windows', root / 'linux']
            for directory, suffix in zip(directories, ('Windows-x64.zip', 'Linux-x86_64.AppImage')):
                directory.mkdir()
                filename = 'Minecraft-Playden-Packager-v0.1.0-' + suffix
                data = b'fixture artifact only'
                (directory / filename).write_bytes(data)
                identity = {'source_commit': BASELINE, 'source_tree_sha256': 'a' * 64, 'preview': False}
                record = {**identity, 'filename': filename, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                          'content_audit_passed': True, 'frozen_smoke_passed': True}
                metadata = {**identity, 'source_dirty': False, 'version': '0.1.0',
                            'architecture': 'x86_64',
                            'platform': 'win32' if directory.name == 'windows' else 'linux',
                            'python': '3.11.9' if directory.name == 'windows' else '3.11.14',
                            'dependencies': {}, 'source_files': {}, 'branding': {}}
                for name, value in [('artifact.json', record), ('build-metadata.json', metadata),
                                    ('content-audit.json', {**identity, 'passed': True})]:
                    (directory / name).write_text(json.dumps(value))
            result = verify(*directories, BASELINE)
            self.assertTrue(result['automated_pair_checks_passed'])
            self.assertEqual(result['python_versions'], {'win32': '3.11.9', 'linux': '3.11.14'})
            path = directories[1] / 'build-metadata.json'
            metadata = json.loads(path.read_text())
            metadata['source_commit'] = '0' * 40
            path.write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, 'exact commit'):
                verify(*directories, BASELINE)
            metadata['source_commit'] = BASELINE
            metadata['python'] = '3.11.9'
            path.write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, 'Python patch version'):
                verify(*directories, BASELINE)


class FourPlatformTests(unittest.TestCase):
    def test_four_candidates_require_both_native_mac_architectures_and_same_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            targets = [('windows', 'Windows-x64.zip', 'win32', 'x86_64', '3.11.9'),
                       ('linux', 'Linux-x86_64.AppImage', 'linux', 'x86_64', '3.11.14'),
                       ('arm', 'macOS-ARM64.zip', 'darwin', 'arm64', '3.11.9'),
                       ('intel', 'macOS-x86_64.zip', 'darwin', 'x86_64', '3.11.9')]
            for name, suffix, platform, architecture, python in targets:
                directory = root / name
                directory.mkdir()
                filename = 'Minecraft-Playden-Packager-v0.1.0-' + suffix
                data = b'synthetic fixture'
                (directory / filename).write_bytes(data)
                identity = {'source_commit': BASELINE, 'source_tree_sha256': 'b' * 64, 'preview': False}
                record = {**identity, 'filename': filename, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                          'content_audit_passed': True, 'frozen_smoke_passed': True}
                metadata = {**identity, 'source_dirty': False, 'platform': platform, 'architecture': architecture,
                            'python': python, 'dependencies': {}, 'branding': {}, 'source_files': {}, 'version': '0.1.0'}
                audit = {**identity, 'passed': True, 'macos_bundle': {'architecture': architecture, 'icon_verified': True}}
                for filename, value in [('artifact.json', record), ('build-metadata.json', metadata), ('content-audit.json', audit)]:
                    (directory / filename).write_text(json.dumps(value))
            result = verify(root / 'windows', root / 'linux', BASELINE, root / 'arm', root / 'intel')
            self.assertEqual(len(result['artifacts']), 4)
            self.assertEqual(result['python_versions']['darwin-arm64'], '3.11.9')
            with self.assertRaisesRegex(ValueError, 'Both macOS'):
                verify(root / 'windows', root / 'linux', BASELINE, root / 'arm')
            path = root / 'intel/build-metadata.json'
            original = json.loads(path.read_text())
            for key, value, message in [('architecture', 'arm64', 'platform'), ('source_commit', '0' * 40, 'exact commit'),
                                        ('source_files', {'changed': 'hash'}, 'source or toolchain'), ('python', '3.11.14', 'Python')]:
                with self.subTest(key=key):
                    path.write_text(json.dumps({**original, key: value}))
                    with self.assertRaisesRegex(ValueError, message):
                        verify(root / 'windows', root / 'linux', BASELINE, root / 'arm', root / 'intel')
            path.write_text(json.dumps(original))
            artifact = root / 'intel/Minecraft-Playden-Packager-v0.1.0-macOS-x86_64.zip'
            artifact.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'checksum/size'):
                verify(root / 'windows', root / 'linux', BASELINE, root / 'arm', root / 'intel')


if __name__ == '__main__':
    unittest.main()
