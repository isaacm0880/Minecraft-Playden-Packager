"""First-show sizing and concise labels retain technical and physical evidence."""
from pathlib import Path

import pytest
pytest.importorskip('PySide6')
from PySide6.QtWidgets import QApplication, QLabel

from minecraft_playden.compatibility import Compatibility, Status, REGISTRY, assess
from minecraft_playden.discovery import Installation, DiscoveryResult
from minecraft_playden.modloaders import ModLoader
from test_gui_preferences import preferences


def test_splitter_tooltip_is_only_on_its_handle_and_fabric_text_is_not_duplicated(preferences):
    create, _ = preferences
    window = create()
    assert not window.splitter.toolTip()
    assert 'Drag the divider' in window.splitter.handle(1).toolTip()
    for widget in (window.output, window.build_button, window.prepare_fabric, window.receiver_manager.start_button):
        while widget is not None:
            assert 'Drag the divider' not in widget.toolTip()
            widget = widget.parentWidget()
    explanation = "Prepares Fabric's game files before transfer to avoid first-launch remapping issues on Playden."
    assert window.prepare_fabric.toolTip() == explanation
    assert all(label.text() != explanation for label in window.findChildren(QLabel))


@pytest.mark.parametrize('font_scale', [1., 1.5])
def test_ps5_buttons_have_style_minimum_height_on_first_show(preferences, font_scale):
    create, _ = preferences
    window = create()
    font = window.font()
    font.setPointSizeF(font.pointSizeF() * font_scale)
    window.setFont(font)
    window.show()
    QApplication.instance().processEvents()
    manager = window.receiver_manager
    for button in (manager.start_button, manager.stop_button, manager.refresh_button, manager.cancel_button):
        assert button.height() >= button.minimumSizeHint().height()
        assert button.height() >= button.sizeHint().height()
    # Expansion must update scroll content constraints without a window resize.
    manager.advanced.set_expanded(True)
    QApplication.instance().processEvents()
    for button in (manager.start_button, manager.stop_button, manager.pair_button):
        assert button.height() >= button.minimumSizeHint().height()


@pytest.mark.parametrize('status,label', [(Status.TESTED, 'Works'), (Status.UNTESTED, 'Untested'),
                                         (Status.BROKEN, 'Broken'), (Status.RETEST, 'Broken'),
                                         (Status.CANNOT_PACKAGE, 'Untested')])
def test_display_labels_do_not_change_internal_policies_or_evidence(status, label):
    result = Compatibility(status, 'Detailed configuration evidence')
    assert result.label == label
    assert result.details == f'{status} — Detailed configuration evidence'
    assert result.permits_build == (status in (Status.TESTED, Status.UNTESTED, Status.RETEST))


def test_status_cell_tooltips_keep_evidence_and_unlisted_combinations_stay_untested(preferences, tmp_path):
    create, _ = preferences
    window = create()
    jar = tmp_path / 'client.jar'; jar.write_bytes(b'fixture')
    instances = [Installation('Confirmed', 'Portable', '1.20.1', tmp_path, jar),
                 Installation('Other Fabric', 'Portable', '1.20.1', tmp_path, jar, loaders=(ModLoader('Fabric', '0.99'),))]
    window.operation = 'scan'
    window.task_result(DiscoveryResult(tuple(instances)))
    assert window.table.item(0, 6).text() == 'Works'
    assert 'startup' in window.table.item(0, 6).toolTip()
    assert window.table.item(1, 6).text() == 'Untested'
    before = dict(REGISTRY)
    jar.unlink()
    window.update_build_button()
    assert window.table.item(0, 6).text() == 'Untested'
    assert 'missing' in window.table.item(0, 6).toolTip()
    assert not window.build_button.isEnabled()
    assert REGISTRY == before


def test_mod_tracker_records_known_issues_only():
    guide = (Path(__file__).resolve().parents[1] / 'docs/COMPATIBILITY.md').read_text()
    tracker = guide.split('## Known Mod Compatibility', 1)[1]
    assert 'Ixeris' in tracker
    assert 'only known issues' in tracker and 'unlisted mod' in tracker
    assert '✅ Working' not in tracker
    assert '| Works |' not in tracker
