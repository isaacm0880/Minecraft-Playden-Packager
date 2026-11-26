"""Exact-source receiver candidate and desktop bundling guards."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from release import receiver
from minecraft_playden.operations import CancellationToken
from minecraft_playden.receiver_manager import BOOT_MARKER, ReceiverConfiguration, ReceiverError, ensure_receiver_elf


def test_receiver_manifest_rejects_other_source_and_changed_elf(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    elf = bundle / receiver.ELF
    elf.write_bytes(b'fixture ELF')
    files = {'ps5/receiver/main.c': 'a' * 64}
    manifest = {'schema': 1, 'source_commit': 'b' * 40, 'source_files': files,
                'receiver_source_sha256': receiver.source_digest(files),
                'sdk_archive_sha256': receiver.SDK_SHA256, 'build_options': receiver.OPTIONS,
                'elf_sha256': receiver.digest(elf), 'elf_size': elf.stat().st_size}
    (bundle / receiver.MANIFEST).write_text(json.dumps(manifest))
    with patch.object(receiver, 'source_files', return_value=files), patch.object(receiver, 'prepare_elf'):
        assert receiver.verify(root, bundle, 'b' * 40) == manifest
        with pytest.raises(ValueError, match='exact source'):
            receiver.verify(root, bundle, 'c' * 40)
        elf.write_bytes(b'changed ELF')
        with pytest.raises(ValueError, match='exact source'):
            receiver.verify(root, bundle, 'b' * 40)


def test_pinned_receiver_build_is_reproducible_and_manifest_matches_checkout(tmp_path):
    archive = Path('/tmp/playden-ps5-sdk-v0.45.zip')
    if not archive.is_file():
        pytest.skip('Pinned SDK archive is not locally available')
    root = Path(__file__).resolve().parents[1]
    result = receiver.build(root, tmp_path / 'receiver', archive, preview=True)
    assert receiver.verify(root, tmp_path / 'receiver', result['source_commit']) == result
    assert result['sdk_archive_sha256'] == receiver.digest(archive)


def test_bundled_receiver_default_and_runtime_checksum_guard(tmp_path, monkeypatch):
    base = tmp_path / 'frozen'
    bundled = base / 'receiver'
    bundled.mkdir(parents=True)
    elf = bundled / receiver.ELF
    elf.write_bytes(b'\x7fELF\x02' + BOOT_MARKER + bytes(388))
    (bundled / receiver.MANIFEST).write_text(json.dumps({'schema': 1, 'elf_sha256': receiver.digest(elf),
                                                           'elf_size': elf.stat().st_size,
                                                           'source_commit': 'b' * 40,
                                                           'source_dirty': False}))
    (base / 'build-metadata.json').write_text(json.dumps({'source_commit': 'b' * 40}))
    monkeypatch.setattr('sys._MEIPASS', str(base), raising=False)
    class EmptySettings:
        def value(self, key, default):
            return default
    config = ReceiverConfiguration.load(settings=EmptySettings())
    assert config.elf == elf
    ensure_receiver_elf(tmp_path / 'no-sdk', elf, CancellationToken())
    elf.write_bytes(elf.read_bytes()[:-1] + b'x')
    with pytest.raises(ReceiverError, match='checksum'):
        ensure_receiver_elf(tmp_path / 'no-sdk', elf, CancellationToken())
