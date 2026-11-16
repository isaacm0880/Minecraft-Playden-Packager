"""The benchmark uses offline fixtures and loopback receivers, never a console."""
import json
import shutil
from pathlib import Path

import pytest

from tools.lan_benchmark import fingerprint, main, prepare
from test_network_receiver import receiver_binary
from test_network_transfer import built_package
from minecraft_playden.validator import validate


def test_prepared_datasets_are_valid_repeatable_and_never_reused(tmp_path):
    dataset = tmp_path / 'dataset'
    prepare(dataset, 1, 2)
    for kind in ('large', 'small'):
        package = dataset / kind / 'Minecraft Package'
        assert validate(package) == []
        assert fingerprint(package) == fingerprint(package)
    with pytest.raises(FileExistsError):
        prepare(dataset, 1, 2)
    large = dataset / 'large/Minecraft Package/instances/default/minecraft/benchmark/large.bin'
    original = fingerprint(large.parents[4])
    with large.open('ab') as handle:
        handle.write(b'changed')
    assert fingerprint(large.parents[4]) != original


@pytest.mark.parametrize('relative_scratch', [False, True])
def test_loopback_benchmark_emits_numeric_records_and_preserves_source(
        tmp_path, built_package, receiver_binary, monkeypatch, capsys, relative_scratch):
    dataset = tmp_path / 'dataset'
    package = dataset / 'large/Minecraft Package'
    shutil.copytree(built_package, package)
    before = fingerprint(package)
    output = tmp_path / 'result.jsonl'
    options = []
    if relative_scratch:
        (tmp_path / 'scratch').mkdir()
        monkeypatch.chdir(tmp_path)
        options = ['--scratch-dir', 'scratch']
    monkeypatch.setattr('sys.argv', ['benchmark', 'loopback', str(dataset), '--datasets', 'large',
                                    '--repeat', '1', '--output', str(output),
                                    '--receiver-binary', str(receiver_binary), *options])
    main()
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record['mode'] == 'loopback' and record['dataset_sha256'] == before
    assert record['sender']['published'] == record['receiver']['published'] == 1
    assert record['sender']['bytes'] == record['receiver']['bytes'] == record['bytes']
    assert 'token' not in output.read_text() and str(package) not in output.read_text()
    assert fingerprint(package) == before
