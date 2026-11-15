"""Destructive operations use generated fixtures and simulated USB volumes only."""
import shutil
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from minecraft_playden.cleanup import GeneratedOutput, snapshot
from minecraft_playden.operations import CancellationToken, OperationCancelled
from minecraft_playden.transfer import TransferRequest
from minecraft_playden.volumes import DriveTarget, VolumeIdentity


@pytest.fixture
def copied(tmp_path):
    original = tmp_path / 'original-minecraft'
    original.mkdir()
    (original / 'world.dat').write_bytes(b'original world')
    parent = tmp_path / 'packages'
    package = parent / 'Generated'
    package.mkdir(parents=True)
    (package / 'package.json').write_text('{"fixture":true}')
    (package / 'world.dat').write_bytes(b'copied world')
    (package / 'empty').mkdir()
    (parent / 'unrelated.txt').write_bytes(b'keep')
    usb = tmp_path / 'USB'
    usb.mkdir()
    info = usb.stat()
    volume = VolumeIdentity('fixture', 'exfat', info.st_dev, info.st_ino)
    receipt = GeneratedOutput.capture(package, (original,))
    with patch('minecraft_playden.volumes._mounted_identity', return_value=volume), patch('minecraft_playden.transfer.validate', return_value=[]), patch('minecraft_playden.transfer.flush_volume', side_effect=lambda drive: drive.check()), patch('minecraft_playden.transfer.allocation_unit', return_value=4096):
        request = TransferRequest(package, DriveTarget.capture(usb))
        result = request.run()
        yield receipt, request, result, original


def test_confirmed_cleanup_removes_only_generated_child_and_retains_usb_and_source(copied):
    receipt, request, result, original = copied
    assert result.verified and result.checksums
    usb = snapshot(result.destination)
    assert receipt.remove_verified(request, result, confirmed=True) == receipt.path
    assert not receipt.path.exists()
    assert receipt.parent.is_dir()
    assert (receipt.parent / 'unrelated.txt').read_bytes() == b'keep'
    assert snapshot(result.destination) == usb
    assert (original / 'world.dat').read_bytes() == b'original world'


@pytest.mark.parametrize('problem', ['unconfirmed', 'skipped-request', 'unverified-result', 'no-checksums',
    'partial-checksums', 'wrong-source', 'wrong-destination', 'wrong-count', 'changed-local',
    'added-file', 'changed-usb', 'removed-usb', 'protected-source', 'parent-path', 'outside-root', 'traversal'])
def test_unsafe_or_incomplete_copy_never_deletes_local_or_original(copied, problem):
    receipt, request, result, original = copied
    confirmed = True
    protected = ()
    local = receipt.path
    if problem == 'unconfirmed':
        confirmed = False
    elif problem == 'skipped-request':
        request = replace(request, verify_after_copy=False)
    elif problem == 'unverified-result':
        result = replace(result, verified=False)
    elif problem == 'no-checksums':
        result = replace(result, checksums=())
    elif problem == 'partial-checksums':
        result = replace(result, checksums=result.checksums[:-1])
    elif problem == 'wrong-source':
        result = replace(result, source=original)
    elif problem == 'wrong-destination':
        result = replace(result, destination=original)
    elif problem == 'wrong-count':
        result = replace(result, files=result.files + 1)
    elif problem == 'changed-local':
        (local / 'world.dat').write_bytes(b'new local world')
    elif problem == 'added-file':
        (local / 'unrelated-added.txt').write_text('do not delete')
    elif problem == 'changed-usb':
        (result.destination / 'world.dat').write_bytes(b'damaged')
    elif problem == 'removed-usb':
        shutil.rmtree(result.destination)
    elif problem == 'protected-source':
        protected = (local,)
    elif problem == 'parent-path':
        receipt = replace(receipt, path=receipt.parent)
    elif problem == 'outside-root':
        receipt = replace(receipt, path=original)
    elif problem == 'traversal':
        receipt = replace(receipt, path=local / '..' / local.name)
    with pytest.raises((ValueError, OSError)):
        receipt.remove_verified(request, result, confirmed=confirmed, protected=protected)
    assert local.is_dir()
    assert (original / 'world.dat').read_bytes() == b'original world'
    assert (local.parent / 'unrelated.txt').read_bytes() == b'keep'


def test_same_size_tampering_with_restored_timestamps_is_detected(copied):
    import os
    receipt, request, result, original = copied
    path = receipt.path / 'world.dat'
    before = path.stat()
    path.write_bytes(b'x' * before.st_size)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError):
        receipt.remove_verified(request, result, confirmed=True)
    assert path.exists()
    assert (original / 'world.dat').read_bytes() == b'original world'


@pytest.mark.parametrize('where', ['root', 'child', 'parent'])
def test_symlinks_and_replaced_directories_cannot_redirect_cleanup(copied, where):
    receipt, request, result, original = copied
    local = receipt.path
    try:
        if where == 'root':
            local.rename(local.with_name('retained'))
            local.symlink_to(original, target_is_directory=True)
        elif where == 'child':
            (local / 'linked').symlink_to(original, target_is_directory=True)
        else:
            alias = local.parent.with_name('alias')
            alias.symlink_to(local.parent, target_is_directory=True)
            receipt = replace(receipt, path=alias / local.name)
    except OSError:
        pytest.skip('Host cannot create test symlinks')
    with pytest.raises((ValueError, OSError)):
        receipt.remove_verified(request, result, confirmed=True)
    assert (original / 'world.dat').read_bytes() == b'original world'
    assert local.exists()


def test_replacing_package_with_new_directory_is_refused(copied):
    receipt, request, result, original = copied
    old = receipt.path.with_name('retained')
    receipt.path.rename(old)
    shutil.copytree(old, receipt.path)
    with pytest.raises(ValueError):
        receipt.remove_verified(request, result, confirmed=True)
    assert old.exists() and receipt.path.exists()


@pytest.mark.parametrize('when', ['before', 'checks'])
def test_cancelled_cleanup_keeps_both_copies(copied, when):
    receipt, request, result, original = copied
    token = CancellationToken()
    if when == 'before':
        token.cancel()
    with pytest.raises(OperationCancelled):
        receipt.remove_verified(request, result, confirmed=True, cancellation=token,
                                progress=lambda event: token.cancel())
    assert receipt.path.exists() and result.destination.exists()


def test_disconnection_and_failed_removal_keep_source_and_usb(copied):
    receipt, request, result, original = copied
    with patch.object(DriveTarget, 'check', side_effect=OSError('Disconnected')), pytest.raises(OSError):
        receipt.remove_verified(request, result, confirmed=True)
    assert receipt.path.exists()
    with patch('minecraft_playden.cleanup.shutil.rmtree', side_effect=PermissionError('Denied')), pytest.raises(PermissionError):
        receipt.remove_verified(request, result, confirmed=True)
    assert receipt.path.exists() and result.destination.exists()
    assert (original / 'world.dat').read_bytes() == b'original world'
    assert not list(receipt.parent.glob('.playden-cleanup-*'))
