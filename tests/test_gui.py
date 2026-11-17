"""Optional offscreen Qt smoke tests; core discovery/request tests need no Qt."""
import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from minecraft_playden.discovery import DiscoveryResult, Installation, discover
from minecraft_playden.desktop import suggest_output, ValidatedPackage
from minecraft_playden.progress import Progress
from minecraft_playden.operations import OperationCancelled
from minecraft_playden.transfer import TransferRequest, TransferResult, TransferError
from minecraft_playden.volumes import DriveTarget, VolumeIdentity
from minecraft_playden.modloaders import ModLoader, packaging_limitation

HAS_QT = importlib.util.find_spec('PySide6') is not None
if HAS_QT:
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import Qt, QCoreApplication, QEvent, QSettings, QRect, QPoint
    from PySide6.QtWidgets import QApplication, QStyleFactory
    from PySide6.QtTest import QTest
    from minecraft_playden.gui_app import MainWindow
    CONFIRM_UNTESTED = MainWindow.confirm_untested


@unittest.skipUnless(HAS_QT, 'Install .[gui] to run optional offscreen Qt tests')
class GuiTests(unittest.TestCase):
    def test_main_window_uses_the_official_application_icon(self):
        from PySide6.QtGui import QImage
        from PySide6.QtCore import QSize
        from minecraft_playden.branding import icon_directory
        rendered = self.window.windowIcon().pixmap(QSize(32, 32), 1.0).toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        expected = QImage(str(icon_directory() / 'playdencreeper-32.png')).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self.assertFalse(rendered.isNull())
        self.assertEqual(rendered, expected)
    def sorting_installation(self, folder, name, version='26.3', **kwargs):
        instance = self.root / 'sorting' / folder
        instance.mkdir(parents=True, exist_ok=True)
        client = instance / 'client.jar'
        client.write_bytes(b'fixture client')
        return Installation(name, kwargs.pop('launcher', 'Prism Launcher'), version, instance, client, **kwargs)

    def populate(self, installations):
        self.window.operation = 'scan'
        self.window.task_result(DiscoveryResult(tuple(installations)))

    def click_header(self, column):
        self.window.show_paths.setChecked(True)
        self.window.resize(1280, 900)
        self.window.show()
        self.app.processEvents()
        header = self.window.table.horizontalHeader()
        self.window.table.horizontalScrollBar().setValue(header.sectionPosition(column))
        position = QPoint(header.sectionViewportPosition(column) + header.sectionSize(column) // 2,
                          header.height() // 2)
        QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=position)
        self.app.processEvents()

    def visual_installations(self):
        return [self.window.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                for row in range(self.window.table.rowCount())]

    def test_real_header_clicks_sort_every_column_and_keep_complete_rows(self):
        a = self.sorting_installation('zeta', 'bravo', '1.10', launcher='Zulu',
                                      loaders=(ModLoader('Quilt', '1'),), problem='z issue')
        b = self.sorting_installation('alpha', 'Alpha', '1.9', launcher='alpha',
                                      loaders=(ModLoader('Fabric', '1'),), problem='A issue')
        c = self.sorting_installation('middle', 'charlie', launcher='MultiMC', problem='middle issue')
        self.populate([a, b, c])
        custom_output = str(self.root / 'manually chosen output')
        self.window.output.setText(custom_output)
        self.window.output.textEdited.emit(custom_output)
        expected = ([b, a, c], [b, c, a], [b, a, c], [b, c, a], [b, c, a], [b, a, c], [b, c, a])
        for column, ascending in enumerate(expected):
            with self.subTest(column=column):
                self.click_header(column)
                header = self.window.table.horizontalHeader()
                self.assertTrue(header.isSortIndicatorShown())
                self.assertEqual(header.sortIndicatorSection(), column)
                self.assertEqual(header.sortIndicatorOrder(), Qt.SortOrder.AscendingOrder)
                self.assertEqual(self.visual_installations(), ascending)
                self.click_header(column)
                self.assertEqual(header.sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
                self.assertEqual(self.visual_installations(), list(reversed(ascending)))
                for row, installation in enumerate(self.visual_installations()):
                    values = [installation.name, installation.launcher, installation.version,
                              str(installation.instance), str(installation.client), installation.loader_label,
                              installation.compatibility.label]
                    self.assertEqual([self.window.table.item(row, col).text() for col in range(7)], values)
                    self.window.table.selectRow(row)
                    self.assertEqual(self.window.selected_installation(), installation)
                    self.assertEqual(self.window.source_path.text(), str(installation.instance))
                    self.assertEqual(self.window.client_path.text(), str(installation.client))
                    self.assertEqual(Path(self.window.output.text()), self.root / installation.name)

    def test_names_are_case_insensitive_and_versions_use_numeric_components(self):
        versions = ['1.10.12', '26.3', '1.9', '1.10.2', '1.10', '25w10a', '25w9a', 'Unknown']
        names = ['charlie', 'Bravo', 'alpha', 'ALPHA', 'delta', 'ECHO', 'foxtrot', 'Golf']
        installations = [self.sorting_installation(str(i), name, version)
                         for i, (name, version) in enumerate(zip(names, versions))]
        self.populate(installations)
        self.click_header(0)
        self.assertEqual([i.name.casefold() for i in self.visual_installations()], sorted(n.casefold() for n in names))
        self.click_header(2)
        expected = ['1.9', '1.10', '1.10.2', '1.10.12', '25w9a', '25w10a', '26.3', 'Unknown']
        self.assertEqual([i.version for i in self.visual_installations()], expected)
        self.click_header(2)
        self.assertEqual([i.version for i in self.visual_installations()], list(reversed(expected)))

    def test_path_sort_uses_full_paths_when_visible_names_and_suffixes_match(self):
        a = self.sorting_installation('a-' + 'long-path-' * 15 + '/same-instance', 'Same name')
        z = self.sorting_installation('Z-' + 'long-path-' * 15 + '/same-instance', 'Same name')
        # Source and JAR orders deliberately differ.
        a, z = replace(a, client=z.client), replace(z, client=a.client)
        self.populate([z, a])
        self.click_header(3)
        self.assertEqual(self.visual_installations(), [a, z])
        self.assertEqual(self.window.table.item(0, 3).toolTip(), str(a.instance))
        self.click_header(4)
        self.assertEqual(self.visual_installations(), [z, a])
        self.assertEqual(self.window.table.item(0, 4).toolTip(), str(z.client))

    def test_selection_sort_and_manual_output_survive_async_rescan_with_duplicates(self):
        a = self.sorting_installation('first', 'Duplicate', '1.9')
        b = self.sorting_installation('second', 'Duplicate', '1.10')
        c = self.sorting_installation('third', 'Other', '26.3')
        self.populate([a, b, c])
        self.window.table.selectRow(1)
        manual = str(self.root / 'keep this output')
        self.window.output.setText(manual)
        self.window.output.textEdited.emit(manual)
        self.click_header(2)
        self.assertEqual(self.window.selected_installation(), b)
        self.click_header(2)
        self.assertEqual(self.window.selected_installation(), b)
        # A renamed, newly unavailable installation must stay selected by identity.
        updated = replace(b, name='Renamed', problem='Client is unavailable')
        with patch('minecraft_playden.gui_app.discover', return_value=DiscoveryResult((c, updated, a))):
            self.window.scan()
            self.wait_until_idle()
        self.assertEqual(self.visual_installations(), [c, updated, a])
        self.assertEqual(self.window.selected_installation(), updated)
        self.assertEqual(self.window.table.horizontalHeader().sortIndicatorSection(), 2)
        self.assertEqual(self.window.table.horizontalHeader().sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
        self.assertEqual(self.window.output.text(), manual)
        self.assertFalse(self.window.build_button.isEnabled())

    def test_empty_table_and_duplicate_names_remain_safe_through_refresh(self):
        self.populate([])
        self.click_header(0)
        self.click_header(0)
        self.populate([])
        self.assertIsNone(self.window.selected_installation())
        a = self.sorting_installation('first', 'Duplicate')
        b = self.sorting_installation('second', 'Duplicate')
        self.populate([a, b])
        row = self.visual_installations().index(b)
        self.window.table.selectRow(row)
        self.populate([b, a])
        self.assertEqual(self.window.selected_installation(), b)
        self.assertEqual(self.window.table.horizontalHeader().sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
        self.populate([])
        self.assertIsNone(self.window.selected_installation())
        self.assertEqual(self.window.source_path.text(), '')
        self.assertFalse(self.window.build_button.isEnabled())

    def test_sort_preferences_survive_reopening_and_apply_to_first_scan(self):
        a = self.sorting_installation('first', 'Alpha', '1.9')
        b = self.sorting_installation('second', 'beta', '1.10')
        self.populate([a, b])
        self.click_header(2)
        self.click_header(2)
        self.window.close()
        self.settings.sync()
        with patch('minecraft_playden.gui_app.QTimer.singleShot'):
            reopened = MainWindow(self.root / 'cache', [])
        try:
            reopened.operation = 'scan'
            reopened.task_result(DiscoveryResult((a, b)))
            self.assertEqual([reopened.table.item(row, 2).text() for row in range(2)], ['1.10', '1.9'])
            self.assertEqual(reopened.table.horizontalHeader().sortIndicatorSection(), 2)
            self.assertEqual(reopened.table.horizontalHeader().sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
        finally:
            reopened.close()
            reopened.deleteLater()

    def test_small_layout_scrolls_many_instances_with_fixed_actions_in_both_styles(self):
        self.window.operation = 'scan'
        self.window.task_result(DiscoveryResult(tuple(
            Installation(f'Vanilla installation {number}', 'Prism Launcher', '26.3', self.instance, self.jar)
            for number in range(40))))
        original_style = self.app.style().objectName()
        try:
            for style in ('Fusion', 'Windows'):
                if style not in QStyleFactory.keys():
                    continue
                with self.subTest(style=style):
                    self.app.setStyle(style)
                    self.window.resize(800, 600)
                    self.window.show()
                    self.app.processEvents()
                    self.assertLessEqual(self.window.height(), 600)
                    table = self.window.table
                    self.assertGreaterEqual(table.height(), 220)
                    self.assertGreaterEqual(table.viewport().height(), table.rowHeight(0) * 5)
                    self.assertGreater(table.verticalScrollBar().maximum(), 0)
                    table.scrollToItem(table.item(39, 0))
                    self.app.processEvents()
                    self.assertTrue(table.viewport().rect().intersects(table.visualItemRect(table.item(39, 0))))
                    for column, value in ((3, str(self.instance)), (4, str(self.jar))):
                        self.assertEqual(table.item(39, column).toolTip(), value)
                    body = self.window.centralWidget()
                    for widget in (self.window.build_button, self.window.open_output_button,
                                   self.window.copy_usb_button, self.window.cancel_button,
                                   self.window.progress_bar, self.window.log):
                        bounds = QRect(widget.mapTo(body, QPoint()), widget.size())
                        self.assertTrue(widget.isVisible())
                        self.assertTrue(body.rect().contains(bounds), widget.objectName() or str(widget))
                    # The complete configuration remains reachable without moving
                    # the fixed actions or reducing the installation list.
                    self.window.details_scroll.ensureWidgetVisible(self.window.cache_button)
                    self.app.processEvents()
                    viewport = self.window.details_scroll.viewport()
                    bounds = QRect(self.window.cache_button.mapTo(viewport, QPoint()), self.window.cache_button.size())
                    self.assertTrue(viewport.rect().intersects(bounds))
        finally:
            self.app.setStyle(original_style)

    def test_instance_list_grows_and_splitter_can_reallocate_space(self):
        self.select_source()
        self.window.resize(1024, 700)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(self.window.table.horizontalScrollBar().maximum(), 0)
        self.assertGreaterEqual(self.window.table.columnWidth(6), 240)
        original = self.window.table.height()
        self.window.show_paths.setChecked(True)
        self.window.resize(1280, 900)
        self.app.processEvents()
        self.assertGreater(self.window.table.height(), original)
        sizes = self.window.splitter.sizes()
        self.window.splitter.setSizes([sizes[0] + 40, sizes[1] - 40])
        self.app.processEvents()
        self.assertGreater(self.window.table.height(), sizes[0])
        self.assertEqual(self.window.selected_installation(), self.result.installations[0])
        self.assertEqual(self.window.output.text(), str(self.root / 'new-package'))

    def test_splitter_position_survives_reopening_without_persisting_build_paths(self):
        self.select_source()
        self.window.resize(1200, 900)
        self.window.show()
        self.app.processEvents()
        self.window.splitter.setSizes([380, 200])
        expected = self.window.splitter.sizes()
        self.window.close()
        self.settings.sync()
        self.assertEqual(self.settings.allKeys(), ['layout/instanceSplitter', 'player/offlineName'])
        with patch('minecraft_playden.gui_app.QTimer.singleShot'):
            reopened = MainWindow(self.root / 'cache', [])
        try:
            reopened.resize(1200, 900)
            reopened.show()
            self.app.processEvents()
            self.assertEqual(reopened.splitter.sizes(), expected)
            self.assertEqual(reopened.output.text(), '')
        finally:
            reopened.close()
            reopened.deleteLater()

    def test_open_folder_uses_local_qt_url_for_spaces_and_unicode(self):
        directory = self.root / 'Minecraft Café With Spaces'
        directory.mkdir()
        with patch('minecraft_playden.gui_app.QDesktopServices.openUrl', return_value=True) as open_url:
            self.window.open_directory(directory)
        self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()), directory.resolve())

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        confirmation = patch('minecraft_playden.gui_app.MainWindow.confirm_untested', return_value=True)
        confirmation.start()
        self.addCleanup(confirmation.stop)
        # A queued Qt slot can enter a nested modal loop inside processEvents(),
        # defeating wait_until_idle's deadline. Unexpected warnings must fail the
        # test after cleanup, rather than await human input on a native runner.
        warning_guard = patch('minecraft_playden.gui_app.QMessageBox.warning', return_value=0)
        self.unexpected_warning = warning_guard.start()
        self.addCleanup(warning_guard.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root / 'layout.ini'), QSettings.Format.IniFormat)
        settings_patch = patch('minecraft_playden.gui_app.QSettings', return_value=self.settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.instance = self.root / 'source/versions/26.3'
        self.instance.mkdir(parents=True)
        self.jar = self.instance / '26.3.jar'
        self.jar.write_bytes(b'client')
        suggestion = patch('minecraft_playden.gui_app.suggest_output', side_effect=lambda installation: suggest_output(installation, self.root))
        suggestion.start()
        self.addCleanup(suggestion.stop)
        completion = patch('minecraft_playden.gui_app.MainWindow.show_completion')
        self.completion = completion.start()
        self.addCleanup(completion.stop)
        self.result = DiscoveryResult((Installation('Minecraft', 'Official launcher', '26.3', self.instance, self.jar),))
        with patch('minecraft_playden.gui_app.QTimer.singleShot'):
            self.window = MainWindow(self.root / 'cache', [])
        batch_completion = patch.object(self.window, 'show_queue_completion')
        self.batch_completion = batch_completion.start()
        self.addCleanup(batch_completion.stop)

    def tearDown(self):
        self.wait_until_idle()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.temp.cleanup()
        self.unexpected_warning.assert_not_called()

    def wait_until_idle(self):
        deadline = time.monotonic() + 5
        while self.window.busy() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.001)
        self.assertFalse(self.window.busy(), 'Worker failed to finish')

    def select_source(self):
        self.window.operation = 'scan'
        self.window.task_result(self.result)
        self.window.output.setText(str(self.root / 'new-package'))

    def test_supported_fabric_rows_enable_build_and_show_both_versions(self):
        from test_fabric_launch import installed
        for layout in ('prism', 'multimc', 'official'):
            with self.subTest(layout=layout):
                launcher = self.root / ('fabric-' + layout)
                installed(launcher, layout=layout)
                result = discover([launcher], include_defaults=False)
                row = next(row for row in result.installations if row.loaders)
                self.populate([row])
                self.window.table.selectRow(0)
                self.window.output.setText(str(self.root / ('new-' + layout)))
                self.assertEqual(self.window.loader.text(), 'Fabric 0.16.10')
                self.assertEqual(self.window.version.text(), '1.20.1')
                self.assertTrue(self.window.build_button.isEnabled(), self.window.build_reason.text())

    def test_async_scan_updates_table_and_selection(self):
        threads = []
        def scan(paths):
            threads.append(threading.get_ident())
            return self.result
        with patch('minecraft_playden.gui_app.discover', side_effect=scan):
            self.window.scan()
            self.assertFalse(self.window.scan_button.isEnabled())
            self.wait_until_idle()
        self.assertNotEqual(threads, [threading.get_ident()])
        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertEqual(self.window.table.item(0, 2).text(), '26.3')
        self.assertEqual(self.window.table.item(0, 4).text(), str(self.jar))
        self.assertTrue(self.window.output.text())
        self.assertTrue(self.window.build_button.isEnabled())
        self.assertIn('Ready to build', self.window.build_reason.text())

    def test_selection_populates_required_fields_and_suggests_fresh_output(self):
        self.window.operation = 'scan'
        self.window.task_result(self.result)
        self.assertEqual(self.window.source_path.text(), str(self.instance))
        self.assertEqual(self.window.version.text(), '26.3')
        self.assertEqual(self.window.client_path.text(), str(self.jar))
        self.assertEqual(self.window.loader.text(), 'Vanilla')
        self.assertTrue(self.window.output.text())
        self.assertFalse(Path(self.window.output.text()).exists())
        self.assertTrue(self.window.build_button.isEnabled())

    def test_disabled_build_explains_missing_selection_output_cache_and_invalid_paths(self):
        self.assertIn('Select a detected installation', self.window.build_reason.text())
        self.select_source()
        self.window.output.clear()
        self.assertFalse(self.window.build_button.isEnabled())
        self.assertIn('output folder', self.window.build_reason.text())
        self.window.cache.clear()
        self.assertIn('cache folder', self.window.build_reason.text())
        self.window.cache.setText(str(self.root / 'cache'))
        self.window.output.setText(str(self.instance / 'output'))
        self.assertIn('separate from the source', self.window.build_reason.text())
        self.assertFalse(self.window.build_button.isEnabled())
        self.window.output.setText(str(self.root / 'new-package'))
        self.assertTrue(self.window.build_button.isEnabled())
        (self.root / 'new-package').mkdir()
        self.window.update_build_button()
        self.assertFalse(self.window.build_button.isEnabled())
        self.assertIn('Existing folders', self.window.build_reason.text())

    def test_switching_selection_updates_fields_and_output_name(self):
        other = self.root / 'other'
        other.mkdir()
        (other / 'client.jar').write_bytes(b'other client')
        second = Installation('Second', 'MultiMC', '1.21.1', other, other / 'client.jar')
        self.window.operation = 'scan'
        self.window.task_result(DiscoveryResult((self.result.installations[0], second)))
        first_suggestion = self.window.output.text()
        self.window.table.selectRow(1)
        self.assertEqual(self.window.version.text(), '1.21.1')
        self.assertEqual(self.window.source_path.text(), str(other))
        self.assertNotEqual(first_suggestion, self.window.output.text())
        custom = str(self.root / 'my custom output')
        self.window.output.setText(custom)
        self.window.table.selectRow(0)
        self.assertEqual(Path(self.window.output.text()), self.root / 'Minecraft')
        self.assertEqual(self.window.version.text(), '26.3')
        self.assertTrue(self.window.build_button.isEnabled())

    def test_rescan_preserves_selection_and_manual_folder_takes_priority(self):
        other = self.root / 'other'
        other.mkdir()
        (other / 'client.jar').write_bytes(b'other client')
        second = Installation('Second', 'MultiMC', '1.21.1', other, other / 'client.jar')
        result = DiscoveryResult((self.result.installations[0], second))
        self.window.operation = 'scan'
        self.window.task_result(result)
        self.window.table.selectRow(1)
        self.window.task_result(result)
        self.assertEqual(self.window.source_path.text(), str(other))
        self.window._preferred_path = self.instance
        self.window.task_result(result)
        self.assertEqual(self.window.source_path.text(), str(self.instance))

    def test_visual_sorting_keeps_selection_bound_to_the_correct_installation(self):
        other = self.root / 'other'
        other.mkdir()
        (other / 'client.jar').write_bytes(b'other client')
        second = Installation('AAA first after sorting', 'MultiMC', '1.21.1', other, other / 'client.jar')
        self.window.operation = 'scan'
        self.window.task_result(DiscoveryResult((self.result.installations[0], second)))
        self.window.table.setSortingEnabled(True)
        self.window.table.sortItems(0, Qt.SortOrder.AscendingOrder)
        self.window.table.selectRow(0)
        self.assertEqual(self.window.source_path.text(), str(other))
        self.assertEqual(self.window.version.text(), '1.21.1')
        self.assertTrue(self.window.build_button.isEnabled())

    def test_official_prism_and_multimc_discovered_vanilla_rows_enable_build(self):
        roots = []
        official = self.root / 'official/versions/26.3'
        official.mkdir(parents=True)
        (official / '26.3.json').write_text(json.dumps({'id': '26.3'}))
        (official / '26.3.jar').write_bytes(b'client')
        roots.append(official.parent.parent)
        for name, cfg in [('PrismLauncher', 'prismlauncher.cfg'), ('MultiMC', 'multimc.cfg')]:
            root = self.root / name
            instance = root / 'instances/Test'
            instance.mkdir(parents=True)
            (root / cfg).write_text('[General]\n')
            (instance / 'mmc-pack.json').write_text(json.dumps({'components': [{'uid': 'net.minecraft', 'version': '26.3'}]}))
            jar = root / 'libraries/com/mojang/minecraft/26.3/minecraft-26.3-client.jar'
            jar.parent.mkdir(parents=True)
            jar.write_bytes(b'client')
            roots.append(root)
        result = discover(roots, include_defaults=False)
        self.assertEqual(len(result.installations), 3)
        self.window.operation = 'scan'
        self.window.task_result(result)
        for row, installation in enumerate(result.installations):
            self.window.table.selectRow(row)
            self.assertEqual(self.window.source_path.text(), str(installation.instance))
            self.assertEqual(self.window.client_path.text(), str(installation.client))
            self.assertEqual(self.window.version.text(), '26.3')
            self.assertTrue(self.window.build_button.isEnabled(), self.window.build_reason.text())

    def test_modded_row_is_selectable_with_base_and_loader_and_explicit_block_reason(self):
        loaders = (ModLoader('Fabric', '0.16.10'),)
        installation = Installation('Fabric instance', 'Prism Launcher', '26.3', self.instance, self.jar,
                                    loaders=loaders, packaging_problem=packaging_limitation(loaders))
        self.window.operation = 'scan'
        self.window.task_result(DiscoveryResult((installation,)))
        self.window.table.selectRow(0)
        self.assertEqual(self.window.source_path.text(), str(self.instance))
        self.assertEqual(self.window.version.text(), '26.3')
        self.assertEqual(self.window.client_path.text(), str(self.jar))
        self.assertEqual(self.window.loader.text(), 'Fabric 0.16.10')
        self.assertFalse(self.window.build_button.isEnabled())
        self.assertIn('Modded packaging', self.window.build_reason.text())
        self.assertNotIn('version is unsupported', self.window.build_reason.text())

    def test_build_runs_off_ui_thread_reports_progress_and_completes(self):
        self.select_source()
        threads = []
        def run(request, progress, cancellation=None):
            threads.append(threading.get_ident())
            progress(Progress('assets', 'Downloading public assets', 1, 2))
            progress(Progress('complete', 'Validated', 1, 1))
            request.output.mkdir()
            return ValidatedPackage(request.output)
        with patch('minecraft_playden.gui_app.LocalBuildRequest.run', autospec=True, side_effect=run):
            self.window.start_build()
            self.assertFalse(self.window.build_button.isEnabled())
            self.wait_until_idle()
        self.assertNotEqual(threads, [threading.get_ident()])
        self.assertIn('Validated package created', self.window.status.text())
        self.assertIn('@../../playden.args', self.window.log.toPlainText())
        self.assertEqual(self.window.progress_bar.text(), '100%')
        self.assertTrue(self.window.scan_button.isEnabled())
        self.completion.assert_called_once_with(self.root / 'new-package', False)
        self.assertTrue(self.window.open_output_button.isEnabled())
        self.assertTrue(self.window.copy_usb_button.isEnabled())
        with patch('minecraft_playden.gui_app.QDesktopServices.openUrl', return_value=True) as opened:
            self.window.open_output_button.click()
        self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), (self.root / 'new-package').resolve())

    def test_build_failure_shows_error_and_never_reports_success(self):
        self.select_source()
        with patch('minecraft_playden.gui_app.LocalBuildRequest.run', side_effect=ValueError('Hash mismatch')), patch('minecraft_playden.gui_app.QMessageBox.warning') as warning:
            self.window.start_build()
            self.wait_until_idle()
            warning.assert_called_once()
        self.assertIn('Build failed', self.window.status.text())
        self.assertIn('Hash mismatch', self.window.log.toPlainText())
        self.assertNotIn('Validated package created', self.window.log.toPlainText())
        self.assertFalse((self.root / 'new-package').exists())

    def test_missing_validated_result_does_not_trigger_completion_even_with_complete_progress(self):
        self.select_source()
        def run(request, progress, cancellation):
            progress(Progress('complete', 'Premature progress', 1, 1))
            return None
        with patch('minecraft_playden.gui_app.LocalBuildRequest.run', autospec=True, side_effect=run), patch('minecraft_playden.gui_app.QMessageBox.warning'):
            self.window.start_build()
            self.wait_until_idle()
        self.assertEqual(self.window.outcome, 'failed')
        self.completion.assert_not_called()
        self.assertFalse(self.window.open_output_button.isEnabled())

    def test_cancelled_build_keeps_last_validated_output_and_ui_responsive(self):
        self.select_source()
        good = self.root / 'previous-package'
        good.mkdir()
        self.window.completed_package = ValidatedPackage(good)
        started = threading.Event()
        def run(request, progress, cancellation):
            started.set()
            cancellation.wait(3)
            self.fail('Expected cancellation')
        with patch('minecraft_playden.gui_app.LocalBuildRequest.run', autospec=True, side_effect=run), patch('minecraft_playden.gui_app.QMessageBox.warning') as warning:
            self.window.start_build()
            self.assertTrue(started.wait(1))
            self.assertTrue(self.window.cancel_button.isEnabled())
            ticks = []
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: ticks.append(True))
            self.app.processEvents()
            self.assertTrue(ticks, 'GUI event loop blocked by build worker')
            self.window.cancel_button.click()
            self.wait_until_idle()
            warning.assert_not_called()
        self.assertEqual(self.window.outcome, 'cancelled')
        self.assertEqual(self.window.progress_bar.text(), '0%')
        self.assertIn('Build cancelled', self.window.status.text())
        self.completion.assert_not_called()
        self.assertTrue(self.window.open_output_button.isEnabled())
        self.assertEqual(self.window.completed_package.path, good)

    def test_output_actions_use_qt_desktop_services_and_handle_open_failure(self):
        good = self.root / 'package with spaces'
        good.mkdir()
        self.window.completed_package = ValidatedPackage(good)
        self.window.update_build_button()
        self.assertTrue(self.window.open_output_button.isEnabled())
        with patch('minecraft_playden.gui_app.QDesktopServices.openUrl', return_value=True) as open_url:
            self.window.open_output_button.click()
            self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()).resolve(), good.resolve())
        with patch('minecraft_playden.gui_app.QDesktopServices.openUrl', return_value=False), patch('minecraft_playden.gui_app.QMessageBox.warning') as warning:
            self.window.open_output_folder()
            warning.assert_called_once()
        self.assertTrue(self.window.copy_usb_button.isEnabled())

    def test_switching_instance_refreshes_explicit_output_with_collision_suffix(self):
        self.select_source()
        path = self.window.output.text()
        with patch('minecraft_playden.gui_app.QFileDialog.getSaveFileName', return_value=(path, '')):
            self.window.choose_output()
        (self.root / 'other').mkdir()
        (self.root / 'other/client.jar').write_bytes(b'client')
        second = Installation('Other', 'MultiMC', '1.21.1', self.root / 'other', self.root / 'other/client.jar')
        self.window.task_result(DiscoveryResult((second,)))
        self.assertEqual(Path(self.window.output.text()), Path(path).parent / 'Other-2')

    def test_usb_transfer_worker_reports_bytes_and_can_retry_after_failure(self):
        good = self.root / 'validated-package'
        good.mkdir()
        self.window.completed_package = ValidatedPackage(good)
        usb = self.root / 'USB'
        usb.mkdir()
        info = usb.stat()
        drive = DriveTarget(usb, VolumeIdentity('test', 'exfat', info.st_dev, info.st_ino))
        request = TransferRequest(good, drive)
        # Match TransferRequest._run: the source is canonical, the destination
        # is the planned path. Windows temp roots may use an 8.3 alias.
        result = TransferResult(good.resolve(), request.destination, 3, 256)
        thread_ids = []
        def success(transfer, progress, cancellation):
            thread_ids.append(threading.get_ident())
            progress(Progress('transfer-copy', 'Copying', 128, 256, 'libraries/example.jar', 128, 256))
            return result
        with patch('minecraft_playden.gui_app.TransferRequest.run', side_effect=TransferError('Disconnected')), patch('minecraft_playden.gui_app.QMessageBox.warning'):
            self.window.start_transfer(request)
            self.wait_until_idle()
        self.assertEqual(self.window.outcome, 'failed')
        self.assertTrue(self.window.retry_usb_button.isEnabled())
        self.completion.assert_not_called()
        with patch('minecraft_playden.gui_app.TransferRequest.run', autospec=True, side_effect=success), patch('minecraft_playden.volumes.DriveTarget.check'):
            self.window.retry_usb_button.click()
            self.wait_until_idle()
        self.assertEqual(len(thread_ids), 1)
        self.assertNotEqual(thread_ids, [threading.get_ident()])
        self.assertEqual(self.window.outcome, 'succeeded')
        self.assertIn('256 B', self.window.status.text())
        self.completion.assert_called_once_with(request.destination, True)
        self.assertFalse(self.window.retry_usb_button.isEnabled())
        self.assertTrue(self.window.copy_usb_button.isEnabled())

    def test_transfer_result_alias_mismatch_finishes_and_retry_accepts_canonical_source(self):
        # Reproduce Windows short/long temp aliases on every host without relying
        # on symlink privileges: this spelling differs from its resolved path.
        (self.root / 'alias').mkdir()
        source = self.root / 'alias' / '..' / 'validated-package'
        source.mkdir()
        self.assertNotEqual(source, source.resolve())
        self.window.completed_package = ValidatedPackage(source)
        usb = self.root / 'USB'
        usb.mkdir()
        info = usb.stat()
        drive = DriveTarget(usb, VolumeIdentity('test', 'exfat', info.st_dev, info.st_ino))
        request = TransferRequest(source, drive)
        from minecraft_playden.desktop import BuildRecord, LocalBuildRequest
        record = BuildRecord(LocalBuildRequest(self.result.installations[0], source, self.root / 'cache'),
                             state='completed', package=self.window.completed_package)
        self.window.build_records = [record]
        invalid = TransferResult(source, request.destination, 1, 1)
        with patch('minecraft_playden.gui_app.TransferRequest.run', return_value=invalid), \
             patch('minecraft_playden.gui_app.QMessageBox.warning') as warning:
            self.window.start_transfer(request)
            self.wait_until_idle()
        self.assertIn('does not match', warning.call_args.args[2])
        self.assertEqual(self.window.outcome, 'failed')
        self.assertIsNone(self.window.worker_thread)
        self.assertIsNone(self.window.task)
        self.assertIsNone(self.window.cleanup_transfer)
        self.assertTrue(self.window.retry_usb_button.isEnabled())
        self.completion.assert_not_called()
        valid = TransferResult(source.resolve(), request.destination, 1, 1)
        with patch('minecraft_playden.gui_app.TransferRequest.run', return_value=valid), \
             patch('minecraft_playden.volumes.DriveTarget.check'), \
             patch('minecraft_playden.gui_app.QMessageBox.warning') as warning:
            self.window.retry_usb_button.click()
            self.wait_until_idle()
        warning.assert_not_called()
        self.assertEqual(self.window.outcome, 'succeeded')
        self.assertEqual(record.usb, request.destination)
        self.assertIn('checksum-verified', record.detail)
        self.assertIsNone(self.window.worker_thread)
        self.assertIsNone(self.window.task)
        self.completion.assert_called_once_with(request.destination, True)

    def test_open_shared_queue_parent_resolves_path_aliases(self):
        (self.root / 'alias').mkdir()
        parent = self.root / 'alias' / '..' / 'shared'
        parent.mkdir()
        self.assertNotEqual(parent, parent.resolve())
        self.window.output_view = parent
        with patch('minecraft_playden.gui_app.QDesktopServices.openUrl', return_value=True) as opened:
            self.window.open_output_folder()
        self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), parent.resolve())

    def test_transfer_progress_formats_sizes_without_changing_exact_values(self):
        event = Progress('transfer-copy', 'Copying', 123456789, 2000000000,
                         'libraries/example.jar', 123456789, 2000000000)
        self.window.task_progress(event)
        self.assertIn('123.46 MB / 2.00 GB copied', self.window.status.text())
        self.assertIn('libraries/example.jar', self.window.status.text())
        self.assertEqual(event.bytes_copied, 123456789)
        self.assertEqual(event.bytes_total, 2000000000)
        self.assertEqual(self.window.progress_bar.value(), 61)
        self.window.task_progress(Progress('transfer-copy', 'Copying', 0, 0, 'empty', 0, 0))
        self.assertIn('0 B / 0 B copied', self.window.status.text())

    def test_transfer_cancellation_releases_worker_and_enables_retry(self):
        good = self.root / 'validated-package'
        good.mkdir()
        self.window.completed_package = ValidatedPackage(good)
        usb = self.root / 'USB'
        usb.mkdir()
        drive = DriveTarget(usb, VolumeIdentity('test', 'exfat', 0, 0))
        started = threading.Event()
        def run(request, progress, cancellation):
            started.set()
            cancellation.wait(3)
        with patch('minecraft_playden.gui_app.TransferRequest.run', autospec=True, side_effect=run):
            self.window.start_transfer(TransferRequest(good, drive))
            self.assertTrue(started.wait(1))
            self.window.cancel_operation()
            self.wait_until_idle()
        self.assertEqual(self.window.outcome, 'cancelled')
        self.assertEqual(self.window.progress_bar.text(), '0%')
        self.assertIn('USB transfer cancelled', self.window.status.text())
        self.assertTrue(self.window.retry_usb_button.isEnabled())
        self.completion.assert_not_called()

    def test_missing_client_is_unbuildable(self):
        self.jar.unlink()
        self.select_source()
        self.window.update_build_button()
        self.assertFalse(self.window.build_button.isEnabled())
        self.window.table.selectRow(0)
        with patch('minecraft_playden.gui_app.QMessageBox.warning') as warning:
            self.window.start_build()
            warning.assert_called_once()
        self.assertFalse(self.window.busy())

    def test_output_picker_chooses_new_path_without_creating_it(self):
        output = self.root / 'new output folder'
        with patch('minecraft_playden.gui_app.QFileDialog.getSaveFileName', return_value=(str(output), '')):
            self.window.choose_output()
        self.assertEqual(self.window.output.text(), str(output))
        self.assertFalse(output.exists())

    def test_manual_folder_picker_retains_path_for_rescan(self):
        with patch('minecraft_playden.gui_app.QFileDialog.getExistingDirectory', return_value=str(self.root)), patch.object(self.window, 'scan') as scan:
            self.window.add_folder()
            self.window.add_folder()
        self.assertEqual(self.window.paths, [self.root])
        self.assertEqual(scan.call_count, 2)

    def test_existing_output_is_rejected_before_worker_starts(self):
        self.select_source()
        (self.root / 'new-package').mkdir()
        with patch('minecraft_playden.gui_app.QMessageBox.warning') as warning:
            self.window.start_build()
            warning.assert_called_once()
        self.assertFalse(self.window.busy())

    def test_completed_custom_output_resets_and_allows_repeated_builds(self):
        self.select_source()
        self.window.output_edited()
        requests = []
        def run(request, progress, cancellation):
            requests.append(request)
            request.output.mkdir(parents=True)
            return ValidatedPackage(request.output)
        with patch('minecraft_playden.gui_app.LocalBuildRequest.run', autospec=True, side_effect=run):
            for _ in range(2):
                self.assertTrue(self.window.build_button.isEnabled(), self.window.build_reason.text())
                self.window.start_build()
                self.wait_until_idle()
            other = self.sorting_installation('second', 'Second')
            self.populate([self.result.installations[0], other])
            self.window.table.selectRow(1)
            self.assertTrue(self.window.build_button.isEnabled())
            self.assertEqual(Path(self.window.output.text()).name, 'Second')
        self.assertNotEqual(requests[0].output, requests[1].output)

    def test_queue_is_sequential_isolates_errors_and_disambiguates_names(self):
        first = self.sorting_installation('one', 'Same?')
        second = self.sorting_installation('two', 'Same*')
        third = self.sorting_installation('three', 'Same?')
        self.populate([first, second, third])
        self.window.output.setText(str(self.root / 'batch/ignored'))
        existing = self.root / 'batch/Same_'
        existing.mkdir(parents=True)
        (existing / 'sentinel').write_text('keep')
        self.window.table.selectAll()
        calls = []
        def run(request, progress, cancellation):
            calls.append(request)
            progress(Progress('instance', request.installation.name, 1, 2))
            if len(calls) == 1:
                raise ValueError('fixture failure')
            request.output.mkdir()
            return ValidatedPackage(request.output)
        with patch('minecraft_playden.gui_app.LocalBuildRequest.run', autospec=True, side_effect=run), patch('minecraft_playden.gui_app.QMessageBox.warning') as warning:
            self.window.start_build()
            self.wait_until_idle()
            warning.assert_not_called()
        self.assertEqual([r.output.name for r in calls], ['Same_-2', 'Same_-3', 'Same_-4'])
        self.assertIn('fixture failure', self.window.queue_table.item(0, 1).text())
        self.assertIn('Completed', self.window.queue_table.item(1, 1).text())
        self.assertIn('Completed', self.window.queue_table.item(2, 1).text())
        self.assertEqual((existing / 'sentinel').read_text(), 'keep')
        self.window.queue_table.setCurrentCell(1, 1)
        self.assertEqual(self.window.completed_package.path, calls[1].output)
        self.completion.assert_not_called()

    def test_queue_cancel_stops_remaining_instances_and_preserves_completed(self):
        self.populate([self.sorting_installation(str(i), f'Instance {i}') for i in range(3)])
        self.window.table.selectAll()
        self.window.output.setText(str(self.root / 'queued/output'))
        calls = []
        started = threading.Event()
        def run(request, progress, cancellation):
            calls.append(request)
            if len(calls) == 2:
                progress(Progress('instance', 'Copying second instance', 1, 4))
                started.set()
                cancellation.wait(3)
            request.output.mkdir(parents=True)
            return ValidatedPackage(request.output)
        with patch('minecraft_playden.gui_app.LocalBuildRequest.run', autospec=True, side_effect=run):
            self.window.start_build()
            until = time.monotonic() + 3
            while not started.is_set() and time.monotonic() < until:
                self.app.processEvents()
                time.sleep(.001)
            self.assertTrue(started.is_set())
            self.app.processEvents()
            self.assertIn('Copying second instance (1/4)', self.window.queue_table.item(1, 1).text())
            self.window.cancel_operation()
            self.wait_until_idle()
        self.assertEqual(len(calls), 2)
        self.assertIn('Completed', self.window.queue_table.item(0, 1).text())
        self.assertEqual(self.window.queue_table.item(1, 1).text(), 'Cancelled')
        self.assertEqual(self.window.queue_table.item(2, 1).text(), 'Cancelled before starting')
        self.assertIn('1 succeeded, 0 failed, 1 cancelled, 1 not started', self.window.completed_label.text())
        self.batch_completion.assert_called_once()
        self.assertEqual(self.window.completed_package.path, calls[0].output)

    def test_mixed_compatibility_selection_blocks_and_reenables_without_restart(self):
        from minecraft_playden.compatibility import Status, UNTESTED_WARNING
        tested = self.sorting_installation('tested', 'Tested pack', loaders=(ModLoader('Fabric', '0.20.1'),))
        untested = self.sorting_installation('untested', 'Untested pack', '1.20.1', loaders=(ModLoader('Fabric', '0.16.10'),))
        for name in ('Forge', 'NeoForge', 'Quilt', 'Missing client'):
            with self.subTest(name=name):
                blocked = self.sorting_installation(name, name, loaders=() if name == 'Missing client' else (ModLoader(name, '1'),))
                if name == 'Missing client':
                    blocked.client.unlink()
                self.populate([tested, untested, blocked])
                self.window.table.selectAll()
                self.assertFalse(self.window.build_button.isEnabled())
                self.assertIn(name, self.window.build_reason.text())
                self.assertIn('1 of 3', self.window.build_reason.text())
                status = self.window.table.item(2, 6).text()
                self.assertTrue(status.startswith('Broken' if name == 'Forge' else 'Cannot package'))
                with patch.object(self.window, 'start_task') as start, patch('minecraft_playden.gui_app.QMessageBox.warning'):
                    self.window.start_build()
                    start.assert_not_called()
                from PySide6.QtCore import QItemSelectionModel
                self.window.table.selectionModel().select(self.window.table.model().index(2, 0),
                    QItemSelectionModel.SelectionFlag.Deselect | QItemSelectionModel.SelectionFlag.Rows)
                self.assertTrue(self.window.build_button.isEnabled(), self.window.build_reason.text())
                self.assertIn(UNTESTED_WARNING, self.window.build_reason.text())
                self.assertIn('without Ixeris', self.window.build_reason.text())
                requests, _ = self.window.build_requests()
                self.assertEqual([r.installation.compatibility.status for r in requests], [Status.TESTED, Status.UNTESTED])
                def run(request, progress, cancellation):
                    request.output.mkdir(parents=True)
                    return ValidatedPackage(request.output)
                with patch('minecraft_playden.gui_app.LocalBuildRequest.run', autospec=True, side_effect=run):
                    for _ in range(2):
                        self.window.start_build()
                        self.wait_until_idle()
                        self.assertTrue(self.window.build_button.isEnabled())
                        self.assertEqual(self.window.progress_bar.text(), '100%')
                        self.assertIn('Completed', self.window.queue_table.item(0, 1).text())
                        self.assertIn('Completed', self.window.queue_table.item(1, 1).text())
                self.assertTrue(self.window.table.item(1, 6).text().startswith('Unsupported / Untested'))

    def test_untested_confirmation_shows_exact_warning_and_can_decline(self):
        from minecraft_playden.compatibility import UNTESTED_WARNING
        from PySide6.QtWidgets import QMessageBox
        self.populate([self.sorting_installation('untested', 'Untested Minecraft', '26.2')])
        self.window.output.setText(str(self.root / 'new-package'))
        requests, _ = self.window.build_requests()
        with patch('minecraft_playden.gui_app.QMessageBox.warning', return_value=QMessageBox.StandardButton.No) as warning:
            self.assertFalse(CONFIRM_UNTESTED(self.window, requests))
            self.assertIn(UNTESTED_WARNING, warning.call_args.args[2])
            self.assertIn('Minecraft', warning.call_args.args[2])
        with patch.object(self.window, 'confirm_untested', side_effect=lambda items: CONFIRM_UNTESTED(self.window, items)), patch('minecraft_playden.gui_app.QMessageBox.warning', return_value=QMessageBox.StandardButton.No), patch.object(self.window, 'start_task') as start:
            self.window.start_build()
            start.assert_not_called()
        with patch('minecraft_playden.gui_app.QMessageBox.warning', return_value=QMessageBox.StandardButton.Yes):
            self.assertTrue(CONFIRM_UNTESTED(self.window, requests))
        tested = self.sorting_installation('tested', 'Tested', loaders=(ModLoader('Fabric', '0.20.1'),))
        with patch('minecraft_playden.gui_app.QMessageBox.warning') as warning:
            self.assertTrue(CONFIRM_UNTESTED(self.window, [replace(requests[0], installation=tested)]))
            warning.assert_not_called()

    def test_selected_instance_status_rechecks_missing_files(self):
        self.select_source()
        self.assertTrue(self.window.build_button.isEnabled())
        original = self.jar.read_bytes()
        self.jar.unlink()
        self.window.update_build_button()
        self.assertFalse(self.window.build_button.isEnabled())
        self.assertTrue(self.window.table.item(0, 6).text().startswith('Cannot package'))
        self.jar.write_bytes(original)
        self.window.update_build_button()
        self.assertTrue(self.window.build_button.isEnabled())
        self.assertTrue(self.window.table.item(0, 6).text().startswith('Tested'))

    def test_startup_and_completed_scan_stay_idle_after_selection_changes(self):
        self.assertEqual(self.window.progress_bar.text(), '0%')
        with patch('minecraft_playden.gui_app.discover', return_value=self.result):
            self.window.scan()
            self.assertEqual(self.window.progress_bar.text(), '0%')
            self.wait_until_idle()
        self.assertEqual(self.window.outcome, 'succeeded')
        self.assertEqual(self.window.progress_bar.text(), '0%')
        self.window.table.clearSelection()
        self.window.table.selectRow(0)
        self.assertEqual(self.window.progress_bar.text(), '0%')
        self.assertTrue(self.window.table.item(0, 6).text().startswith('Tested'))
        self.assertIn('Vanilla', self.window.build_reason.text())
        with patch('minecraft_playden.gui_app.QMessageBox.warning') as warning:
            self.assertTrue(CONFIRM_UNTESTED(self.window, self.window.build_requests()[0]))
            warning.assert_not_called()

    def test_repeated_builds_reset_progress_and_only_validated_success_reaches_100(self):
        self.select_source()
        def run(request, progress, cancellation):
            progress(Progress('assets', 'Stage complete', 1, 1))
            request.output.mkdir(parents=True)
            return ValidatedPackage(request.output)
        with patch('minecraft_playden.gui_app.LocalBuildRequest.run', autospec=True, side_effect=run):
            for _ in range(2):
                self.window.start_build()
                self.assertEqual(self.window.progress_bar.text(), '0%')
                self.wait_until_idle()
                self.assertEqual(self.window.progress_bar.text(), '100%')
        self.window.table.clearSelection()
        self.window.table.selectRow(0)
        self.assertEqual(self.window.progress_bar.text(), '0%')
        for event in (Progress('assets', 'Assets complete', 1, 1),
                      Progress('validation', 'Hashes complete', 5, 5),
                      Progress('transfer-copy', 'Copied', 100, 100, 'file', 100, 100),
                      Progress('complete', 'Unconfirmed completion', 1, 1)):
            self.window.task_progress(event)
            self.assertEqual(self.window.progress_bar.text(), '99%')
        with patch('minecraft_playden.gui_app.LocalBuildRequest.run', return_value=None), patch('minecraft_playden.gui_app.QMessageBox.warning'):
            self.window.start_build()
            self.wait_until_idle()
        self.assertEqual(self.window.outcome, 'failed')
        self.assertEqual(self.window.progress_bar.text(), '0%')

    def test_queue_transitions_reset_progress_and_partial_failures_do_not_show_success(self):
        self.populate([self.sorting_installation(str(i), f'Instance {i}') for i in range(2)])
        self.window.table.selectAll()
        self.window.output.setText(str(self.root / 'progress/output'))
        # Observe each worker start on the GUI thread, before its events are handled.
        starts = []
        real_start = self.window.start_task
        def start(operation, work):
            real_start(operation, work)
            starts.append(self.window.progress_bar.text())
        calls = []
        def run(request, progress, cancellation):
            calls.append(request)
            progress(Progress('complete', 'Stage complete', 1, 1))
            if len(calls) == 1:
                raise ValueError('First build failed')
            request.output.mkdir(parents=True)
            return ValidatedPackage(request.output)
        with patch.object(self.window, 'start_task', side_effect=start), patch('minecraft_playden.gui_app.LocalBuildRequest.run', autospec=True, side_effect=run):
            self.window.start_build()
            self.wait_until_idle()
        self.assertEqual(starts, ['0%', '0%'])
        self.assertIn('Failed', self.window.queue_table.item(0, 1).text())
        self.assertIn('Completed', self.window.queue_table.item(1, 1).text())
        self.assertEqual(self.window.progress_bar.text(), '0%')
        self.assertTrue(self.window.build_button.isEnabled())

    def test_queue_summary_opens_parent_then_selected_successful_package(self):
        self.populate([self.sorting_installation(str(i), f'Instance {i}') for i in range(3)])
        self.window.table.selectAll()
        parent = self.root / 'shared'
        self.window.output.setText(str(parent / 'unused'))
        calls = []
        def run(request, progress, cancellation):
            calls.append(request)
            if len(calls) != 2:
                raise ValueError('Fixture failure')
            request.output.mkdir(parents=True)
            return ValidatedPackage(request.output)
        with patch('minecraft_playden.gui_app.LocalBuildRequest.run', autospec=True, side_effect=run):
            self.window.start_build()
            self.wait_until_idle()
        self.batch_completion.assert_called_once()
        summary = self.window.completed_label.text()
        self.assertIn('1 succeeded, 2 failed, 0 cancelled, 0 not started', summary)
        for request in calls:
            self.assertIn(request.installation.name, summary)
            self.assertIn(str(request.output), summary)
        self.assertIn('Fixture failure', summary)
        MainWindow.show_queue_completion(self.window)
        self.assertIsNotNone(self.window.queue_dialog.copy_button)
        self.assertTrue(self.window.queue_dialog.copy_button.isEnabled())
        self.window.queue_dialog.close()
        with patch('minecraft_playden.gui_app.QDesktopServices.openUrl', return_value=True) as opened:
            self.window.open_output_button.click()
            self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), parent.resolve())
            self.window.queue_table.setCurrentCell(1, 1)
            self.window.open_output_button.click()
            self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), calls[1].output.resolve())
            self.window.queue_table.setCurrentCell(0, 1)
            self.assertFalse(self.window.open_output_button.isEnabled())

    def test_zero_success_queue_does_not_open_an_old_package(self):
        old = self.root / 'previous'
        old.mkdir()
        self.window.completed_package = ValidatedPackage(old)
        self.populate([self.sorting_installation(str(i), f'Instance {i}') for i in range(2)])
        self.window.table.selectAll()
        self.window.output.setText(str(self.root / 'not-created/unused'))
        with patch('minecraft_playden.gui_app.LocalBuildRequest.run', side_effect=ValueError('No output')):
            self.window.start_build()
            self.wait_until_idle()
        self.assertIn('0 succeeded, 2 failed', self.window.completed_label.text())
        self.assertFalse(self.window.copy_usb_button.isEnabled())
        self.assertFalse(self.window.open_output_button.isEnabled())
        self.batch_completion.assert_called_once()

    def test_cleanup_cannot_be_enabled_for_validated_existing_package(self):
        good = self.root / 'existing-package'
        good.mkdir()
        self.window.completed_package = ValidatedPackage(good)
        self.window.cleanup_after_usb.setChecked(True)
        drive = DriveTarget(self.root / 'USB', VolumeIdentity('fixture', 'exfat', 0, 0))
        request = TransferRequest(good, drive)
        result = TransferResult(good.resolve(), request.destination, 1, 1, True, (('package.json', 1, '0' * 64),))
        with patch('minecraft_playden.gui_app.TransferRequest.run', return_value=result), patch.object(self.window, 'confirm_cleanup') as confirm:
            self.window.start_transfer(request)
            self.wait_until_idle()
            confirm.assert_not_called()
        self.assertTrue(good.exists())
        self.assertIn('only packages generated in this session', self.window.log.toPlainText())

    def test_cleanup_confirmation_names_exact_local_path_and_defaults_no(self):
        from PySide6.QtWidgets import QMessageBox
        self.assertFalse(self.window.cleanup_after_usb.isChecked())
        with patch('minecraft_playden.gui_app.QMessageBox.exec', return_value=QMessageBox.StandardButton.No), patch('minecraft_playden.gui_app.QMessageBox.setText') as text, patch('minecraft_playden.gui_app.QMessageBox.setTextFormat') as text_format, patch('minecraft_playden.gui_app.QMessageBox.setDefaultButton') as default:
            local = self.root / '<output> & personal'
            self.assertFalse(self.window.confirm_cleanup(local, self.root / 'USB/Pack'))
            self.assertIn(str(local), text.call_args.args[0])
            self.assertIn(str(self.root / 'USB/Pack'), text.call_args.args[0])
            text_format.assert_called_once_with(Qt.TextFormat.PlainText)
            default.assert_called_once_with(QMessageBox.StandardButton.No)

    def test_usb_dialog_passes_explicit_fast_copy_choice_to_request(self):
        from PySide6.QtWidgets import QDialog
        package = self.root / 'Pack'
        package.mkdir()
        self.window.completed_package = ValidatedPackage(package)
        drive = DriveTarget(self.root, VolumeIdentity('fixture', 'exfat', 0, 0))
        with patch('minecraft_playden.gui_app.DriveDialog') as dialog_type, patch.object(self.window, 'start_transfer') as start:
            dialog = dialog_type.return_value
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.target = drive
            for skip in (False, True):
                dialog.skip_verification.isChecked.return_value = skip
                self.window.copy_to_usb()
                self.assertEqual(start.call_args.args[0].verify_after_copy, not skip)

    def test_storage_error_opens_recheck_dialog_and_retry_is_available(self):
        from minecraft_playden.transfer import InsufficientSpace
        good = self.root / 'package'
        good.mkdir()
        usb = self.root / 'usb'
        usb.mkdir()
        request = TransferRequest(good, DriveTarget(usb, VolumeIdentity('fixture', 'exfat', 0, 0)))
        with patch('minecraft_playden.volumes.DriveTarget.check'), patch('minecraft_playden.gui_app.TransferRequest.run', side_effect=InsufficientSpace(100)):
            self.window.start_transfer(request)
            self.wait_until_idle()
        self.assertIsNotNone(self.window.space_dialog)
        self.assertTrue(self.window.retry_usb_button.isEnabled())
        self.assertIn('Insufficient', self.window.status.text())
        self.window.space_dialog.close()


if __name__ == '__main__':
    unittest.main()
