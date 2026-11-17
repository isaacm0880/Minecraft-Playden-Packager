"""User-facing build defaults, folder naming and shared throughput estimates."""
from pathlib import Path
from unittest.mock import patch

import pytest

from minecraft_playden.transfer_timing import ThroughputEstimator, eta_text


def test_estimate_warmup_smoothing_display_interval_and_sustained_change():
    estimate = ThroughputEstimator()
    assert estimate.update(0, 10000, 0).eta is None
    assert estimate.update(100, 10000, 1).eta is None
    assert estimate.update(200, 10000, 2).eta is None
    steady = estimate.update(300, 10000, 3)
    assert steady.rate == 100 and steady.eta == 97
    assert estimate.update(350, 10000, 3.1) == steady
    burst = estimate.update(1300, 10000, 4)
    assert 100 < burst.rate < 1000
    for second in range(5, 26):
        sustained = estimate.update(1300 + (second - 4) * 1000, 100000, second)
    assert sustained.rate > 900


def test_stall_recovery_completion_and_rewind():
    estimate = ThroughputEstimator()
    for second in range(5):
        estimate.update(second * 100, 10000, second)
    stalled = estimate.update(400, 10000, 10)
    assert stalled.stalled and stalled.eta is None and stalled.rate == 0
    assert not estimate.update(500, 10000, 11).stalled
    assert estimate.update(10000, 10000, 12).eta == 0
    assert estimate.update(0, 10000, 13).eta is None
    assert estimate.update(0, None, 14).eta is None


@pytest.mark.parametrize('seconds,expected', [(None, 'estimating…'), (float('inf'), 'estimating…'),
                                             (0, 'finishing…'), (1, 'about 5s'), (17, 'about 20s'),
                                             (62, 'about 2 min'), (3601, 'about 2 h')])
def test_eta_avoids_false_precision(seconds, expected):
    assert eta_text(seconds) == expected


def test_advanced_groups_collapsed_and_options_persist(preferences):
    create, read = preferences
    first = create()
    assert not hasattr(first, 'ps5_name')
    for group in (first.build_advanced, first.transfer_advanced, first.local_management):
        assert group.content.isHidden()
        group.toggle.click()
        assert not group.content.isHidden()
    assert first.allow_runtime_download.isChecked()
    assert not first.offline.isChecked()
    first.allow_runtime_download.setChecked(False)
    first.offline.setChecked(True)
    first.privacy_cleanup.setChecked(False)
    first.ps5_pipeline.setChecked(True)
    second = create()
    assert not second.allow_runtime_download.isChecked()
    assert second.offline.isChecked()
    assert not second.privacy_cleanup.isChecked()
    assert second.ps5_pipeline.isChecked()
    assert not second.cleanup_after_usb.isChecked()  # Destructive opt-in is per session.
    assert all(group.content.isHidden() for group in (second.build_advanced, second.transfer_advanced, second.local_management))


def test_switching_instance_resets_leaf_and_preserves_output_parent(preferences, tmp_path):
    from minecraft_playden.discovery import Installation, DiscoveryResult
    create, _ = preferences
    window = create()
    instances = []
    for name in ('First Fabric', 'Second Vanilla'):
        source = tmp_path / name
        source.mkdir()
        client = source / 'client.jar'
        client.write_bytes(b'client')
        instances.append(Installation(name, 'Prism', '26.3', source, client))
    window.operation = 'scan'
    window.task_result(DiscoveryResult(tuple(instances)))
    parent = tmp_path / 'output'
    window.output.setText(str(parent / 'Custom First'))
    window.output.textEdited.emit(window.output.text())
    window.table.selectRow(1)
    assert Path(window.output.text()) == parent / 'Second Vanilla'
    window.output.setText(str(parent / 'Renamed Second'))
    window.output.textEdited.emit(window.output.text())
    window.selection_changed()
    assert Path(window.output.text()).name == 'Renamed Second'
    requests, reason = window.build_requests()
    assert len(requests) == 1, reason
    assert requests[0].allow_runtime_download and not requests[0].offline
    window.offline.setChecked(True)
    assert window.build_requests()[0][0].offline
    # Launcher profiles may share one game directory but have distinct names.
    from dataclasses import replace
    window.operation = 'scan'
    window.task_result(DiscoveryResult((replace(instances[1], name='Another profile', version='1.21.1'),)))
    assert Path(window.output.text()) == parent / 'Another profile'



def test_runtime_http_error_is_actionable_and_no_wrong_java_substitution(tmp_path):
    from minecraft_playden.downloads import Downloader
    from minecraft_playden import runtime
    downloader = Downloader(tmp_path / 'cache', allow_runtime_download=True)
    with patch.object(downloader, 'json', side_effect=OSError('connection unavailable')):
        with pytest.raises(ValueError, match='Runtime download failed; retry the build') as failure:
            runtime.install({'javaVersion': {'majorVersion': 17}}, downloader, tmp_path / 'java')
    assert 'Java 17' in str(failure.value) and 'connection unavailable' in str(failure.value)
    assert not (tmp_path / 'java').exists()


# Reuse the isolated Qt settings fixture; no native user preferences are touched.
from test_gui_preferences import preferences


@pytest.mark.parametrize('options,allowed,offline', [([], True, False),
                                                   (['--no-runtime-download'], False, False),
                                                   (['--offline'], True, True)])
def test_cli_runtime_download_default_and_offline_override(tmp_path, monkeypatch, options, allowed, offline):
    from minecraft_playden.cli import main
    monkeypatch.setattr('sys.argv', ['packager', '--cache', str(tmp_path / 'cache'), 'build',
                                    '--source', 'local', '--client-jar', str(tmp_path / 'client.jar'),
                                    '--version', '26.3', '--output', str(tmp_path / 'package'), *options])
    with patch('minecraft_playden.cli.build') as build:
        assert main() == 0
    downloader = build.call_args.args[1]
    assert downloader.allow_runtime_download == allowed
    assert downloader.offline == offline
