"""USB transactions operate only on fixture packages and simulated mounted drives."""
import errno
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from minecraft_playden.operations import CancellationToken, OperationCancelled
from minecraft_playden.transfer import TransferRequest, TransferError
from minecraft_playden.volumes import DriveTarget, DriveUnavailable, VolumeIdentity, publish_without_replace


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'Minecraft Package'
        self.usb = self.root / 'USB'
        self.source.mkdir()
        self.usb.mkdir()
        (self.source / 'package.json').write_text('{"fixture":true}')
        (self.source / 'playden.args').write_text('fixture arguments')
        from minecraft_playden import recipe
        recipe.write(self.source, 'Minecraft fixture')
        (self.source / 'libraries').mkdir()
        (self.source / 'libraries/data.jar').write_bytes(b'fixture-content-' * 12000)
        (self.source / 'instances/default/minecraft').mkdir(parents=True)
        (self.source / 'empty-file').write_bytes(b'')
        self.original = self.snapshot(self.source)
        info = self.usb.stat()
        self.identity = VolumeIdentity('fixture-mounted-volume', 'exfat', info.st_dev, info.st_ino)
        mounted = patch('minecraft_playden.volumes._mounted_identity', return_value=self.identity)
        self.mounted = mounted.start()
        self.addCleanup(mounted.stop)
        validator = patch('minecraft_playden.transfer.validate', return_value=[])
        self.validator = validator.start()
        self.addCleanup(validator.stop)
        flush = patch('minecraft_playden.transfer.flush_volume', side_effect=lambda drive: drive.check())
        self.flush = flush.start()
        self.addCleanup(flush.stop)
        # This is a simulated volume under a temporary folder, not a native
        # Windows drive root accepted by GetDiskFreeSpaceW.
        allocation = patch('minecraft_playden.transfer.allocation_unit', return_value=4096)
        allocation.start()
        self.addCleanup(allocation.stop)
        self.drive = DriveTarget.capture(self.usb)
        self.request = TransferRequest(self.source, self.drive)

    @staticmethod
    def snapshot(root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}

    def assert_source_untouched(self):
        self.assertEqual(self.snapshot(self.source), self.original)

    def test_success_copies_complete_tree_checks_all_files_and_flushes_before_completion(self):
        events = []
        result = self.request.run(events.append)
        self.assertEqual(result.destination.resolve(), (self.usb / 'playden/import/Minecraft Package').resolve())
        self.assertEqual(self.snapshot(result.destination), self.original)
        self.assertEqual((result.destination / 'recipe.json').read_bytes(), self.original['recipe.json'])
        self.assertTrue((result.destination / 'instances/default/minecraft').is_dir())
        self.assertEqual(result.files, len(self.original))
        self.assertEqual(result.bytes_copied, sum(len(v) for v in self.original.values()))
        self.assertEqual(events[-1].stage, 'transfer-complete')
        log = '\n'.join(event.message for event in events)
        for text in ('Local package: Minecraft Package', 'PS5 destination: Minecraft Package', 'Transfer method: USB copy', 'Destination path:', 'Bytes transferred:', 'Transfer duration:', 'Average transfer speed:', 'Verification: SHA-256 verified'):
            self.assertIn(text, log)
        self.assertGreaterEqual(self.flush.call_count, 3)
        self.assertTrue(any(e.stage == 'transfer-copy' and e.current_file and e.bytes_total for e in events))
        self.assertTrue(any(e.stage == 'transfer-verify' and e.current_file for e in events))
        self.assertEqual(self.validator.call_count, 2)
        self.assertEqual({name: (size, sha) for name, size, sha in result.checksums},
                         {name: (len(data), hashlib.sha256(data).hexdigest()) for name, data in self.original.items()})
        self.assert_source_untouched()

    def test_fast_copy_skips_destination_checksum_pass_but_keeps_validation(self):
        from minecraft_playden.transfer import _checksum
        # Exercise distinct input/canonical spellings on every host, including
        # Linux, so Windows short temp aliases cannot hide this assumption.
        (self.root / 'alias').mkdir()
        source = self.root / 'alias' / '..' / self.source.name
        self.assertNotEqual(source, source.resolve())
        events, checksummed = [], []
        def checksum(path, *args, **kwargs):
            checksummed.append(path)
            return _checksum(path, *args, **kwargs)
        with patch('minecraft_playden.transfer._checksum', side_effect=checksum):
            result = TransferRequest(source, self.drive, verify_after_copy=False).run(events.append)
        self.assertFalse(result.verified)
        self.assertFalse(result.checksums)
        self.assertEqual(self.validator.call_count, 2)
        self.assertEqual(len(checksummed), len(self.original))
        self.assertEqual({path.relative_to(source.resolve()).as_posix() for path in checksummed},
                         set(self.original))
        self.assertNotIn('transfer-verify', [event.stage for event in events])
        self.assertIn('skipped', events[-1].message)
        self.assertEqual(self.snapshot(result.destination), self.original)
        self.assert_source_untouched()

    def test_fast_copy_still_rejects_invalid_destination_package(self):
        self.validator.side_effect = [[], ['Hash mismatch']]
        with self.assertRaisesRegex(TransferError, 'Transferred package validation failed'):
            TransferRequest(self.source, self.drive, verify_after_copy=False).run()
        self.assertFalse(self.request.destination.exists())

    def test_destination_alias_preserves_package_and_source(self):
        request = TransferRequest(self.source, self.drive, destination_name='Distinct Package-2')
        result = request.run()
        self.assertEqual(result.destination.name, 'Distinct Package-2')
        self.assertEqual(result.source, self.source.resolve())
        self.assertEqual(self.snapshot(result.destination), self.original)
        self.assertFalse(self.request.destination.exists())
        self.assert_source_untouched()

    def test_unsafe_destination_aliases_are_rejected_before_writing(self):
        for name in ('', '..', '../other', '/escape', 'CON', 'trailing.', 'nested/name'):
            with self.subTest(name=name), self.assertRaises(TransferError):
                TransferRequest(self.source, self.drive, destination_name=name).run()
        self.assertFalse((self.usb / 'playden').exists())
        self.assert_source_untouched()

    def test_invalid_source_never_publishes_or_copies(self):
        self.validator.return_value = ['Missing: critical.jar']
        with self.assertRaisesRegex(TransferError, 'Source package validation failed'):
            self.request.run()
        self.assertFalse(self.request.destination.exists())
        self.assert_source_untouched()

    def test_missing_and_corrupt_transferred_files_fail_and_retry_repairs_private_staging(self):
        for fault in ('missing', 'corrupt'):
            with self.subTest(fault=fault):
                # Independent content identity/package name for each subtest.
                source = self.root / f'Package-{fault}'
                shutil.copytree(self.source, source)
                request = TransferRequest(source, self.drive)
                events = []
                damaged = False
                def progress(event):
                    nonlocal damaged
                    events.append(event)
                    if event.stage == 'transfer-flush' and not damaged:
                        target = next((self.usb / 'playden/.packager-transfers').glob('*/payload/libraries/data.jar'))
                        if fault == 'missing':
                            target.unlink()
                        else:
                            target.write_bytes(b'x' * target.stat().st_size)
                        damaged = True
                with self.assertRaises(TransferError):
                    request.run(progress)
                self.assertFalse(request.destination.exists())
                self.assertNotIn('transfer-complete', [e.stage for e in events])
                result = request.run()
                self.assertEqual(self.snapshot(result.destination), self.original)
        self.assert_source_untouched()

    def test_existing_destination_is_never_overwritten_even_if_identical(self):
        self.request.destination.mkdir(parents=True)
        sentinel = self.request.destination / 'unrelated'
        sentinel.write_text('keep')
        with self.assertRaisesRegex(TransferError, 'already exists'):
            self.request.run()
        self.assertEqual(sentinel.read_text(), 'keep')
        self.assert_source_untouched()

    def test_atomic_destination_race_does_not_replace_unrelated_directory(self):
        def race(source, destination):
            destination.mkdir()
            (destination / 'other').write_text('keep')
            publish_without_replace(source, destination)
        with patch('minecraft_playden.transfer.publish_without_replace', side_effect=race):
            with self.assertRaises(TransferError):
                self.request.run()
        self.assertEqual((self.request.destination / 'other').read_text(), 'keep')
        self.assert_source_untouched()

    def test_insufficient_space_and_permission_errors_do_not_report_success(self):
        with patch('minecraft_playden.transfer.shutil.disk_usage', return_value=shutil._ntuple_diskusage(100, 100, 0)):
            with self.assertRaisesRegex(TransferError, 'Insufficient'):
                self.request.run()
        with patch('minecraft_playden.transfer._state', side_effect=PermissionError(errno.EACCES, 'Permission denied')):
            with self.assertRaisesRegex(TransferError, 'Permission denied'):
                self.request.run()
        self.assertFalse(self.request.destination.exists())
        self.assert_source_untouched()

    def test_cancel_before_start_and_during_copy_are_distinct_and_retryable(self):
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(OperationCancelled):
            self.request.run(cancellation=token)
        token = CancellationToken()
        events = []
        def progress(event):
            events.append(event)
            if event.stage == 'transfer-copy':
                token.cancel()
        with self.assertRaises(OperationCancelled):
            self.request.run(progress, token)
        self.assertFalse(self.request.destination.exists())
        self.assertNotIn('transfer-complete', [e.stage for e in events])
        self.assertEqual(self.snapshot(self.request.run().destination), self.original)
        self.assert_source_untouched()

    def test_cancel_during_verification_keeps_verified_copy_private(self):
        token = CancellationToken()
        def progress(event):
            if event.stage == 'transfer-verify':
                token.cancel()
        with self.assertRaises(OperationCancelled):
            self.request.run(progress, token)
        self.assertFalse(self.request.destination.exists())
        self.assertEqual(self.snapshot(self.request.run().destination), self.original)

    def test_disconnection_stops_without_falling_back_to_host_mountpoint_and_can_retry(self):
        def progress(event):
            if event.stage == 'transfer-copy':
                self.mounted.side_effect = DriveUnavailable('disconnected')
        with self.assertRaisesRegex(TransferError, 'unavailable|changed'):
            self.request.run(progress)
        self.assertFalse(self.request.destination.exists())
        self.mounted.side_effect = None
        self.mounted.return_value = self.identity
        self.assertEqual(self.snapshot(self.request.run().destination), self.original)
        self.assert_source_untouched()

    def test_sync_failure_never_reports_success_and_can_retry_after_publish(self):
        calls = 0
        events = []
        def flush(drive):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError(errno.EIO, 'USB sync failed')
        with patch('minecraft_playden.transfer.flush_volume', side_effect=flush):
            with self.assertRaisesRegex(TransferError, 'sync failed'):
                self.request.run(events.append)
        self.assertNotIn('transfer-complete', [e.stage for e in events])
        self.assertTrue(self.request.destination.exists())
        before = self.snapshot(self.request.destination)
        self.request.run()
        self.assertEqual(before, self.snapshot(self.request.destination))

    def test_source_mutation_is_detected(self):
        changed = False
        def progress(event):
            nonlocal changed
            if event.stage == 'transfer-copy' and not changed:
                (self.source / 'playden.args').write_text('changed')
                changed = True
        with self.assertRaisesRegex(TransferError, 'Source changed'):
            self.request.run(progress)
        self.assertFalse(self.request.destination.exists())

    @unittest.skipIf(os.name == 'nt', 'Windows symlinks may require privileges')
    def test_source_and_destination_symlinks_are_rejected(self):
        (self.source / 'link').symlink_to(self.source / 'playden.args')
        with self.assertRaisesRegex(TransferError, 'Symlinks'):
            self.request.run()
        (self.source / 'link').unlink()
        outside = self.root / 'unrelated'
        outside.mkdir()
        (self.usb / 'playden').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(TransferError, 'Unsafe'):
            self.request.run()
        self.assertEqual(list(outside.iterdir()), [])

    def test_unplanned_staging_files_are_not_deleted_on_retry(self):
        token = CancellationToken()
        def progress(event):
            if event.stage == 'transfer-copy':
                token.cancel()
        with self.assertRaises(OperationCancelled):
            self.request.run(progress, token)
        payload = next((self.usb / 'playden/.packager-transfers').glob('*/payload'))
        other = payload / 'unrelated.txt'
        other.write_text('keep')
        with self.assertRaisesRegex(TransferError, 'unrelated'):
            self.request.run()
        self.assertEqual(other.read_text(), 'keep')

    def test_retry_cleans_only_the_checkpoint_recorded_temporary_file(self):
        token = CancellationToken()
        def progress(event):
            if event.stage == 'transfer-copy':
                token.cancel()
        with self.assertRaises(OperationCancelled):
            self.request.run(progress, token)
        state_path = next((self.usb / 'playden/.packager-transfers').glob('*/state.json'))
        state = json.loads(state_path.read_text())
        recorded = state_path.parent / state['active_temp']
        recorded.write_bytes(b'interrupted bytes')
        unknown = state_path.parent / 'unrelated.part'
        unknown.write_bytes(b'keep')
        self.request.run()
        self.assertFalse(recorded.exists())
        self.assertEqual(unknown.read_bytes(), b'keep')

    def test_atomic_publish_refuses_even_an_empty_existing_directory(self):
        stage = self.root / 'stage'
        stage.mkdir()
        target = self.root / 'existing'
        target.mkdir()
        with self.assertRaises(OSError):
            publish_without_replace(stage, target)
        self.assertTrue(stage.exists())
        self.assertTrue(target.exists())

    def test_changed_mount_identity_is_rejected(self):
        self.mounted.return_value = VolumeIdentity('different-drive', 'exfat', 1, 2)
        with self.assertRaisesRegex(TransferError, 'changed'):
            self.request.run()
        self.assertFalse(self.request.destination.exists())
