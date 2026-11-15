"""Optional offscreen tests for reusable dialogs and worker result semantics."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

HAS_QT = importlib.util.find_spec('PySide6') is not None
if HAS_QT:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QDialog, QWidget
    from minecraft_playden.gui_components import CompletionDialog, DriveDialog, Task
    from minecraft_playden.operations import CancellationToken
    from minecraft_playden.volumes import DriveTarget, VolumeIdentity


@unittest.skipUnless(HAS_QT, 'Install .[gui] to run Qt tests')
class ComponentTests(unittest.TestCase):
    def test_many_worker_progress_signals_and_interpreter_shutdown_are_safe(self):
        # A real package emits thousands of progress signals. Keep this in a
        # subprocess so a native binding regression reports failure safely.
        code = '''
from PySide6.QtCore import QCoreApplication
from minecraft_playden.gui_components import Task
from minecraft_playden.operations import CancellationToken
from minecraft_playden.progress import Progress
app = QCoreApplication([])
def work(progress):
    for number in range(6000):
        progress(Progress('validation', 'Hashing', number, 6000))
task = Task(work, CancellationToken())
events, results, errors, finished = [], [], [], []
task.progress.connect(events.append)
task.result.connect(results.append)
task.error.connect(errors.append)
task.finished.connect(lambda: finished.append(True))
task.run()
assert len(events) == 6000 and len(results) == 1 and not errors and finished == [True]
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_worker_reports_actionable_error_and_path_without_traceback(self):
        def denied(progress):
            raise PermissionError(13, 'Access denied', 'C:/Users/Tester/package')
        task = Task(denied, CancellationToken())
        errors, results, finished = [], [], []
        task.error.connect(errors.append)
        task.result.connect(results.append)
        task.finished.connect(lambda: finished.append(True))
        task.run()
        self.assertIn('writable output/cache', errors[0])
        self.assertNotIn('PermissionError:', errors[0])
        self.assertNotIn('Traceback', errors[0])
        self.assertIn('C:/Users/Tester/package', errors[0])
        self.assertEqual(results, [])
        self.assertEqual(finished, [True])

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.parent = QWidget()

    def tearDown(self):
        self.parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.temp.cleanup()

    def test_verification_defaults_on_and_fast_result_does_not_claim_verified(self):
        with patch('minecraft_playden.gui_components.mounted_drives', return_value=[]):
            drive = DriveDialog('Pack', self.parent)
        self.assertFalse(drive.skip_verification.isChecked())
        dialog = CompletionDialog(self.root / 'Pack', self.parent, Mock(), transferred=True, verified=False)
        self.assertIn('skipped', dialog.windowTitle())
        self.assertIn('not been fully verified', dialog.detail.toPlainText())
        drive.close()
        dialog.close()

    def test_check_again_recalculates_free_space_and_handles_disconnection(self):
        import shutil
        from minecraft_playden.gui_components import SpaceDialog
        drive = DriveTarget(self.root, VolumeIdentity('fixture', 'exfat', 0, 0))
        with patch('minecraft_playden.volumes.DriveTarget.check'), patch('minecraft_playden.gui_components.shutil.disk_usage',
                side_effect=[shutil._ntuple_diskusage(1000, 950, 50), shutil._ntuple_diskusage(1000, 500, 500)]) as usage:
            dialog = SpaceDialog(drive, 100, self.parent)
            self.assertFalse(dialog.retry_button.isEnabled())
            dialog.check_button.click()
            self.assertTrue(dialog.retry_button.isEnabled())
            self.assertEqual(usage.call_count, 2)
        with patch('minecraft_playden.volumes.DriveTarget.check', side_effect=OSError('Disconnected')):
            dialog.check_button.click()
            self.assertFalse(dialog.retry_button.isEnabled())
            self.assertIn('Disconnected', dialog.message.text())
        dialog.close()

    def test_queue_completion_lists_all_items_and_uses_exact_configuration(self):
        from minecraft_playden.gui_components import QueueCompletionDialog
        from minecraft_playden.exporter import INSTRUCTIONS
        summary = '3 instances: 1 succeeded, 1 failed, 0 cancelled, 1 not started.\nPack A: completed\nPack B: failed\nPack C: not started'
        opened = Mock()
        dialog = QueueCompletionDialog(summary, self.root, self.parent, opened, has_packages=True)
        self.assertEqual(dialog.detail.toPlainText(), summary + '\n\n' + INSTRUCTIONS)
        dialog.open_button.click()
        opened.assert_called_once()
        dialog.close()
        dialog = QueueCompletionDialog('0 succeeded, 3 failed', self.root / 'missing', self.parent, Mock(), has_packages=False)
        self.assertFalse(dialog.open_button.isEnabled())
        self.assertNotIn(INSTRUCTIONS, dialog.detail.toPlainText())
        dialog.close()

    def test_batch_completion_copy_button_calls_the_shared_action(self):
        from minecraft_playden.gui_components import QueueCompletionDialog
        copy = Mock()
        dialog = QueueCompletionDialog('Three completed packages', self.root, self.parent, Mock(),
                                       has_packages=True, copy_usb=copy)
        self.assertEqual(dialog.copy_button.text(), 'Copy to USB')
        dialog.copy_button.click()
        copy.assert_called_once()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_drive_picker_previews_every_distinct_destination_and_collisions(self):
        existing = self.root / 'playden/import/Pack'
        existing.mkdir(parents=True)
        with patch('minecraft_playden.gui_components.mounted_drives', return_value=[('Fixture USB', self.root)]):
            dialog = DriveDialog('Pack', self.parent, package_names=['Pack', 'Pack', 'Other'])
        self.assertIn(str(existing.with_name('Pack-2')), dialog.destination.text())
        self.assertIn(str(existing.with_name('Pack-3')), dialog.destination.text())
        self.assertIn(str(existing.with_name('Other')), dialog.destination.text())
        self.assertFalse(dialog.skip_verification.isChecked())
        dialog.close()

    def test_completion_dialog_names_directory_and_three_actions(self):
        open_folder, copy = Mock(), Mock()
        dialog = CompletionDialog(self.root / 'My Minecraft', self.parent, open_folder, copy)
        self.assertIn('My Minecraft', dialog.package_label.text())
        self.assertEqual(dialog.path_label.text(), str(self.root / 'My Minecraft'))
        self.assertEqual(dialog.open_button.text(), 'Open Output Folder')
        self.assertEqual(dialog.copy_button.text(), 'Copy to USB')
        self.assertEqual(dialog.close_button.text(), 'Close')
        dialog.open_button.click()
        open_folder.assert_called_once()
        dialog.copy_button.click()
        copy.assert_called_once()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_close_dialog_does_not_open_or_transfer(self):
        open_folder, copy = Mock(), Mock()
        dialog = CompletionDialog(self.root / 'My Minecraft', self.parent, open_folder, copy)
        dialog.close_button.click()
        open_folder.assert_not_called()
        copy.assert_not_called()

    def test_launch_completion_has_scrollable_configuration(self):
        dialog = CompletionDialog(self.root / 'Minecraft', self.parent, Mock())
        dialog.resize(640, 400)
        dialog.show()
        self.app.processEvents()
        from minecraft_playden.exporter import INSTRUCTIONS
        self.assertIn(INSTRUCTIONS, dialog.detail.toPlainText())
        self.assertNotIn('Import recipe', dialog.detail.toPlainText())
        self.assertTrue(dialog.detail.isReadOnly())
        self.assertLessEqual(dialog.height(), 400)
        self.assertTrue(dialog.close_button.isVisible())
        dialog.close()

    def test_file_only_completion_never_claims_launch_readiness(self):
        dialog = CompletionDialog(self.root / 'Imported', self.parent, Mock(), file_only=True)
        self.assertIn('unverified', dialog.windowTitle())
        self.assertIn('not a launch-ready package', dialog.detail.toPlainText())
        self.assertNotIn('@../../playden.args', dialog.detail.toPlainText())
        self.assertIsNone(dialog.copy_button)
        dialog.close()

    def test_drive_selection_constructs_playden_import_destination(self):
        info = self.root.stat()
        identity = VolumeIdentity('fixture', 'exfat', info.st_dev, info.st_ino)
        with patch('minecraft_playden.gui_components.mounted_drives', return_value=[('USB fixture', self.root)]), patch('minecraft_playden.volumes._mounted_identity', return_value=identity):
            dialog = DriveDialog('Minecraft-26.3', self.parent)
            self.assertIn(str(self.root / 'playden/import/Minecraft-26.3'), dialog.destination.text())
            dialog.copy_button.click()
            self.assertEqual(dialog.target.root, self.root.resolve())
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertFalse((self.root / 'playden').exists())

    def test_no_drive_or_cancel_does_not_start_transfer(self):
        with patch('minecraft_playden.gui_components.mounted_drives', return_value=[]):
            dialog = DriveDialog('Minecraft', self.parent)
            self.assertFalse(dialog.copy_button.isEnabled())
            self.assertIn('No writable', dialog.destination.text())
            dialog.reject()
            self.assertIsNone(dialog.target)

    def test_manual_internal_volume_requires_explicit_confirmation(self):
        from PySide6.QtWidgets import QMessageBox
        storage = Mock()
        storage.rootPath.return_value = str(self.root)
        storage.displayName.return_value = 'Internal disk'
        storage.bytesAvailable.return_value = -1
        storage.bytesTotal.return_value = -1
        from PySide6.QtCore import QByteArray
        storage.fileSystemType.return_value = QByteArray(b'exfat')
        storage.isValid.return_value = True
        storage.isReady.return_value = True
        storage.isReadOnly.return_value = False
        with patch('minecraft_playden.gui_components.mounted_drives', return_value=[]), patch('minecraft_playden.gui_components.QFileDialog.getExistingDirectory', return_value=str(self.root)), patch('minecraft_playden.gui_components.QStorageInfo', return_value=storage), patch('minecraft_playden.gui_components.external_mounts', return_value=set()), patch('minecraft_playden.gui_components.QMessageBox.warning', return_value=QMessageBox.StandardButton.No) as warning:
            dialog = DriveDialog('Minecraft', self.parent)
            dialog.browse()
            self.assertFalse(dialog.copy_button.isEnabled())
            self.assertIn('INTERNAL/SYSTEM', warning.call_args.args[2])
            self.assertEqual(warning.call_args.args[4], QMessageBox.StandardButton.No)
            warning.return_value = QMessageBox.StandardButton.Yes
            dialog.browse()
            self.assertTrue(dialog.copy_button.isEnabled())
            self.assertIn('Manual:', dialog.drives.currentText())

    def test_refresh_replaces_old_manual_choice_without_fallback(self):
        with patch('minecraft_playden.gui_components.mounted_drives', side_effect=[[('USB', self.root)], []]) as detect:
            dialog = DriveDialog('Minecraft', self.parent)
            self.assertTrue(dialog.copy_button.isEnabled())
            dialog.refresh()
            self.assertFalse(dialog.copy_button.isEnabled())
            self.assertEqual(dialog.drives.count(), 0)
            self.assertEqual(detect.call_count, 2)

    def test_mounted_drives_uses_device_allowlist_and_friendly_details(self):
        from minecraft_playden.gui_components import mounted_drives
        from PySide6.QtCore import QByteArray
        def volume(root):
            storage = Mock()
            storage.rootPath.return_value = root
            storage.isValid.return_value = True
            storage.isReady.return_value = True
            storage.isReadOnly.return_value = False
            storage.displayName.return_value = 'External NVMe'
            storage.bytesAvailable.return_value = 123456789
            storage.bytesTotal.return_value = 1_000_000_000
            storage.fileSystemType.return_value = QByteArray(b'exfat')
            return storage
        with patch('minecraft_playden.gui_components.QStorageInfo.mountedVolumes', return_value=[volume('/'), volume('/home'), volume('/run/media/isaac/extnvme')]), patch('minecraft_playden.gui_components.external_mounts', return_value={'/run/media/isaac/extnvme'}):
            choices = mounted_drives()
            self.assertEqual(len(choices), 1)
            self.assertEqual(choices[0][1], Path('/run/media/isaac/extnvme'))
            for detail in ('External NVMe', 'exfat', '0.12 GB free', '1.00 GB total'):
                self.assertIn(detail, choices[0][0])

    def test_cancelled_worker_emits_cancelled_and_finished_not_result_or_error(self):
        token = CancellationToken()
        token.cancel()
        action = Mock()
        task = Task(action, token)
        results, errors, cancelled, finished = [], [], [], []
        task.result.connect(results.append)
        task.error.connect(errors.append)
        task.cancelled.connect(cancelled.append)
        task.finished.connect(lambda: finished.append(True))
        task.run()
        self.assertEqual(results, [])
        self.assertEqual(errors, [])
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(finished, [True])
        action.assert_not_called()
