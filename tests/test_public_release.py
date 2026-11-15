"""Public-source privacy, links and candidate workflow regressions."""
import json
from pathlib import Path
import re
import tempfile
import unittest

from release.provenance import application_hash
from release.public_audit import audit_paths, SYNTHETIC_CLIENT

ROOT = Path(__file__).resolve().parents[1]


class PublicReleaseTests(unittest.TestCase):
    def test_application_matches_reviewed_snapshot(self):
        policy = json.loads((ROOT / 'release/source-policy.json').read_text())
        self.assertEqual(application_hash(ROOT), policy['application_tree_sha256'])

    def test_public_document_relative_links_resolve(self):
        documents = list(ROOT.glob('*.md')) + list((ROOT / 'docs').glob('*.md'))
        for path in documents:
            for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
                if '://' in target or target.startswith('#'):
                    continue
                with self.subTest(document=path.name, target=target):
                    self.assertTrue((path.parent / target.split('#')[0]).exists())

    def test_manual_candidate_uses_same_commit_for_every_target(self):
        workflow = (ROOT / '.github/workflows/desktop-build.yml').read_text()
        self.assertIn('workflow_dispatch:', workflow)
        self.assertNotIn('contents: write', workflow)
        self.assertIn("requested != os.environ['WORKFLOW_SHA']", workflow)
        self.assertEqual(workflow.count('ref: ${{ needs.source.outputs.sha }}'), 4)
        for expected in ('windows-2022', 'macos-15-intel', 'architecture: arm64',
                         'release.verify_pair', 'SHA256SUMS', 'release.public_audit',
                         '--native-cocoa', 'QT_QPA_PLATFORM=xcb'):
            self.assertIn(expected, workflow)

    def test_synthetic_game_fixture_is_pinned(self):
        self.assertTrue(audit_paths(ROOT, [SYNTHETIC_CLIENT])['passed'])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / SYNTHETIC_CLIENT
            path.parent.mkdir(parents=True)
            path.write_bytes(b'replacement')
            with self.assertRaisesRegex(ValueError, 'fixture content changed'):
                audit_paths(root, [SYNTHETIC_CLIENT])

    def test_private_inputs_and_obsolete_instructions_are_rejected(self):
        cases = [('client.jar', b'game'), ('accounts.json', b'account'),
                 ('guide.md', b'Import' + b' recipe through Playden'),
                 ('guide.md', b'Minecraft-Playden-Packager' + b'-DEV')]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, data in cases:
                with self.subTest(name=name, data=data):
                    (root / name).write_bytes(data)
                    with self.assertRaises(ValueError):
                        audit_paths(root, [name])

    def test_credential_error_suppresses_value(self):
        token = b'ghp_' + b'A' * 36
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'note.txt').write_bytes(token)
            with self.assertRaisesRegex(ValueError, 'value suppressed') as error:
                audit_paths(root, ['note.txt'])
            self.assertNotIn(token.decode(), str(error.exception))
