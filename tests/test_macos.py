"""macOS API fixtures; no real installation, mounted drive or disk writes."""
import ctypes
import errno
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from minecraft_playden.discovery import default_roots, discover
from minecraft_playden.drive_discovery import external_mounts
from minecraft_playden.macos_storage import external_volume, volume_info
from minecraft_playden.volumes import DriveTarget, DriveUnavailable, _mounted_identity, flush_file, publish_without_replace


def disk(root=Path('/Volumes/USB SSD ü')):
    return {'MountPoint': str(root), 'Mounted': True, 'Internal': False,
            'BusProtocol': 'USB', 'VirtualOrPhysical': 'Physical', 'Removable': False,
            'Writable': True, 'VolumeReadOnly': False, 'MediaReadOnly': False,
            'VolumeUUID': 'fixture-uuid', 'DeviceIdentifier': 'disk4s1', 'FilesystemType': 'exfat'}


class MacDiscoveryTests(unittest.TestCase):
    def test_official_prism_multimc_defaults_and_read_only_discovery(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / 'Mac User ü'
            support = home / 'Library/Application Support'
            official = support / 'minecraft/versions/26.3'
            official.mkdir(parents=True)
            (official / '26.3.json').write_text(json.dumps({'id': '26.3'}))
            (official / '26.3.jar').write_bytes(b'local fixture')
            for launcher in ('PrismLauncher', 'MultiMC'):
                root = support / launcher
                instance = root / 'instances/Vanilla'
                instance.mkdir(parents=True)
                (instance / 'mmc-pack.json').write_text(json.dumps({'components': [{'uid': 'net.minecraft', 'version': '26.3'}]}))
                client = root / 'libraries/com/mojang/minecraft/26.3/minecraft-26.3-client.jar'
                client.parent.mkdir(parents=True)
                client.write_bytes(b'local fixture')
            before = {p: p.read_bytes() for p in home.rglob('*') if p.is_file()}
            with patch('urllib.request.urlopen') as network:
                result = discover(home=home, platform='darwin', environ={})
                network.assert_not_called()
            self.assertEqual(len(result.installations), 3)
            self.assertTrue(all(item.available for item in result.installations))
            self.assertEqual(before, {p: p.read_bytes() for p in home.rglob('*') if p.is_file()})
            self.assertNotIn(home / '.minecraft', [p for _, p in default_roots(home, 'darwin', {})])

    def test_multimc_app_data_and_legacy_bundle_locations(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            for suffix in ('Applications/MultiMC.app/Data', 'Downloads/MultiMC.app/Contents/MacOS'):
                root = home / suffix
                instance = root / 'instances/Vanilla'
                instance.mkdir(parents=True)
                (instance / 'mmc-pack.json').write_text(json.dumps({'components': [{'uid': 'net.minecraft', 'version': '26.3'}]}))
                client = root / 'libraries/com/mojang/minecraft/26.3/minecraft-26.3-client.jar'
                client.parent.mkdir(parents=True)
                client.write_bytes(b'local fixture')
            result = discover(home=home, platform='darwin', environ={})
            self.assertEqual(len(result.installations), 2)
            self.assertTrue(all(item.available for item in result.installations))
            selected = discover([home / 'Applications/MultiMC.app'], home=home, platform='darwin', include_defaults=False)
            self.assertEqual(len(selected.installations), 1)
            self.assertEqual(selected.installations[0].instance, home / 'Applications/MultiMC.app/Data/instances/Vanilla')

    def test_case_aliases_deduplicate_by_filesystem_identity(self):
        from minecraft_playden.discovery import _canonical
        root = Path('/Users/Fixture')
        with patch('pathlib.Path.resolve', autospec=True, side_effect=lambda path: path), patch('pathlib.Path.stat', return_value=SimpleNamespace(st_dev=3, st_ino=42)):
            self.assertEqual(_canonical(root / 'MultiMC'), _canonical(root / 'multimc'))
        with patch('pathlib.Path.resolve', autospec=True, side_effect=lambda path: path), patch('pathlib.Path.stat', side_effect=[SimpleNamespace(st_dev=3, st_ino=42), SimpleNamespace(st_dev=3, st_ino=43)]):
            self.assertNotEqual(_canonical(root / 'MultiMC'), _canonical(root / 'multimc'))

    def test_cache_and_logs_use_macos_user_locations(self):
        from minecraft_playden.downloads import default_cache
        with patch('minecraft_playden.downloads.sys.platform', 'darwin'), patch('pathlib.Path.home', return_value=Path('/Users/Fixture')):
            self.assertEqual(default_cache(), Path('/Users/Fixture/Library/Caches/minecraft-playden'))


class MacStorageTests(unittest.TestCase):
    def test_diskutil_is_bounded_read_only_and_preserves_unicode_spaces(self):
        root = Path('/Volumes/USB SSD ü')
        with patch('minecraft_playden.macos_storage.subprocess.run', return_value=SimpleNamespace(stdout=plistlib.dumps(disk(root)))) as run:
            self.assertEqual(volume_info(root)['VolumeUUID'], 'fixture-uuid')
        self.assertEqual(run.call_args.args[0], ['/usr/sbin/diskutil', 'info', '-plist', str(root)])
        self.assertEqual(run.call_args.kwargs['timeout'], 5)
        for failure in (OSError('permission denied'), subprocess.TimeoutExpired('diskutil', 5)):
            with self.subTest(failure=failure), patch('minecraft_playden.macos_storage.subprocess.run', side_effect=failure), self.assertRaises(OSError):
                volume_info(root)
        with patch('minecraft_playden.macos_storage.subprocess.run', return_value=SimpleNamespace(stdout=b'not a plist')), self.assertRaises(OSError):
            volume_info(root)

    def test_fixed_external_ssd_allowed_internal_network_images_readonly_rejected(self):
        root = Path('/Volumes/USB SSD ü')
        self.assertTrue(external_volume(root, disk(root)))
        for change in ({'Internal': True}, {'BusProtocol': 'Network'}, {'BusProtocol': 'Disk Image'},
                       {'DiskImage': True}, {'VirtualOrPhysical': 'Virtual'}, {'Mounted': False},
                       {'Writable': False}, {'VolumeReadOnly': True}, {'MediaReadOnly': True},
                       {'VolumeUUID': ''}, {'MountPoint': '/'}, {'DeviceIdentifier': ''}):
            with self.subTest(change=change):
                self.assertFalse(external_volume(root, {**disk(root), **change}))
        self.assertFalse(external_volume(Path('/'), disk(Path('/'))))
        self.assertFalse(external_volume(root / 'folder', disk(root / 'folder')))
        self.assertFalse(external_volume(root, {}))

    def test_qt_roots_are_filtered_and_metadata_failure_fails_closed(self):
        root = Path('/Volumes/USB SSD ü')
        with patch('minecraft_playden.drive_discovery.sys.platform', 'darwin'), patch('pathlib.Path.is_symlink', return_value=False), patch('minecraft_playden.macos_storage.volume_info', return_value=disk(root)):
            self.assertEqual(external_mounts([str(root), '/']), {str(root)})
        with patch('minecraft_playden.drive_discovery.sys.platform', 'darwin'), patch('pathlib.Path.is_symlink', return_value=False), patch('minecraft_playden.macos_storage.volume_info', side_effect=OSError('gone')):
            self.assertEqual(external_mounts([str(root)]), set())
        with patch('minecraft_playden.drive_discovery.sys.platform', 'darwin'), patch('pathlib.Path.is_symlink', return_value=True), patch('minecraft_playden.macos_storage.volume_info') as info:
            self.assertEqual(external_mounts([str(root)]), set())
            info.assert_not_called()

    def test_volume_uuid_disconnect_and_permissions_rechecked(self):
        root = Path('/Volumes/USB SSD ü')
        with patch('minecraft_playden.volumes.sys.platform', 'darwin'), patch('pathlib.Path.stat', return_value=SimpleNamespace(st_dev=12, st_ino=34)), patch('minecraft_playden.macos_storage.volume_info', return_value=disk(root)):
            identity = _mounted_identity(root)
            self.assertEqual(identity.filesystem, 'exfat')
            target = DriveTarget(root, identity)
            target.check()
            with patch('minecraft_playden.macos_storage.volume_info', return_value={**disk(root), 'VolumeUUID': 'replacement'}), self.assertRaises(DriveUnavailable):
                target.check()
            with patch('minecraft_playden.macos_storage.volume_info', return_value={**disk(root), 'Writable': False}), self.assertRaises(DriveUnavailable):
                target.check()

    @unittest.skipUnless(os.name == 'posix', 'POSIX API boundary')
    def test_exclusive_rename_never_uses_replace_flag(self):
        source, target = Path('/tmp/source'), Path('/tmp/destination')
        libc = Mock()
        libc.renamex_np.return_value = 0
        with patch('minecraft_playden.volumes.sys.platform', 'darwin'), patch('minecraft_playden.volumes.ctypes.CDLL', return_value=libc):
            publish_without_replace(source, target)
            libc.renamex_np.assert_called_once_with(os.fsencode(source), os.fsencode(target), 4)
            libc.renamex_np.return_value = -1
            with patch('minecraft_playden.volumes.ctypes.get_errno', return_value=errno.EEXIST), self.assertRaises(FileExistsError):
                publish_without_replace(source, target)

    @unittest.skipUnless(os.name == 'posix', 'POSIX fcntl')
    def test_file_flush_asks_for_hardware_cache_sync_and_propagates_errors(self):
        import fcntl
        with patch('minecraft_playden.volumes.sys.platform', 'darwin'), patch('minecraft_playden.volumes.os.fsync') as sync, patch.object(fcntl, 'F_FULLFSYNC', 51, create=True), patch('fcntl.fcntl') as full:
            flush_file(12)
            sync.assert_called_once_with(12)
            full.assert_called_once_with(12, 51)
            full.side_effect = OSError('hardware sync failed')
            with self.assertRaises(OSError):
                flush_file(12)
