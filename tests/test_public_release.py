"""Public-source privacy, links and candidate workflow regressions."""
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from release.provenance import candidate_source, check_source
from release.public_audit import audit_paths, SYNTHETIC_CLIENT
from release.verify_pair import write_report
from release.build_desktop import bundle_documentation

ROOT = Path(__file__).resolve().parents[1]


class PublicReleaseTests(unittest.TestCase):
    def test_unreleased_features_cannot_reuse_historical_release_approval(self):
        policy = json.loads((ROOT / 'release/source-policy.json').read_text(encoding="utf-8"))
        # A stale application hash must fail even after this source snapshot is
        # eventually reviewed and the policy is legitimately updated.
        stale = '0' * 64 if policy['application_tree_sha256'] != '0' * 64 else '1' * 64
        with patch('release.provenance.application_hash', return_value=stale), patch('release.provenance.git', return_value='a' * 40), patch('release.provenance.subprocess.run'), self.assertRaisesRegex(ValueError, 'explicit source review is required'):
            check_source(ROOT, preview=True)


    def test_bundled_guides_keep_relative_links_and_licensing_notices(self):
        for macos in (False, True):
            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary)
                bundle_documentation(ROOT, output, macos=macos)
                for name in ('README.md', 'INSTALLATION.md', 'USAGE.md', 'COMPATIBILITY.md',
                             'TROUBLESHOOTING.md', 'PLAYDEN-RECIPES.md', 'CHANGELOG.md', 'THIRD_PARTY.md', 'LICENSE'):
                    self.assertTrue((output / name).is_file(), name)
                self.assertEqual((output / 'LICENSE').read_bytes(), (ROOT / 'LICENSE').read_bytes())
                self.assertEqual((output / 'README-MACOS.md').exists(), macos)
                for notice in ('SDK-RECEIVER-NOTICE.md', 'SDK-GPL-3.txt',
                               'BearSSL-LICENSE.txt', 'sha-256-LICENSE.md'):
                    self.assertTrue((output / 'receiver/notices' / notice).is_file(), notice)
                for path in output.glob('*.md'):
                    for target in re.findall(r'\]\(([^)]+)\)', path.read_text(encoding="utf-8")):
                        if '://' not in target and not target.startswith('#'):
                            self.assertTrue((output / target.split('#')[0]).exists(), target)

    def test_public_document_relative_links_resolve(self):
        documents = list(ROOT.glob('*.md')) + list((ROOT / 'docs').glob('*.md'))
        for path in documents:
            for target in re.findall(r'\]\(([^)]+)\)', path.read_text(encoding="utf-8")):
                if '://' in target or target.startswith('#'):
                    continue
                with self.subTest(document=path.name, target=target):
                    self.assertTrue((path.parent / target.split('#')[0]).exists())

    def test_manual_candidate_uses_same_commit_for_every_target(self):
        workflow = (ROOT / '.github/workflows/desktop-build.yml').read_text(encoding="utf-8")
        self.assertIn('workflow_dispatch:', workflow)
        self.assertNotIn('contents: write', workflow)
        self.assertIn("requested = candidate_source(", workflow)
        self.assertNotIn("  push:", workflow)
        self.assertEqual(workflow.count('ref: ${{ needs.source.outputs.sha }}'), 4)
        self.assertIn('Receiver-${{ needs.source.outputs.sha }}', workflow)
        for expected in ('windows-2022', 'release.verify_pair', 'SHA256SUMS',
                         'release.public_audit', 'QT_QPA_PLATFORM=xcb'):
            self.assertIn(expected, workflow)
        self.assertNotIn('  macos:\n', workflow)

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


class CandidateSourceTests(unittest.TestCase):
    def test_preparation_push_uses_its_exact_commit(self):
        sha = 'a' * 40
        self.assertEqual(candidate_source('push', 'refs/heads/release/v0.1.0-prep', sha, ''), sha)

    def test_manual_dispatch_requires_explicit_matching_commit(self):
        sha = 'b' * 40
        self.assertEqual(candidate_source('workflow_dispatch', 'refs/heads/main', sha, sha), sha)
        for requested in ('', 'b' * 7, 'c' * 40, 'B' * 40, 'z' * 40):
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                candidate_source('workflow_dispatch', 'refs/heads/main', sha, requested)

    def test_other_pushes_events_and_revision_overrides_are_blocked(self):
        for event, ref, requested in (
            ('push', 'refs/heads/main', ''), ('push', 'refs/tags/v0.1.0', ''),
            ('pull_request', 'refs/pull/1/merge', ''),
            ('push', 'refs/heads/release/v0.1.0-prep', 'a' * 40)):
            with self.subTest(event=event, ref=ref), self.assertRaises(ValueError):
                candidate_source(event, ref, 'a' * 40, requested)


class CandidateEvidenceTests(unittest.TestCase):
    def test_combined_checksums_and_report_preserve_existing_evidence(self):
        report = {'artifacts': [{'sha256': 'a' * 64, 'filename': 'candidate.zip'}]}
        for existing in ('release-pair.json', 'SHA256SUMS'):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / existing).write_text('existing evidence')
                with self.assertRaisesRegex(ValueError, 'existing evidence'):
                    write_report(root / 'release-pair.json', report)
                self.assertEqual((root / existing).read_text(encoding="utf-8"), 'existing evidence')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_report(root / 'release-pair.json', report)
            self.assertEqual(json.loads((root / 'release-pair.json').read_text(encoding="utf-8")), report)
            self.assertEqual((root / 'SHA256SUMS').read_text(encoding="utf-8"), 'a' * 64 + '  candidate.zip\n')
