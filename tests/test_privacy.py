"""Known secret cleanup operates on staging, preserving ordinary game content."""
import json
import shutil
from pathlib import Path

import pytest

from minecraft_playden.desktop import sanitise_name, unique_output
from minecraft_playden.operations import CancellationToken, OperationCancelled
from minecraft_playden.privacy import cleanup


@pytest.mark.parametrize(('name', 'expected'), [
    ('My Prism Pack', 'My Prism Pack'), ('../A:B?*', '.._A_B__'),
    ('CON.txt', '_CON.txt'), ('nul', '_nul'), (' . ', 'Minecraft-Instance'),
    ('A\x00B/End. ', 'A_B_End'), ('a' * 200, 'a' * 120),
])
def test_portable_instance_names(name, expected):
    assert sanitise_name(name) == expected


def test_collisions_include_case_names_symlinks_and_queue(tmp_path):
    (tmp_path / 'PACK').mkdir()
    (tmp_path / 'Pack-2').symlink_to(tmp_path / 'absent', target_is_directory=True)
    assert unique_output(tmp_path / 'Pack', {tmp_path / 'pack-3'}) == tmp_path / 'Pack-4'


def test_cleanup_preserves_source_worlds_and_ordinary_configs(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    files = {
        'config/normal.json': b'{"volume": 42, "tokenCount": 5}',
        'config/accounts.json': b'{"modSetting": true}',
        'config/auth.json': b'{"accessToken":"private", "nested":[{"uuid":"private", "volume":5}]}',
        'config/auth.yaml': b'password: |\n  secret-on-another-line\nsetting: yes\n',
        'config/auth.toml': b'api_key = "private"\n',
        'config/auth.ini': b'[auth]\nrefresh_token=private\n',
        'config/broken.json': b'{"clientToken":"private",',
        'usercache.json': b'[{"name":"private"}]',
        'servers.dat': b'personal address',
        'webcache/Local Storage/data': b'private',
        'saves/World/level.dat': b'world-binary-data',
        'mods/mod.jar': b'jar-data',
    }
    for relative, data in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    staged = tmp_path / 'staged'
    shutil.copytree(source, staged)
    cleanup(staged)
    for relative in ['config/normal.json', 'config/accounts.json', 'saves/World/level.dat', 'mods/mod.jar']:
        assert (staged / relative).read_bytes() == files[relative]
    assert json.loads((staged / 'config/auth.json').read_text()) == {'nested': [{'volume': 5}]}
    for relative in ['usercache.json', 'servers.dat', 'webcache', 'config/auth.yaml', 'config/auth.toml', 'config/auth.ini', 'config/broken.json']:
        assert not (staged / relative).exists()
    assert {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob('*') if p.is_file()} == files


def test_cleanup_can_be_cancelled(tmp_path):
    (tmp_path / 'file.json').write_text('{"token":"secret"}')
    token = CancellationToken()
    token.cancel()
    with pytest.raises(OperationCancelled):
        cleanup(tmp_path, token)
    assert (tmp_path / 'file.json').exists()
