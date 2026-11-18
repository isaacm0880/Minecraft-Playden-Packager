"""Qt-to-engine release regressions using deterministic local instances and HTTP payloads."""
from runtime_fixtures import install_runtime
import copy
import faulthandler
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from minecraft_playden.cli import main as cli
from minecraft_playden.desktop import check_package, LocalBuildRequest
from minecraft_playden.exporter import INSTRUCTIONS
from minecraft_playden.discovery import discover
from minecraft_playden.operations import OperationCancelled
from minecraft_playden.validator import validate
from test_fabric_launch import installed, archive, save

HAS_QT = importlib.util.find_spec('PySide6') is not None
if HAS_QT:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QSettings, QCoreApplication, QEvent, QTimer, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from minecraft_playden.gui_app import MainWindow


@unittest.skipUnless(HAS_QT, 'Install .[gui] to run Qt release tests')
class ReleaseGuiTests(unittest.TestCase):
    def test_bad_qt_binding_on_python311_is_rejected_before_gui_startup(self):
        from minecraft_playden.gui import launch
        with patch('PySide6.__version__', '6.12.0'), patch('sys.version_info', (3, 11, 0)), patch('sys.stderr', new_callable=io.StringIO) as error:
            self.assertEqual(launch(self.root / 'cache', []), 1)
        self.assertIn('PYSIDE-3474', error.getvalue())
        self.assertIn('pip install', error.getvalue())

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        confirmation = patch('minecraft_playden.gui_app.MainWindow.confirm_untested', return_value=True)
        confirmation.start()
        self.addCleanup(confirmation.stop)
        self.temp = tempfile.TemporaryDirectory()
        # Cleanup fixtures use a physical root: macOS /var and Windows
        # short temp names may alias another spelling. Explicit alias tests
        # separately cover the GUI/transfer boundary.
        self.root = Path(self.temp.name).resolve()
        settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        with patch('minecraft_playden.gui_app.QSettings', return_value=settings), patch('minecraft_playden.gui_app.QTimer.singleShot'):
            self.window = MainWindow(self.root / 'cache', [])
        batch_completion = patch.object(self.window, 'show_queue_completion')
        self.batch_completion = batch_completion.start()
        self.addCleanup(batch_completion.stop)
        self.warnings = patch('minecraft_playden.gui_app.QMessageBox.warning')
        self.warning = self.warnings.start()
        self.addCleanup(self.warnings.stop)
        completion = patch.object(self.window, 'show_completion')
        self.completion = completion.start()
        self.addCleanup(completion.stop)

    def wait(self):
        until = time.monotonic() + 10
        while self.window.busy() and time.monotonic() < until:
            self.app.processEvents()
            time.sleep(0.001)
        if self.window.busy():
            faulthandler.dump_traceback()
        self.assertFalse(self.window.busy(),
                         f'GUI worker did not finish: {self.window.operation}, {self.window.outcome}; '
                         f'{self.window.status.text()}; USB row {self.window.usb_current}; '
                         f'{[record.state for record in self.window.usb_records]}')

    def tearDown(self):
        self.window.cancellation.cancel()
        self.wait()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.temp.cleanup()

    def select(self, root):
        self.window.operation = 'scan'
        self.window.task_result(discover([root], include_defaults=False))
        self.window.table.selectRow(0)
        self.window.output.setText(str(self.root / 'gui output'))

    def network(self, metadata, extra=None):
        payloads = {
            'https://fixture/netty': archive({'io/netty/channel/unix/Errors.class': b'class'}),
            'https://fixture/fastutil': archive({'it/unimi/dsi/fastutil/floats/FloatList.class': b'class'}),
            'https://fixture/native': archive({'lwjgl.dll': b'MZnative'}),
            'https://fixture/index': b'{"objects":{}}',
            'https://fixture/base': json.dumps(metadata).encode(),
        }
        payloads.update(extra or {})
        def request(req, **kwargs):
            url = req.full_url
            if url.endswith('version_manifest_v2.json'):
                return io.BytesIO(json.dumps({'versions': [{'id': metadata['id'], 'url': 'https://fixture/base'}]}).encode())
            self.assertNotIn('client', url, 'A local build must not download Minecraft')
            return io.BytesIO(payloads[url])
        return patch('urllib.request.urlopen', side_effect=request)

    def runtime(self, metadata, downloader, java):
        return install_runtime(metadata, downloader, java)

    def test_gui_cli_byte_parity_for_vanilla_fabric_layouts_and_privacy_policies(self):
        for loader, layout in [('Vanilla', 'prism'), ('Vanilla', 'multimc'), ('Vanilla', 'official'),
                               ('Fabric', 'prism'), ('Fabric', 'multimc'), ('Fabric', 'official')]:
            for privacy in [False, True]:
                with self.subTest(loader=loader, layout=layout, privacy=privacy):
                    root = self.root / f'{loader}-{layout}-{privacy}'
                    instance, metadata, _ = installed(root / 'source', '26.3', '0.20.1', layout)
                    official = json.loads((Path(__file__).parent / 'fixtures/26.3.json').read_text())
                    metadata['arguments']['default-user-jvm'] = official['arguments']['default-user-jvm']
                    if layout == 'official':
                        (root / 'source/versions/26.3/26.3.json').write_text(json.dumps(metadata))
                    if loader == 'Vanilla' and layout != 'official':
                        (instance / 'mmc-pack.json').write_text(json.dumps({'components': [{'uid': 'net.minecraft', 'version': '26.3'}]}))
                    elif loader == 'Vanilla':
                        instance = root / 'source/versions/26.3'
                    source_before = {p.relative_to(root / 'source'): p.read_bytes() for p in (root / 'source').rglob('*') if p.is_file()}
                    self.select(instance)
                    output = root / 'gui output'
                    self.window.output.setText(str(output))
                    self.window.privacy_cleanup.setChecked(privacy)
                    self.assertEqual(self.window.loader.text(), 'Fabric 0.20.1' if loader == 'Fabric' else 'Vanilla')
                    self.assertEqual(self.window.version.text(), '26.3')
                    events = []
                    original_run = LocalBuildRequest.run
                    def run(request, progress, cancellation):
                        def report(event):
                            events.append(event.stage)
                            progress(event)
                        return original_run(request, report, cancellation)
                    with self.network(metadata), patch('minecraft_playden.runtime.install', side_effect=self.runtime), patch('minecraft_playden.gui_app.LocalBuildRequest.run', autospec=True, side_effect=run):
                        self.window.start_build()
                        self.assertFalse(self.window.privacy_cleanup.isEnabled())
                        self.window.start_build()  # A duplicate click must not queue another build.
                        self.wait()
                        self.assertEqual(self.window.outcome, 'succeeded', self.window.log.toPlainText())
                        cli_output = root / 'cli output'
                        argv = ['packager', '--cache', str(self.root / 'cache'), 'build', '--source', 'local', '--instance', str(instance), '--output', str(cli_output)]
                        with patch('sys.argv', argv):
                            self.assertEqual(cli(), 0)
                    left = {p.relative_to(output): p.read_bytes() for p in output.rglob('*') if p.is_file()}
                    right = {p.relative_to(cli_output): p.read_bytes() for p in cli_output.rglob('*') if p.is_file()}
                    self.assertEqual(left, right)
                    from minecraft_playden import arguments, recipe
                    words = arguments.parse_argfile((output / 'playden.args').read_text())
                    self.assertNotIn('-XX:+UseZGC', words)
                    for option in ['-Xms2G', '-Xmx4G', '-XX:+AlwaysPreTouch',
                                   '-XX:+UseCompactObjectHeaders', '-XX:+UseStringDeduplication']:
                        self.assertIn(option, words)
                    self.assertEqual(json.loads((output / 'recipe.json').read_text()), recipe.generate('Minecraft 26.3'))
                    self.assertEqual(validate(output), [])
                    self.assertEqual(check_package(output).kind, 'launch-package')
                    self.assertEqual((output / 'instances/default/minecraft/saves/World/level.dat').exists(), True)
                    self.assertEqual((output / 'instances/default/minecraft/custom-config/account/token').read_bytes(), b'custom-data')
                    self.assertTrue({'instance', 'hashing', 'validation', 'complete'}.issubset(events))
                    if loader == 'Fabric':
                        self.assertIn('loader', events)
                    self.assertEqual(source_before, {p.relative_to(root / 'source'): p.read_bytes() for p in (root / 'source').rglob('*') if p.is_file()})
                    if checker := os.environ.get('PLAYDEN_RECIPE_CHECK'):
                        result = subprocess.run([checker, str(output / 'recipe.json')], capture_output=True, text=True)
                        self.assertEqual(result.returncode, 0, result.stderr)

    def test_privacy_cleanup_in_real_fabric_build_precedes_hashing_and_validation(self):
        instance, metadata, _ = installed(self.root / 'source')
        game = instance / '.minecraft'
        save(game, 'config/private.json', b'{"access_token":"fixture-secret", "email":"private@example.test", "volume":7}')
        save(game, 'config/normal.json', b'{"volume":9}')
        save(game, 'usercache.json', b'[{"name":"PrivatePlayer"}]')
        save(game, 'servers.dat', b'private-server')
        before = {p.relative_to(instance): p.read_bytes() for p in instance.rglob('*') if p.is_file()}
        self.select(instance)
        self.assertTrue(self.window.privacy_cleanup.isChecked())
        with self.network(metadata), patch('minecraft_playden.runtime.install', side_effect=self.runtime):
            self.window.start_build()
            self.wait()
        output = self.root / 'gui output'
        self.assertEqual(self.window.outcome, 'succeeded', self.window.log.toPlainText())
        self.assertEqual(validate(output), [])
        packaged = output / 'instances/default/minecraft'
        self.assertEqual(json.loads((packaged / 'config/private.json').read_text()), {'volume': 7})
        self.assertEqual((packaged / 'config/normal.json').read_bytes(), b'{"volume":9}')
        self.assertFalse((packaged / 'usercache.json').exists())
        self.assertFalse((packaged / 'servers.dat').exists())
        self.assertTrue((packaged / 'saves/World/level.dat').is_file())
        self.assertEqual(before, {p.relative_to(instance): p.read_bytes() for p in instance.rglob('*') if p.is_file()})

    def test_real_queue_blocks_until_invalid_instance_deselected(self):
        from dataclasses import replace
        from minecraft_playden.discovery import DiscoveryResult
        instance, metadata, _ = installed(self.root / 'source')
        supported = discover([instance], include_defaults=False).installations[0]
        bad = replace(supported, name='Unsupported', packaging_problem='Unsupported fixture loader')
        good = replace(supported, name='Fabric Pack')
        self.window.operation = 'scan'
        self.window.task_result(DiscoveryResult((bad, good)))
        self.window.table.selectAll()
        self.window.output.setText(str(self.root / 'output/unused'))
        self.assertFalse(self.window.build_button.isEnabled())
        self.assertIn('Unsupported', self.window.build_reason.text())
        self.assertIn('Unsupported fixture loader', self.window.build_reason.text())
        self.window.start_build()
        self.assertFalse(self.window.busy())
        self.assertFalse((self.root / 'output').exists())
        self.window.table.selectRow(1)
        self.assertTrue(self.window.build_button.isEnabled())
        with self.network(metadata), patch('minecraft_playden.runtime.install', side_effect=self.runtime):
            self.window.start_build()
            self.wait()
        self.assertEqual(self.window.outcome, 'succeeded', self.window.log.toPlainText())
        self.assertEqual(validate(self.root / 'output/unused'), [])
        self.assertFalse((self.root / 'output/Unsupported').exists())

    def test_untested_fabric_versions_package_without_promoting_compatibility(self):
        from minecraft_playden.compatibility import Status, UNTESTED_WARNING
        for version, loader in [('1.20.1', '0.16.10'), ('26.2', '0.20.1'), ('26.3', '0.20.1')]:
            with self.subTest(version=version):
                instance, metadata, _ = installed(self.root / version / 'source', version, loader)
                self.select(instance)
                self.window.cache.setText(str(self.root / version / 'cache'))
                installation = self.window.selected_installation()
                expected = Status.TESTED if version == '26.3' else Status.UNTESTED
                self.assertEqual(installation.compatibility.status, expected)
                self.assertTrue(self.window.build_button.isEnabled(), self.window.build_reason.text())
                if expected == Status.UNTESTED:
                    self.assertIn(UNTESTED_WARNING, self.window.build_reason.text())
                for attempt in range(2):
                    output = self.root / version / f'output-{attempt}'
                    self.window.output.setText(str(output))
                    with self.network(metadata), patch('minecraft_playden.runtime.install', side_effect=self.runtime):
                        self.window.start_build()
                        self.wait()
                        if attempt == 0:
                            cli_output = self.root / version / 'cli-output'
                            stdout = io.StringIO()
                            argv = ['packager', '--cache', str(self.root / version / 'cache'), 'build',
                                    '--source', 'local', '--instance', str(instance), '--output', str(cli_output)]
                            with patch('sys.argv', argv), contextlib.redirect_stdout(stdout):
                                self.assertEqual(cli(), 0)
                            self.assertEqual(validate(cli_output), [])
                            self.assertTrue(stdout.getvalue().endswith(INSTRUCTIONS + '\n'))
                            if expected == Status.UNTESTED:
                                self.assertIn(UNTESTED_WARNING, stdout.getvalue())
                            else:
                                self.assertNotIn(UNTESTED_WARNING, stdout.getvalue())
                    self.assertEqual(self.window.outcome, 'succeeded', self.window.log.toPlainText())
                    self.assertEqual(validate(output), [])
                    self.assertEqual(installation.compatibility.status, expected)
                    self.assertEqual(self.window.selected_installation().compatibility.status, expected)
                    self.assertEqual((output / 'PLAYDEN.txt').read_text().split('\n\n', 1)[1],
                                     INSTRUCTIONS)

    def test_three_real_builds_from_ctrl_and_shift_selection_create_distinct_packages(self):
        from minecraft_playden.discovery import DiscoveryResult
        instances = []
        for index in range(3):
            instance, metadata, _ = installed(self.root / f'source-{index}', '26.3', '0.20.1')
            (instance / '.minecraft/saves/World/level.dat').write_bytes(f'world-{index}'.encode())
            instances.append(discover([instance], include_defaults=False).installations[0])
        self.window.operation = 'scan'
        self.window.task_result(DiscoveryResult(tuple(instances)))
        self.window.resize(1280, 900)
        self.window.show()
        self.app.processEvents()
        original = LocalBuildRequest.run
        executed, stages = [], []
        def run(request, progress, cancellation):
            executed.append(request)
            def report(event):
                stages.append((request.output, event.stage))
                progress(event)
            return original(request, report, cancellation)
        outputs = []
        multi_modifier = Qt.KeyboardModifier.MetaModifier if sys.platform == 'darwin' else Qt.KeyboardModifier.ControlModifier
        for modifier in (multi_modifier, Qt.KeyboardModifier.ShiftModifier):
            self.window.table.clearSelection()
            for row in range(3):
                point = self.window.table.visualItemRect(self.window.table.item(row, 0)).center()
                QTest.mouseClick(self.window.table.viewport(), Qt.MouseButton.LeftButton,
                                 modifier if row else Qt.KeyboardModifier.NoModifier, point)
            QTest.keyRelease(self.window.table.viewport(), Qt.Key.Key_Control)
            QTest.keyRelease(self.window.table.viewport(), Qt.Key.Key_Meta)
            QTest.keyRelease(self.window.table.viewport(), Qt.Key.Key_Shift)
            selected = self.window.selected_installations()
            self.assertEqual(len(selected), 3)
            self.window.output.setText(str(self.root / 'outputs/unused'))
            with self.network(metadata), patch('minecraft_playden.runtime.install', side_effect=self.runtime), patch('minecraft_playden.gui_app.LocalBuildRequest.run', autospec=True, side_effect=run):
                self.window.build_button.click()
                self.wait()
            latest = executed[-3:]
            self.assertEqual([r.installation for r in latest], selected)
            for request in latest:
                self.assertEqual(validate(request.output), [])
                self.assertTrue((request.output / 'package.json').is_file())
                self.assertEqual((request.output / 'instances/default/minecraft/saves/World/level.dat').read_bytes(),
                                 (request.installation.instance / '.minecraft/saves/World/level.dat').read_bytes())
                self.assertIn((request.output, 'complete'), stages)
                outputs.append(request.output)
            self.assertEqual(len(self.window.build_records), 3)
            self.assertTrue(all(record.state == 'completed' for record in self.window.build_records))
            self.assertIn('3 succeeded, 0 failed', self.window.completed_label.text())
            for request in latest:
                self.assertIn(str(request.output), self.window.completed_label.text())
            self.assertEqual(self.window.output_view, self.root / 'outputs')
        self.assertEqual(len(set(outputs)), 6)
        self.assertEqual(len(list((self.root / 'outputs').glob('*/package.json'))), 6)

    def simulated_usb(self, root):
        from minecraft_playden.volumes import DriveTarget, VolumeIdentity
        root.mkdir(parents=True)
        info = root.stat()
        identity = VolumeIdentity('fixture-usb', 'exfat', info.st_dev, info.st_ino)
        from contextlib import ExitStack
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('minecraft_playden.volumes._mounted_identity', return_value=identity))
        stack.enter_context(patch('minecraft_playden.transfer.flush_volume', side_effect=lambda drive: drive.check()))
        stack.enter_context(patch('minecraft_playden.transfer.allocation_unit', return_value=4096))
        # This fixture models a USB drive using ordinary temporary files. Keep
        # real copies, validation, hashing and atomic/no-replace publication, but
        # simulate durability too: per-file fsync and Windows WRITE_THROUGH can
        # otherwise spend the GUI deadline flushing the runner's physical disk.
        stack.enter_context(patch('minecraft_playden.transfer.flush_file'))
        stack.enter_context(patch('minecraft_playden.transfer.flush_directory'))
        if os.name == 'nt':
            from minecraft_playden.volumes import _move_windows
            stack.enter_context(patch('minecraft_playden.volumes._move_windows',
                                      side_effect=lambda source, target, flags:
                                      _move_windows(source, target, flags & ~0x8)))
        return stack, DriveTarget.capture(root)

    def test_real_verified_usb_cleanup_opt_in_confirmation_and_skipped_verification(self):
        from minecraft_playden.transfer import TransferRequest
        for option, verified, confirmed in [(False, True, True), (True, True, False),
                                             (True, False, True), (True, True, True)]:
            with self.subTest(option=option, verified=verified, confirmed=confirmed):
                root = self.root / f'{option}-{verified}-{confirmed}'
                instance, metadata, _ = installed(root / 'source', '26.3', '0.20.1')
                before = {p.relative_to(root / 'source'): p.read_bytes() for p in (root / 'source').rglob('*') if p.is_file()}
                self.select(instance)
                package = root / 'Package'
                self.window.output.setText(str(package))
                with self.network(metadata), patch('minecraft_playden.runtime.install', side_effect=self.runtime):
                    self.window.start_build()
                    self.wait()
                self.assertEqual(validate(package), [], self.window.log.toPlainText())
                self.assertIsNotNone(self.window.completed_package.generated)
                with patch('minecraft_playden.gui_app.QDesktopServices.openUrl', return_value=True) as opened:
                    self.window.open_output_button.click()
                    self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), package.resolve())
                self.window.cleanup_after_usb.setChecked(option)
                stack, drive = self.simulated_usb(root / 'USB')
                request = TransferRequest(package, drive, verify_after_copy=verified)
                self.completion.reset_mock()
                with stack, patch.object(self.window, 'confirm_cleanup', return_value=confirmed) as confirm:
                    self.window.start_transfer(request)
                    self.wait()
                removed = option and verified and confirmed
                self.assertEqual(package.exists(), not removed, self.window.log.toPlainText())
                self.assertEqual(validate(request.destination), [])
                self.assertEqual(before, {p.relative_to(root / 'source'): p.read_bytes() for p in (root / 'source').rglob('*') if p.is_file()})
                if option and verified:
                    confirm.assert_called_once_with(package.resolve(), request.destination)
                else:
                    confirm.assert_not_called()
                self.completion.assert_called_once_with(request.destination, True)
                if removed:
                    self.assertNotIn(package, self.window.generated_outputs)
                    self.assertTrue(self.window.build_records[0].removed)
                    self.assertIn('removed after verified USB', self.window.queue_table.item(0, 1).text())
                    self.assertFalse(self.window.open_output_button.isEnabled())
                    self.assertFalse(self.window.copy_usb_button.isEnabled())

    def test_partial_multi_package_usb_transfer_cleans_only_verified_success(self):
        from minecraft_playden.discovery import DiscoveryResult
        from minecraft_playden.transfer import TransferRequest, TransferError
        instances = []
        for index in range(2):
            instance, metadata, _ = installed(self.root / f'source-{index}', '26.3', '0.20.1')
            instances.append(discover([instance], include_defaults=False).installations[0])
        self.window.operation = 'scan'
        self.window.task_result(DiscoveryResult(tuple(instances)))
        self.window.table.selectAll()
        parent = self.root / 'outputs'
        self.window.output.setText(str(parent / 'unused'))
        with self.network(metadata), patch('minecraft_playden.runtime.install', side_effect=self.runtime):
            self.window.start_build()
            self.wait()
        first, second = [record.request.output for record in self.window.build_records]
        self.window.cleanup_after_usb.setChecked(True)
        stack, drive = self.simulated_usb(self.root / 'USB')
        with stack, patch.object(self.window, 'confirm_cleanup', return_value=True) as confirm:
            self.window.start_transfer(TransferRequest(first, drive))
            self.wait()
            self.assertFalse(first.exists())
            self.assertTrue(second.exists())
            with patch('minecraft_playden.gui_app.TransferRequest.run', side_effect=TransferError('Disconnected')):
                self.window.start_transfer(TransferRequest(second, drive))
                self.wait()
            confirm.assert_called_once()
        self.assertTrue(second.exists())
        self.assertEqual(validate(drive.root / 'playden/import' / first.name), [])
        self.assertTrue(self.window.build_records[0].removed)
        self.assertFalse(self.window.build_records[1].removed)
        self.assertEqual(self.window.output_view, parent)
        with patch('minecraft_playden.gui_app.QDesktopServices.openUrl', return_value=True) as opened:
            self.window.open_output_button.click()
            self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), parent.resolve())

    def test_cancelled_copy_of_known_output_never_offers_cleanup(self):
        from minecraft_playden.transfer import TransferRequest
        instance, metadata, _ = installed(self.root / 'source', '26.3', '0.20.1')
        self.select(instance)
        with self.network(metadata), patch('minecraft_playden.runtime.install', side_effect=self.runtime):
            self.window.start_build()
            self.wait()
        package = self.root / 'gui output'
        self.assertIn(package, self.window.generated_outputs)
        self.window.cleanup_after_usb.setChecked(True)
        stack, drive = self.simulated_usb(self.root / 'USB')
        entered = threading.Event()
        def waiting(request, progress, cancellation):
            entered.set()
            cancellation.wait(5)
        with stack, patch('minecraft_playden.gui_app.TransferRequest.run', autospec=True, side_effect=waiting), patch.object(self.window, 'confirm_cleanup') as confirm:
            self.window.start_transfer(TransferRequest(package, drive))
            self.assertTrue(entered.wait(1))
            self.window.cancel_operation()
            self.wait()
            confirm.assert_not_called()
        self.assertTrue(package.exists())
        self.assertEqual(validate(package), [])
        self.assertEqual(self.window.outcome, 'cancelled')

    def build_real_usb_queue(self, count=3, prefix='usb', fail_indices=()):
        from minecraft_playden.discovery import DiscoveryResult
        found = []
        for index in range(count):
            instance, metadata, _ = installed(self.root / f'{prefix}-source-{index}', '26.3', '0.20.1')
            (instance / '.minecraft/saves/World/level.dat').write_bytes(f'{prefix}-world-{index}'.encode())
            found.append(discover([instance], include_defaults=False).installations[0])
        self.window.operation = 'scan'
        self.window.task_result(DiscoveryResult(tuple(found)))
        self.window.table.selectAll()
        self.window.output.setText(str(self.root / f'{prefix}-outputs/unused'))
        original = LocalBuildRequest.run
        def execute(request, progress, cancellation):
            if found.index(request.installation) in fail_indices:
                raise ValueError('Fixture build failed before output publication')
            return original(request, progress, cancellation)
        with self.network(metadata), patch('minecraft_playden.runtime.install', side_effect=self.runtime), patch('minecraft_playden.gui_app.LocalBuildRequest.run', autospec=True, side_effect=execute):
            self.window.start_build()
            self.wait()
        outputs = [record.request.output for record in self.window.build_records]
        self.assertEqual(len(outputs), count)
        for record in self.window.build_records:
            if record.state == 'completed':
                self.assertEqual(validate(record.request.output), [])
            else:
                self.assertFalse(record.request.output.exists())
        return outputs

    @contextlib.contextmanager
    def usb_picker(self, drive, *, skip=False):
        from PySide6.QtWidgets import QDialog
        with patch('minecraft_playden.gui_app.DriveDialog') as picker:
            picker.return_value.exec.return_value = QDialog.DialogCode.Accepted
            picker.return_value.target = drive
            picker.return_value.skip_verification.isChecked.return_value = skip
            yield picker

    def test_popup_and_main_copy_the_full_three_package_queue_with_real_verification(self):
        from minecraft_playden.transfer import TransferRequest
        outputs = self.build_real_usb_queue()
        # Selecting one row must not narrow either Copy to USB entry point.
        self.window.queue_table.setCurrentCell(0, 1)
        stack, drive = self.simulated_usb(self.root / 'USB')
        existing = drive.root / 'playden/import' / outputs[0].name
        existing.mkdir(parents=True)
        (existing / 'unrelated').write_bytes(b'keep USB data')
        original = TransferRequest.run
        executed, stages = [], []
        def run(request, progress, cancellation):
            executed.append(request)
            def report(event):
                stages.append((request.source, event.stage))
                progress(event)
            return original(request, report, cancellation)
        destinations = []
        with stack, self.usb_picker(drive) as picker, patch('minecraft_playden.gui_app.TransferRequest.run', autospec=True, side_effect=run), patch.object(self.window, 'show_usb_completion') as summary:
            MainWindow.show_queue_completion(self.window)
            dialog = self.window.queue_dialog
            self.assertIsNotNone(dialog.copy_button)
            self.assertTrue(dialog.copy_button.isEnabled())
            dialog.copy_button.click()
            self.wait()
            self.assertEqual([request.source for request in executed], outputs)
            self.assertTrue(all(record.state == 'verified' for record in self.window.usb_records))
            self.assertIn('3 verified', self.window.usb_aggregate.text())
            self.assertIn('3 verified', self.window.status.text())
            summary.assert_called_once()
            destinations.extend(record.result.destination for record in self.window.usb_records)
            self.assertTrue(self.window.copy_usb_button.isEnabled())
            self.window.copy_usb_button.click()
            self.wait()
            self.assertEqual([request.source for request in executed[3:]], outputs)
            self.assertEqual(summary.call_count, 2)
            destinations.extend(record.result.destination for record in self.window.usb_records)
            self.assertEqual(picker.call_args.kwargs['package_names'], [path.name for path in outputs])
        self.assertEqual(len(set(destinations)), 6)
        self.assertEqual(len(list((drive.root / 'playden/import').glob('*/package.json'))), 6)
        for request in executed:
            self.assertEqual(validate(request.destination), [])
            self.assertEqual((request.destination / 'instances/default/minecraft/saves/World/level.dat').read_bytes(),
                             (request.source / 'instances/default/minecraft/saves/World/level.dat').read_bytes())
            self.assertIn((request.source, 'transfer-verify'), stages)
        self.assertEqual((existing / 'unrelated').read_bytes(), b'keep USB data')
        self.assertTrue(all(path.exists() for path in outputs))

    def test_queue_partial_failure_retries_only_failed_package_and_preserves_cleanup_receipts(self):
        from minecraft_playden.transfer import TransferRequest, TransferError
        outputs = self.build_real_usb_queue()
        self.window.cleanup_after_usb.setChecked(True)
        stack, drive = self.simulated_usb(self.root / 'USB')
        original = TransferRequest.run
        executed = []
        def partial(request, progress, cancellation):
            executed.append(request.source)
            if request.source == outputs[1]:
                raise TransferError('Fixture USB failure')
            return original(request, progress, cancellation)
        with stack, self.usb_picker(drive), patch.object(self.window, 'confirm_cleanup', return_value=True) as confirm, patch.object(self.window, 'show_usb_completion'):
            with patch('minecraft_playden.gui_app.TransferRequest.run', autospec=True, side_effect=partial):
                self.window.copy_to_usb()
                self.wait()
            self.assertEqual(executed, outputs)
            self.assertEqual([record.state for record in self.window.usb_records], ['verified', 'failed', 'verified'])
            self.assertEqual([path.exists() for path in outputs], [False, True, False])
            self.assertEqual(confirm.call_count, 2)
            self.assertTrue(self.window.copy_usb_button.isEnabled())
            self.assertTrue(self.window.retry_usb_button.isEnabled())
            self.assertEqual(self.window.progress_bar.text(), '0%')
            copied_destinations = [self.window.usb_records[i].result.destination for i in (0, 2)]
            retried = []
            def retry(request, progress, cancellation):
                retried.append(request.source)
                return original(request, progress, cancellation)
            with patch('minecraft_playden.gui_app.TransferRequest.run', autospec=True, side_effect=retry):
                self.window.retry_usb_button.click()
                self.wait()
            self.assertEqual(retried, [outputs[1]])
            self.assertTrue(all(record.state == 'verified' for record in self.window.usb_records))
            self.assertEqual(confirm.call_count, 3)
            self.assertFalse(any(path.exists() for path in outputs))
            self.assertFalse(self.window.copy_usb_button.isEnabled())
            self.assertFalse(self.window.retry_usb_button.isEnabled())
            for destination in copied_destinations:
                self.assertEqual(validate(destination), [])

    def test_usb_queue_cancellation_stops_pending_packages_and_retry_keeps_successes(self):
        from minecraft_playden.transfer import TransferRequest
        outputs = self.build_real_usb_queue()
        self.window.cleanup_after_usb.setChecked(True)
        stack, drive = self.simulated_usb(self.root / 'USB')
        original = TransferRequest.run
        entered = threading.Event()
        executed = []
        def interrupted(request, progress, cancellation):
            executed.append(request.source)
            if request.source == outputs[1]:
                entered.set()
                cancellation.wait(5)
            return original(request, progress, cancellation)
        with stack, self.usb_picker(drive), patch.object(self.window, 'confirm_cleanup', return_value=True) as confirm, patch.object(self.window, 'show_usb_completion'):
            with patch('minecraft_playden.gui_app.TransferRequest.run', autospec=True, side_effect=interrupted):
                self.window.copy_to_usb()
                deadline = time.monotonic() + 5
                while not entered.is_set() and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.001)
                self.assertTrue(entered.is_set())
                self.assertIn('1/3 finished', self.window.usb_aggregate.text())
                self.assertFalse(self.window.copy_usb_button.isEnabled())
                self.window.cancel_operation()
                self.wait()
            self.assertEqual(executed, outputs[:2])
            self.assertEqual([record.state for record in self.window.usb_records], ['verified', 'cancelled', 'not started'])
            self.assertEqual([path.exists() for path in outputs], [False, True, True])
            confirm.assert_called_once()
            retried = []
            def retry(request, progress, cancellation):
                retried.append(request.source)
                return original(request, progress, cancellation)
            with patch('minecraft_playden.gui_app.TransferRequest.run', autospec=True, side_effect=retry):
                self.window.retry_transfer()
                self.wait()
            self.assertEqual(retried, outputs[1:])
            self.assertTrue(all(record.state == 'verified' for record in self.window.usb_records))
            self.assertFalse(self.window.copy_usb_button.isEnabled())
            self.assertEqual(confirm.call_count, 3)

    def test_skipped_checksum_queue_keeps_all_local_outputs_and_mandatory_validation(self):
        from minecraft_playden.validator import validate as real_validate
        from minecraft_playden.volumes import _move_windows
        outputs = self.build_real_usb_queue()
        before = [{path.relative_to(output): path.read_bytes() for path in output.rglob('*') if path.is_file()}
                  for output in outputs]
        self.window.cleanup_after_usb.setChecked(True)
        def move_without_durability(source, target, flags):
            self.assertFalse(flags & 0x8, 'Simulated USB must not request host write-through')
            return _move_windows(source, target, flags)
        # A GUI regression should not depend on the host disk's flush latency.
        # Fail if the simulated drive accidentally reaches physical durability
        # APIs, while still exercising the native rename/no-overwrite behavior.
        with patch('minecraft_playden.volumes.os.fsync', side_effect=AssertionError('Unexpected host fsync')), \
                patch('minecraft_playden.volumes._move_windows', side_effect=move_without_durability):
            stack, drive = self.simulated_usb(self.root / 'USB')
            with stack, self.usb_picker(drive, skip=True), patch.object(self.window, 'confirm_cleanup') as confirm, patch.object(self.window, 'show_usb_completion'), patch('minecraft_playden.transfer.validate', wraps=real_validate) as validated:
                self.window.copy_to_usb()
                self.wait()
                self.assertEqual(validated.call_count, 6)
                confirm.assert_not_called()
        self.assertTrue(all(record.state == 'unverified' for record in self.window.usb_records))
        self.assertTrue(all(record.result is not None and not record.result.checksums for record in self.window.usb_records))
        self.assertTrue(all(path.exists() for path in outputs))
        for output, expected, record in zip(outputs, before, self.window.usb_records):
            self.assertEqual({path.relative_to(output): path.read_bytes() for path in output.rglob('*') if path.is_file()}, expected)
            destination = record.result.destination
            self.assertEqual({path.relative_to(destination): path.read_bytes() for path in destination.rglob('*') if path.is_file()}, expected)
        self.assertIsNone(self.window.task)
        self.assertIn('3 unverified', self.window.status.text())
        self.assertTrue(self.window.copy_usb_button.isEnabled())

    def test_skipped_checksum_queue_rejects_corrupt_copy_and_keeps_local_outputs(self):
        from minecraft_playden.validator import validate as real_validate
        outputs = self.build_real_usb_queue()
        self.window.cleanup_after_usb.setChecked(True)
        stack, drive = self.simulated_usb(self.root / 'USB')
        def validate_copy(path, **kwargs):
            if path not in outputs:
                # Corrupt a copied file without changing its size, immediately
                # before mandatory package validation. Skipping the optional
                # full copy-checksum pass must not allow publication or cleanup.
                args = path / 'playden.args'
                args.write_bytes(b'x' * args.stat().st_size)
            return real_validate(path, **kwargs)
        with stack, self.usb_picker(drive, skip=True), patch.object(self.window, 'confirm_cleanup') as confirm, patch.object(self.window, 'show_usb_completion'), patch('minecraft_playden.transfer.validate', side_effect=validate_copy) as validated:
            self.window.copy_to_usb()
            self.wait()
            self.assertEqual(validated.call_count, 6)
            confirm.assert_not_called()
        self.assertEqual([record.state for record in self.window.usb_records], ['failed'] * 3)
        self.assertTrue(all(record.result is None for record in self.window.usb_records))
        self.assertTrue(all('Transferred package validation failed' in record.detail for record in self.window.usb_records))
        self.assertTrue(all(path.exists() and not validate(path) for path in outputs))
        self.assertTrue(all(not record.request.destination.exists() for record in self.window.usb_records))
        self.assertTrue(self.window.retry_usb_button.isEnabled())

    def test_single_build_copy_retains_single_completion_behavior(self):
        outputs = self.build_real_usb_queue(1)
        stack, drive = self.simulated_usb(self.root / 'USB')
        self.completion.reset_mock()
        with stack, self.usb_picker(drive), patch.object(self.window, 'show_usb_completion') as batch_summary:
            self.window.copy_usb_button.click()
            self.wait()
            batch_summary.assert_not_called()
        self.assertEqual(len(self.window.usb_records), 1)
        self.assertEqual(self.window.usb_records[0].state, 'verified')
        self.completion.assert_called_once_with(drive.root / 'playden/import' / outputs[0].name, True)

    def test_usb_catalog_reports_failed_builds_and_uses_only_the_latest_queue(self):
        outputs = self.build_real_usb_queue(fail_indices={1})
        stack, drive = self.simulated_usb(self.root / 'USB')
        with stack, self.usb_picker(drive), patch.object(self.window, 'show_usb_completion'):
            self.window.copy_to_usb()
            self.wait()
            self.assertEqual([record.state for record in self.window.usb_records], ['verified', 'skipped', 'verified'])
            self.assertIn('Build failed', self.window.usb_records[1].detail)
            self.assertIn('Fixture build failed', self.window.usb_table.item(1, 1).text())
            self.assertFalse(outputs[1].exists())
            self.assertTrue((drive.root / 'playden/import' / outputs[0].name).exists())
            self.build_real_usb_queue(1, prefix='latest')
            self.assertEqual(self.window.usb_records, [])
            self.assertFalse(self.window.retry_usb_button.isEnabled())
            self.assertEqual(len(self.window.usb_candidates()), 1)
            self.window.copy_to_usb()
            self.wait()
            self.assertEqual(len(self.window.usb_records), 1)
            self.assertIn('latest-outputs', str(self.window.usb_records[0].candidate.source))
            self.build_real_usb_queue(2, prefix='all-failed', fail_indices={0, 1})
            self.assertFalse(self.window.copy_usb_button.isEnabled())
            MainWindow.show_queue_completion(self.window)
            self.assertIsNone(self.window.queue_dialog.copy_button)
            self.window.queue_dialog.close()
            with patch('minecraft_playden.gui_app.DriveDialog') as picker:
                self.window.copy_to_usb()
                picker.assert_not_called()

    def test_usb_batch_storage_failure_keeps_live_recheck_and_retries_failed_subset(self):
        from minecraft_playden.transfer import TransferRequest, InsufficientSpace
        outputs = self.build_real_usb_queue(2)
        stack, drive = self.simulated_usb(self.root / 'USB')
        original = TransferRequest.run
        def constrained(request, progress, cancellation):
            if request.source == outputs[0]:
                raise InsufficientSpace(100)
            return original(request, progress, cancellation)
        with stack, self.usb_picker(drive), patch.object(self.window, 'show_usb_completion'):
            with patch('minecraft_playden.gui_app.TransferRequest.run', autospec=True, side_effect=constrained):
                self.window.copy_to_usb()
                self.wait()
            self.assertEqual([record.state for record in self.window.usb_records], ['failed', 'verified'])
            self.window.retry_transfer()
            self.assertIsNotNone(self.window.space_dialog)
            self.window.space_dialog.check_button.click()
            self.assertTrue(self.window.space_dialog.retry_button.isEnabled())
            self.window.space_dialog.retry_button.click()
            self.wait()
            self.assertTrue(all(record.state == 'verified' for record in self.window.usb_records))
            self.assertEqual(len(list((drive.root / 'playden/import').glob('*/package.json'))), 2)

    def test_vanilla_reference_profile_and_offline_name_match_cli(self):
        from minecraft_playden import profiles
        root = self.root
        instance, metadata, _ = installed(root / 'source', '26.3', '0.20.1')
        (instance / 'mmc-pack.json').write_text(json.dumps({'components': [{'uid': 'net.minecraft', 'version': '26.3'}]}))
        metadata['libraries'].append({'name':'org.lwjgl:lwjgl:3.4.3','downloads':{'artifact':{
            'path':'org/lwjgl/lwjgl/3.4.3/lwjgl-3.4.3.jar','url':'https://fixture/core'}}})
        extra={'https://fixture/core':archive({'org/lwjgl/system/MemoryUtil.class':b'class'})}
        supplements=copy.deepcopy(profiles.SPNG_LIBRARIES)
        for supplement, entries in zip(supplements,[{'org/lwjgl/util/spng/SPNG.class':b'class'},{'lwjgl_spng.dll':b'MZspng'}]):
            spec=supplement['downloads']['artifact'];payload=archive(entries);spec['url']='https://fixture/'+Path(spec['path']).name
            spec.update(size=len(payload),sha1=hashlib.sha1(payload).hexdigest());extra[spec['url']]=payload
        self.select(instance)
        self.assertFalse(hasattr(self.window, 'launch_profile'))
        with self.network(metadata,extra),patch('minecraft_playden.profiles.SPNG_LIBRARIES',supplements),patch('minecraft_playden.runtime.install',side_effect=self.runtime):
            installation = self.window.selected_installation()
            LocalBuildRequest(installation, root/'gui output', root/'cache', exclude_saves=True,
                              launch_profile='reference-26.3', username='Preview Player', privacy_cleanup=False).run()
            other=root/'cli reference'
            with patch('sys.argv',['packager','--cache',str(root/'cache'),'build','--source','local','--instance',str(instance),'--output',str(other),
                                   '--launch-profile','reference-26.3','--username','Preview Player','--exclude-saves']):self.assertEqual(cli(),0)
            output=root/'gui output'
            self.assertEqual(validate(output),[])
            self.assertEqual({p.relative_to(output):p.read_bytes() for p in output.rglob('*') if p.is_file()},
                             {p.relative_to(other):p.read_bytes() for p in other.rglob('*') if p.is_file()})
            self.assertFalse((output/'instances/test/minecraft/saves').exists())
            if checker:=os.environ.get('PLAYDEN_RECIPE_CHECK'):
                self.assertEqual(subprocess.run([checker,str(output/'recipe.json')],capture_output=True).returncode,0)

    def test_reference_profile_blocks_fabric_and_copyable_error_log(self):
        instance,_,_=installed(self.root/'source','26.3','0.20.1');self.select(instance)
        self.assertFalse(hasattr(self.window, 'launch_profile'))
        self.assertTrue(self.window.build_button.isEnabled())
        self.window.log.setPlainText('Missing local library. Finish installing the instance and rescan.')
        self.window.copy_log_button.click()
        self.assertEqual(QApplication.clipboard().text(),self.window.log.toPlainText())

    def test_standalone_validation_is_cancellable_without_changing_prior_result(self):
        entered=threading.Event()
        def checking(path,progress,cancellation):
            entered.set();cancellation.wait(5)
        with patch('minecraft_playden.gui_app.check_package',side_effect=checking),patch('minecraft_playden.gui_app.QFileDialog.getExistingDirectory',return_value=str(self.root)):
            self.window.validate_existing();self.assertTrue(entered.wait(1))
            self.assertTrue(self.window.cancel_button.isEnabled());self.window.cancel_button.click();self.wait()
        self.assertEqual(self.window.outcome,'cancelled')
        self.assertIsNone(self.window.completed_package)
        self.completion.assert_not_called()

    def test_unsupported_loaders_file_import_is_explicit_and_matches_cli(self):
        for uid, name in [('net.minecraftforge', 'Forge'), ('net.neoforged.neoforge', 'NeoForge'), ('org.quiltmc.quilt-loader', 'Quilt')]:
            for exclude in [False, True]:
                with self.subTest(loader=name, exclude=exclude):
                    root = self.root / f'{name}-{exclude}'
                    instance, _, _ = installed(root / 'source')
                    path = instance / 'mmc-pack.json'
                    meta = json.loads(path.read_text());meta['components'][1]['uid'] = uid
                    path.write_text(json.dumps(meta))
                    self.select(instance)
                    self.assertIn(name, self.window.loader.text())
                    expected_status = 'Broken' if name == 'Forge' else 'Cannot package'
                    self.assertEqual(self.window.selected_installation().compatibility.status.value, expected_status)
                    self.assertTrue(self.window.table.item(0, 6).text() == ('Broken' if name == 'Forge' else 'Untested'))
                    self.assertFalse(self.window.build_button.isEnabled())
                    self.assertFalse(hasattr(self.window, 'file_only'))
                    output = root / 'imported'
                    request = LocalBuildRequest(self.window.selected_installation(), output, self.root / 'cache',
                                                exclude_saves=exclude, file_only=True)
                    self.assertEqual(request.run().kind, 'instance-import')
                    self.assertFalse((output / 'recipe.json').exists())
                    self.assertFalse((output / 'playden.args').exists())
                    other = root / 'cli import'
                    argv = ['packager', 'import-instance', '--instance', str(instance), '--output', str(other)]
                    if exclude:argv.append('--exclude-saves')
                    with patch('sys.argv', argv):self.assertEqual(cli(), 0)
                    self.assertEqual({p.relative_to(output):p.read_bytes() for p in output.rglob('*') if p.is_file()},
                                     {p.relative_to(other):p.read_bytes() for p in other.rglob('*') if p.is_file()})
                    self.assertEqual(validate(output), [])

    def test_missing_loader_dependency_blocks_selection_before_network(self):
        from minecraft_playden.compatibility import Status
        instance, _, _ = installed(self.root / 'source')
        dependency = next((self.root / 'source/libraries/org/ow2/asm').rglob('*.jar'))
        dependency.unlink()
        with patch('urllib.request.urlopen') as network:
            self.select(instance)
            self.assertEqual(self.window.selected_installation().compatibility.status, Status.CANNOT_PACKAGE)
            self.assertFalse(self.window.build_button.isEnabled())
            self.assertIn('library missing', self.window.build_reason.text())
            self.assertIn('launcher', self.window.build_reason.text())
            self.window.start_build()
            self.assertFalse(self.window.busy())
            network.assert_not_called()
        self.assertFalse((self.root / 'gui output').exists())
        self.completion.assert_not_called()

    def test_validate_existing_is_background_read_only_and_file_only_aware(self):
        from minecraft_playden.instance_content import import_instance
        instance, _, _ = installed(self.root / 'source')
        output = self.root / 'import';import_instance(instance, output)
        before = {p.relative_to(output): p.read_bytes() for p in output.rglob('*') if p.is_file()}
        with patch('minecraft_playden.gui_app.QFileDialog.getExistingDirectory', return_value=str(output)):
            self.window.validate_button.click();self.wait()
        self.assertEqual(self.window.outcome, 'succeeded')
        self.assertIn('unverified', self.window.status.text())
        self.assertFalse(self.window.copy_usb_button.isEnabled())
        self.assertEqual(before, {p.relative_to(output):p.read_bytes() for p in output.rglob('*') if p.is_file()})
        (output / 'IMPORT.txt').write_text('corrupted')
        with patch('minecraft_playden.gui_app.QFileDialog.getExistingDirectory', return_value=str(output)):
            self.window.validate_existing();self.wait()
        self.assertEqual(self.window.outcome, 'failed')
        self.assertIn('Hash mismatch', self.window.log.toPlainText())

    def test_real_import_cancellation_cleans_staging_and_keeps_gui_responsive(self):
        instance, metadata, _ = installed(self.root / 'source')
        self.select(instance)
        entered = threading.Event()
        from minecraft_playden.instance_content import copy_into
        def copying(content, root, game_directory, exclude_saves, cancellation):
            entered.set()
            cancellation.wait(5)
            return copy_into(content, root, game_directory, exclude_saves, cancellation)
        with self.network(metadata), patch('minecraft_playden.runtime.install', side_effect=self.runtime), patch('minecraft_playden.sources.copy_into', side_effect=copying):
            self.window.start_build();self.assertTrue(entered.wait(1))
            ticks=[];QTimer.singleShot(0, lambda: ticks.append(1));self.app.processEvents()
            self.assertEqual(ticks,[1]);self.window.cancel_operation();self.wait()
        self.assertEqual(self.window.outcome,'cancelled')
        self.assertFalse((self.root / 'gui output').exists())
        self.assertFalse(list(self.root.glob('.playden-*')))
        self.completion.assert_not_called()

    def test_invalid_validation_path_and_existing_output_are_safe(self):
        with patch('minecraft_playden.gui_app.QFileDialog.getExistingDirectory', return_value=str(self.root / 'missing')):
            self.window.validate_existing();self.wait()
        self.assertIn('package.json',self.window.log.toPlainText())
        instance, _, _ = installed(self.root / 'source');self.select(instance)
        output=self.root/'existing';output.mkdir();save(output,'sentinel',b'keep')
        self.window.output.setText(str(output));self.assertFalse(self.window.build_button.isEnabled())
        self.window.start_build();self.assertFalse(self.window.busy());self.assertEqual((output/'sentinel').read_bytes(),b'keep')
