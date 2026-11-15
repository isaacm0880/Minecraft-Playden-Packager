"""Recipe contract, filesystem validation and optional upstream parser checks."""
import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from minecraft_playden import recipe


class RecipeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'Package With Spaces'
        (self.root / 'java/bin').mkdir(parents=True)
        (self.root / recipe.PROGRAM).write_bytes(b'MZfixture')
        (self.root / 'playden.args').write_text('-version\n')
        recipe.write(self.root, 'Minecraft 26.3')

    def save(self, document):
        (self.root / 'recipe.json').write_text(json.dumps(document), encoding='utf-8')

    def test_minimal_contract_and_working_folder(self):
        document = json.loads((self.root / 'recipe.json').read_text())
        self.assertEqual(set(document), {'recipe_id', 'recipe_version', 'game', 'launch_entries',
                                         'executable', 'architecture', 'graphics', 'community'})
        self.assertEqual(document['community'], {'format_version': 1, 'status': 'untested'})
        self.assertEqual(document['architecture'], 'pe64')
        entry = document['launch_entries'][0]
        self.assertNotIn('working_directory', entry)
        self.assertEqual((self.root / entry['executable']).parent, self.root / 'java/bin')
        self.assertEqual(((self.root / entry['executable']).parent / entry['arguments'][1:]).resolve(),
                         (self.root / 'playden.args').resolve())
        self.assertEqual(recipe.validate(self.root, 'Minecraft 26.3'), [])
        self.assertFalse((self.root / 'launch.args').exists())

    def test_name_keys(self):
        for name, key in [
            ('Crème Brûlée & Clockwork — déjà vu', 'creme-brulee-clockwork-deja-vu'),
            ('ＦＵＬＬ　ＷＩＤＴＨ', 'full-width'), ('Minecraft 26.3', 'minecraft-26-3'),
            ('  A___B---C  ', 'a-b-c'), ('\u0301\u0308', 'game'), ('世界', 'game'),
            ('', 'game'), ('x' * 47 + ' y', 'x' * 47), ('x' * 80, 'x' * 48),
        ]:
            with self.subTest(name=name):
                self.assertEqual(recipe.name_key(name), key)

    def test_invalid_game_names(self):
        for name in ['', ' ' * 3, '\u0301', '\u00a0', 'x' * 121, 'A\nB', 'A\tB',
                     'A\u200dB', '\ue000', '\ud800', '\u0378', 'A\u2028B', 'A\u2029B', None, 42]:
            with self.subTest(name=repr(name)), self.assertRaises(ValueError):
                recipe.generate(name)

    def test_valid_unicode_names(self):
        for name in ['世界', 'ＦＵＬＬ　ＷＩＤＴＨ', 'Crème Brûlée', 'x' * 120]:
            with self.subTest(name=name):
                self.save(recipe.generate(name))
                self.assertEqual(recipe.validate(self.root, name), [])

    def test_missing_launch_files(self):
        for name in [recipe.PROGRAM, 'playden.args', 'recipe.json']:
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.unlink()
                self.assertTrue(recipe.validate(self.root))
                path.mkdir()
                self.assertTrue(recipe.validate(self.root))
                path.rmdir()
                path.write_bytes(original)

    def test_invalid_launch_paths(self):
        original = recipe.generate('Minecraft 26.3')
        for field, values in [
            ('executable', ['../escape.exe', '/tmp/java.exe', 'C:/java.exe', r'java\bin\java.exe', 'other.exe']),
            ('working_directory', ['../../outside', '/', 'C:/', '.', 'java']),
            ('arguments', ['@launch.args', '@../../missing.args', '@../../../outside.args', '@C:/playden.args']),
        ]:
            for value in values:
                with self.subTest(field=field, value=value):
                    document = copy.deepcopy(original)
                    document['launch_entries'][0][field] = value
                    if field == 'executable':
                        document['executable'] = value
                    self.save(document)
                    self.assertTrue(recipe.validate(self.root))

    def test_escaping_symlinks(self):
        outside = Path(self.temporary.name) / 'outside'
        outside.write_text('outside')
        for name in [recipe.PROGRAM, 'playden.args', 'recipe.json']:
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.unlink()
                try:
                    path.symlink_to(outside)
                except OSError:
                    path.write_bytes(original)
                    self.skipTest('Symlinks unavailable on this host')
                self.assertTrue(recipe.validate(self.root))
                path.unlink()
                path.write_bytes(original)

    def test_malformed_or_nonminimal_recipes(self):
        for document in [None, [], {}, {'game': []}, {'game': {'name': 3}}]:
            with self.subTest(document=document):
                self.save(document)
                self.assertTrue(recipe.validate(self.root))
        for text in ['{', '\ufffd', ' ' * (recipe.MAX_RECIPE_BYTES + 1)]:
            (self.root / 'recipe.json').write_text(text, encoding='utf-8')
            self.assertTrue(recipe.validate(self.root))
        for field, value in [('architecture', 'pe32'), ('graphics', 'dxvk'),
                             ('recipe_version', True), ('recipe_version', 1.0), ('defaults', {}),
                             ('community', {'format_version': 1, 'status': 'works'})]:
            with self.subTest(field=field, value=value):
                document = recipe.generate('Minecraft 26.3')
                document[field] = value
                self.save(document)
                self.assertTrue(recipe.validate(self.root))

    def test_package_identity_must_match(self):
        self.assertTrue(recipe.validate(self.root, 'Minecraft 1.21'))

    def test_write_does_not_overwrite(self):
        original = (self.root / 'recipe.json').read_bytes()
        with self.assertRaises(FileExistsError):
            recipe.write(self.root, 'Minecraft 1.21')
        self.assertEqual((self.root / 'recipe.json').read_bytes(), original)

    @unittest.skipUnless(os.environ.get('PLAYDEN_RECIPE_CHECK'), 'Set PLAYDEN_RECIPE_CHECK for upstream integration')
    def test_upstream_parser_rules_and_effective_launch(self):
        checker = os.environ['PLAYDEN_RECIPE_CHECK']
        for name in ['Minecraft 26.3', 'Crème Brûlée & Clockwork — déjà vu', 'ＦＵＬＬ　ＷＩＤＴＨ', '世界', 'x' * 120]:
            with self.subTest(name=name):
                self.save(recipe.generate(name))
                result = subprocess.run([checker, str(self.root / 'recipe.json')], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('cwd java/bin, opengl', result.stdout)
        for field in ['community', 'executable', 'launch_entries', 'recipe_id', 'recipe_version', 'game']:
            with self.subTest(missing=field):
                document = recipe.generate('Minecraft 26.3')
                del document[field]
                self.save(document)
                result = subprocess.run([checker, str(self.root / 'recipe.json')], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
