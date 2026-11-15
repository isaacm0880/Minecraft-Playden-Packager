"""Platform API boundaries; no real USB device or privileged utilities used."""
import ctypes
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from minecraft_playden.volumes import DriveTarget, VolumeIdentity, DriveUnavailable, _mounted_identity, _move_windows, flush_volume, publish_without_replace, replace_private


class VolumeTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'Linux mountinfo fixture')
    def test_linux_mount_id_change_and_unmounted_folder_are_detected(self):
        with tempfile.TemporaryDirectory(prefix='USB With Space ') as temp:
            root = Path(temp).resolve()
            escaped = str(root).replace(' ', '\\040')
            line = f'42 1 8:1 / {escaped} rw - exfat /dev/fixture rw\n'
            with patch('minecraft_playden.volumes.sys.platform', 'linux'), patch('pathlib.Path.open', return_value=io.StringIO(line)):
                identity = _mounted_identity(root)
            self.assertEqual(identity.filesystem, 'exfat')
            target = DriveTarget(root, identity)
            changed = VolumeIdentity('different mount', 'exfat', identity.device, identity.inode)
            with patch('minecraft_playden.volumes._mounted_identity', return_value=changed), self.assertRaises(DriveUnavailable):
                target.check()
            with patch('minecraft_playden.volumes.sys.platform', 'linux'), patch('pathlib.Path.open', return_value=io.StringIO('')), self.assertRaises(DriveUnavailable):
                _mounted_identity(root)

    def test_windows_publication_does_not_request_replace_existing(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'a', Path(temp) / 'b'
            with patch('minecraft_playden.volumes.os.name', 'nt'), patch('minecraft_playden.volumes._move_windows') as move:
                publish_without_replace(source, target)
                move.assert_called_once_with(source, target, 0x8)
            with patch('minecraft_playden.volumes.os.name', 'nt'), patch('minecraft_playden.volumes._move_windows') as move:
                replace_private(source, target)
                move.assert_called_once_with(source, target, 0x9)

    def test_windows_move_uses_wide_api_and_propagates_failure(self):
        calls = []
        def move(source, target, flags):
            calls.append((source, target, flags))
            return 1
        kernel = SimpleNamespace(MoveFileExW=move)
        with tempfile.TemporaryDirectory() as temp, patch.object(ctypes, 'WinDLL', return_value=kernel, create=True):
            _move_windows(Path(temp) / 'source', Path(temp) / 'target', 0x8)
        self.assertEqual(calls[0][2], 0x8)
        self.assertTrue(calls[0][0].startswith('\\\\?\\'))
