"""Console evidence never substitutes for technical package validation."""
from dataclasses import replace
from pathlib import Path

import pytest

from minecraft_playden.compatibility import Status, UNTESTED_WARNING, assess
from minecraft_playden.desktop import selection_readiness
from minecraft_playden.discovery import Installation
from minecraft_playden.modloaders import ModLoader


@pytest.mark.parametrize(('version', 'loader', 'status'), [
    ('26.3', ModLoader('Fabric', '0.20.1'), Status.TESTED),
    ('26.3', ModLoader('Fabric', '0.20.2'), Status.UNTESTED),
    ('26.2', ModLoader('Fabric', '0.20.1'), Status.UNTESTED),
    ('1.20.1', ModLoader('Fabric', '0.16.10'), Status.UNTESTED),
    ('1.20.1', ModLoader('Forge', '47'), Status.BROKEN),
    ('26.3', ModLoader('NeoForge', '1'), Status.CANNOT_PACKAGE),
    ('26.3', ModLoader('Quilt', '1'), Status.CANNOT_PACKAGE),
])
def test_explicit_evidence_and_loader_policies(version, loader, status):
    result = assess(version, (loader,))
    assert result.status == status
    assert result.permits_build == (status in (Status.TESTED, Status.UNTESTED))
    if status == Status.UNTESTED:
        assert result.evidence == UNTESTED_WARNING
    if status == Status.TESTED:
        assert 'without Ixeris' in result.evidence
        assert 'not verified' in result.evidence


def test_real_blocker_overrides_tested_evidence():
    assert assess('26.3', (ModLoader('Fabric', '0.20.1'),), 'Missing client').status == Status.CANNOT_PACKAGE
    assert assess('1.20.1', (ModLoader('Forge', '47'),), 'Missing client').status == Status.BROKEN
    assert assess('26.3', ()).status == Status.TESTED
    assert assess('26.2', ()).status == Status.UNTESTED
    assert assess('26.3', (), 'Missing client').status == Status.CANNOT_PACKAGE


def fixture(root: Path, name: str, **kwargs) -> Installation:
    instance = root / name
    instance.mkdir()
    jar = instance / 'client.jar'
    jar.write_bytes(b'fixture client')
    return Installation(name, 'Portable', '26.3', instance, jar, **kwargs)


@pytest.mark.parametrize('blocker', ['Forge', 'NeoForge', 'Quilt', 'missing', 'metadata'])
def test_every_selected_instance_must_pass_and_deselection_reenables(tmp_path, blocker):
    good = fixture(tmp_path, 'Tested', loaders=(ModLoader('Fabric', '0.20.1'),))
    untested = fixture(tmp_path, 'Untested', loaders=(ModLoader('Fabric', '0.16.10'),))
    bad = fixture(tmp_path, 'Blocked')
    if blocker in ('Forge', 'NeoForge', 'Quilt'):
        bad = replace(bad, loaders=(ModLoader(blocker, '1'),))
    elif blocker == 'missing':
        bad.client.unlink()
    else:
        bad = replace(bad, packaging_problem='Unsupported custom format')
    args = str(tmp_path / 'output/unused'), str(tmp_path / 'cache')
    requests, reason = selection_readiness([good, untested, bad], *args)
    assert not requests
    assert '1 of 3' in reason
    assert 'Blocked' in reason
    assert ('Broken' if blocker == 'Forge' else 'Cannot package') in reason
    requests, reason = selection_readiness([good, untested], *args)
    assert len(requests) == 2 and not reason
    assert [r.installation for r in requests] == [good, untested]
    assert not (tmp_path / 'output').exists()


def test_request_validation_errors_also_block_the_entire_queue(tmp_path):
    good = fixture(tmp_path, 'Good')
    other = fixture(tmp_path, 'Other')
    requests, reason = selection_readiness([good, other], str(tmp_path / 'out/unused'), str(other.instance / 'cache'))
    assert not requests
    assert 'Other' in reason and 'separate from the source' in reason


def test_vanilla_evidence_is_separate_from_loader_versions():
    vanilla = assess('26.3', ())
    assert vanilla.status == Status.TESTED
    assert 'Vanilla' in vanilla.evidence
    assert 'does not cover modded configurations' in vanilla.evidence
    assert assess('26.3', (ModLoader('Fabric', '0.20.1'),)).status == Status.TESTED
    assert assess('26.3', (ModLoader('Fabric', '0.20.2'),)).status == Status.UNTESTED
    assert assess('26.2', (ModLoader('Fabric', '0.20.1'),)).status == Status.UNTESTED
    assert assess('26.3', (ModLoader('Forge', '1'),)).status == Status.BROKEN
