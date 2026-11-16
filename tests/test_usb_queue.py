"""The latest build catalog, not a last-selected package, determines USB work."""
from dataclasses import replace

from minecraft_playden.desktop import BuildRecord, LocalBuildRequest, ValidatedPackage
from minecraft_playden.discovery import Installation
from minecraft_playden.usb_queue import transfer_candidates, plan_transfers, usb_summary
from minecraft_playden.volumes import DriveTarget, VolumeIdentity


def records(tmp_path, count=3):
    result = []
    for index in range(count):
        source = tmp_path / f'source-{index}'
        source.mkdir()
        output = tmp_path / f'Pack-{index}'
        output.mkdir()
        installation = Installation(f'Instance {index}', 'Fixture', '26.3', source, source / 'client.jar')
        request = LocalBuildRequest(installation, output, tmp_path / 'cache')
        result.append(BuildRecord(request, state='completed', package=ValidatedPackage(output)))
    return result


def test_catalog_uses_every_latest_success_even_after_selecting_one(tmp_path):
    builds = records(tmp_path)
    candidates = transfer_candidates(builds, builds[0].package, True)
    assert [c.source for c in candidates] == [r.request.output for r in builds]
    assert all(not c.skip_reason for c in candidates)
    assert len(transfer_candidates(builds[:1], builds[-1].package, True)) == 1
    builds[0].state = 'failed'
    assert transfer_candidates(builds[:1], builds[-1].package, True)[0].skip_reason
    assert len(transfer_candidates(builds, builds[-1].package, False)) == 1
    assert transfer_candidates([], None, False) == []


def test_skips_explain_build_and_filesystem_states(tmp_path):
    builds = records(tmp_path, 6)
    for record, state in zip(builds, ['failed', 'cancelled', 'not started']):
        record.state, record.package = state, None
    builds[3].removed = True
    builds[4].request.output.rmdir()
    builds[5].package = replace(builds[5].package, kind='instance-import')
    candidates = transfer_candidates(builds, None, True)
    assert all(c.skip_reason for c in candidates)
    for candidate, expected in zip(candidates, ['failed', 'cancelled', 'not started', 'removed', 'no longer exists', 'File-only']):
        assert expected in candidate.skip_reason
    usb = tmp_path / 'USB'
    usb.mkdir()
    drive = DriveTarget(usb, VolumeIdentity('fixture', 'exfat', 0, 0))
    plan = plan_transfers(candidates, drive, True)
    assert all(record.state == 'skipped' and record.request is None for record in plan)
    summary = usb_summary(plan)
    for candidate in candidates:
        assert candidate.name in summary and candidate.skip_reason in summary


def test_destination_planning_is_unique_case_insensitive_and_never_overwrites(tmp_path):
    builds = records(tmp_path)
    builds[1].request.output.rmdir()
    builds[1].request = replace(builds[1].request, output=tmp_path / 'nested' / 'pack-0')
    builds[1].request.output.mkdir(parents=True)
    builds[1].package = ValidatedPackage(builds[1].request.output)
    usb = tmp_path / 'USB'
    existing = usb / 'playden/import/PACK-0'
    existing.mkdir(parents=True)
    (existing / 'keep').write_text('unrelated')
    drive = DriveTarget(usb, VolumeIdentity('fixture', 'exfat', 0, 0))
    plan = plan_transfers(transfer_candidates(builds, None, True), drive, False)
    destinations = [record.request.destination for record in plan]
    assert len({p.name.casefold() for p in destinations}) == 3
    assert all(not path.exists() for path in destinations)
    assert all(not record.request.verify_after_copy for record in plan)
    assert (existing / 'keep').read_text() == 'unrelated'
