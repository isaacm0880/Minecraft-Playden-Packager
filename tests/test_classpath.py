"""Classpath validation must detect archives and classes missing at runtime."""
import copy
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock

from minecraft_playden.arguments import classpath_argument, generate, parse_argfile, quote, write
from minecraft_playden.classpath import FLOAT_LIST, argument_classpath, validate_classpath
from minecraft_playden.dependencies import classpath, resolve
from minecraft_playden.versions import VersionResolver

FIXTURES = Path(__file__).parent / 'fixtures'
METADATA = json.loads((FIXTURES / 'version.json').read_text())
FASTUTIL_PATH = 'libraries/it/unimi/dsi/fastutil/8.5.18/fastutil-8.5.18.jar'


def jar(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as archive:
        for name in entries:
            archive.writestr(name, b'fixture-class-content')


def package(root, metadata=None):
    metadata = copy.deepcopy(METADATA if metadata is None else metadata)
    (root / 'java/bin').mkdir(parents=True)
    (root / 'versions').mkdir()
    (root / 'versions/client.jar').write_bytes((FIXTURES / 'client.jar').read_bytes())
    for spec, _, _ in resolve(metadata):
        entries = [FLOAT_LIST] if 'fastutil' in spec['path'] else ['io/netty/channel/unix/Errors.class'] if 'netty' in spec['path'] else ['lwjgl.dll']
        jar(root / 'libraries' / spec['path'], entries)
    write(root, metadata, classpath(metadata), 'Player')
    return metadata


class ClasspathTests(unittest.TestCase):
    def test_windows_classpath_resolves_all_jars_from_java_bin_on_any_host(self):
        with tempfile.TemporaryDirectory(prefix='Package With Spaces ') as temp:
            root = Path(temp)
            metadata = package(root)
            self.assertEqual(validate_classpath(root, metadata, classpath(metadata)), [])
            tokens = parse_argfile((root / 'playden.args').read_text())
            entries = classpath_argument(tokens, metadata['mainClass']).split(';')
            self.assertTrue(all(entry.startswith('../../') for entry in entries))
            self.assertIn('../../' + FASTUTIL_PATH, entries)

    def test_missing_fastutil_jar_is_reported_with_working_directory_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = package(root)
            (root / FASTUTIL_PATH).unlink()
            errors = validate_classpath(root, metadata, classpath(metadata))
            self.assertTrue(any('Required classpath JAR missing' in e and 'fastutil' in e for e in errors))
            self.assertTrue(any('playden.args' in e and str(Path('java') / 'bin') in e and 'fastutil' in e for e in errors))
            self.assertTrue(any(FLOAT_LIST in e for e in errors))

    def test_wrong_cwd_prefix_and_omitted_argument_entry_fail_independently_of_regeneration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = package(root)
            expected = classpath(metadata)
            (root / 'playden.args').write_text(generate(metadata, expected, ''))
            errors = argument_classpath(root, 'playden.args', root / 'java/bin', metadata['mainClass'], expected)
            self.assertTrue(any('classpath JAR missing' in e for e in errors))
            (root / 'playden.args').write_text(generate(metadata, [p for p in expected if p != FASTUTIL_PATH], '../../'))
            errors = argument_classpath(root, 'playden.args', root / 'java/bin', metadata['mainClass'], expected)
            self.assertTrue(any('Missing:' in e and FASTUTIL_PATH in e for e in errors))

    def test_missing_fastutil_metadata_cannot_pass_self_consistent_classpath_validation(self):
        metadata = copy.deepcopy(METADATA)
        metadata['libraries'] = [lib for lib in metadata['libraries'] if not lib['name'].startswith('it.unimi.dsi:fastutil:')]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = package(root, metadata)
            errors = validate_classpath(root, metadata, classpath(metadata))
            self.assertTrue(any('absent from the exported classpath' in e and FLOAT_LIST in e for e in errors))

    def test_valid_hashed_jar_missing_floatlist_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = package(root)
            path = root / FASTUTIL_PATH
            jar(path, ['it/unimi/dsi/fastutil/ints/IntList.class'])
            spec = next(lib['downloads']['artifact'] for lib in metadata['libraries'] if lib['name'].startswith('it.unimi.dsi:fastutil:'))
            spec.update(sha1=hashlib.sha1(path.read_bytes()).hexdigest(), size=path.stat().st_size)
            errors = validate_classpath(root, metadata, classpath(metadata))
            self.assertTrue(any('Critical dependency class missing' in e and FLOAT_LIST in e for e in errors))
            self.assertFalse(any('mismatch' in e for e in errors))

    def test_corrupt_or_empty_classpath_archives_and_critical_members_fail(self):
        for content in (b'not a zip', b'PK\x03\x04truncated'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                metadata = package(root)
                (root / FASTUTIL_PATH).write_bytes(content)
                self.assertTrue(any('Invalid classpath JAR' in e for e in validate_classpath(root, metadata, classpath(metadata))))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = package(root)
            with zipfile.ZipFile(root / FASTUTIL_PATH, 'w') as archive:
                archive.writestr(FLOAT_LIST, b'')
            self.assertTrue(any('class is empty' in e for e in validate_classpath(root, metadata, classpath(metadata))))

    def test_main_class_and_declared_dependency_entry_points_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = package(root)
            jar(root / 'versions/client.jar', ['wrong/Main.class'])
            jar(root / 'libraries/netty/common.jar', ['wrong/Errors.class'])
            errors = validate_classpath(root, metadata, classpath(metadata))
            self.assertTrue(any('net/minecraft/client/main/Main.class' in e for e in errors))
            self.assertTrue(any('io/netty/channel/unix/Errors.class' in e for e in errors))

    def test_official_dependency_hash_and_size_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            metadata = package(root)
            spec = next(lib['downloads']['artifact'] for lib in metadata['libraries'] if lib['name'].startswith('it.unimi.dsi:fastutil:'))
            spec.update(sha1='0' * 40, size=0)
            errors = validate_classpath(root, metadata, classpath(metadata))
            self.assertTrue(any('official SHA-1 mismatch' in e for e in errors))
            self.assertTrue(any('size mismatch' in e for e in errors))

    def test_required_library_without_download_must_not_be_silently_dropped(self):
        metadata = copy.deepcopy(METADATA)
        metadata['libraries'].append({'name': 'required:missing:1'})
        with self.assertRaisesRegex(ValueError, 'no downloadable artifact'):
            resolve(metadata)

    def test_version_response_must_match_requested_local_source_version(self):
        downloader = Mock()
        downloader.json.side_effect = [{'versions': [{'id': '26.3', 'url': 'https://fixture/version'}]}, METADATA]
        with self.assertRaisesRegex(ValueError, 'does not match'):
            VersionResolver(downloader).resolve('26.3')

    def test_java_argfile_quoting_not_shell_splitting(self):
        values = ['-cp', '../../path with spaces/a.jar;../../libraries/b.jar', 'a"b', r'a\b', '#literal', '', 'line\nnext']
        self.assertEqual(parse_argfile('\n'.join(quote(value) for value in values)), values)
        self.assertEqual(parse_argfile('# comment\n-cp\n"one;two" # comment\nMain\n'), ['-cp', 'one;two', 'Main'])
        self.assertEqual(parse_argfile('"line\\\n   continued"'), ['linecontinued'])
        self.assertEqual(parse_argfile("'single quoted'"), ['single quoted'])
        with self.assertRaisesRegex(ValueError, 'Unterminated'):
            parse_argfile('"truncated')
        with self.assertRaises(ValueError):
            classpath_argument(['-cp', 'a', '-cp', 'b', 'Main'], 'Main')
        with self.assertRaises(ValueError):
            classpath_argument(['Main', '-cp', 'a'], 'Main')
