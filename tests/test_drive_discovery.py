"""Device metadata fixtures: no disks mounted, modified or ejected."""
import ctypes
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from minecraft_playden.drive_discovery import linux_external_mounts, windows_external, _windows_storage


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sys = Path(self.temp.name)
        (self.sys / 'dev/block').mkdir(parents=True)
        self.lines = []

    def disk(self, number, name, *, usb=False, removable=False, sd=False, optical=False):
        parent = 'devices/pci/usb2/2-1' if usb else 'devices/pci/mmc_host/mmc0' if sd else 'devices/pci/internal'
        disk = self.sys / parent / name
        disk.mkdir(parents=True)
        (disk / 'removable').write_text('1' if removable else '0')
        if optical:
            (disk / 'device').mkdir()
            (disk / 'device/type').write_text('5')
        part = disk / (name + '1')
        part.mkdir()
        (part / 'partition').write_text('1')
        (self.sys / 'dev/block' / number).symlink_to(part)
        return disk, part

    def mount(self, number, path, *, root='/', filesystem='exfat', options='rw', source='/dev/fixture'):
        self.lines.append(f'{len(self.lines)+1} 0 {number} {root} {path} {options} - {filesystem} {source} rw')

    def detect(self):
        return linux_external_mounts('\n'.join(self.lines), self.sys)

    @unittest.skipIf(os.name == 'nt', 'Linux sysfs fixture requires symlinks')
    def test_usb_ssd_rm_zero_flash_sd_and_internal(self):
        for n, name, options in [('8:1', 'ssd', {'usb': True}), ('8:2', 'flash', {'usb': True, 'removable': True}), ('179:1', 'mmcblk0', {'sd': True}), ('8:3', 'sata', {}), ('259:1', 'nvme0n1', {})]:
            self.disk(n, name, **options)
            self.mount(n, '/media/' + name)
        self.assertEqual(self.detect(), {'/media/ssd', '/media/flash', '/media/mmcblk0'})

    @unittest.skipIf(os.name == 'nt', 'Linux sysfs fixture requires symlinks')
    def test_tmpfs_overlay_network_cloud_and_bind_excluded(self):
        self.disk('8:1', 'usb', usb=True)
        self.mount('8:1', '/run/media/isaac/extnvme')
        for i, fs in enumerate(('tmpfs', 'overlay', 'nfs', 'cifs', 'fuse.rclone', 'fuse.onedrive')):
            self.mount(f'0:{i}', '/fake/' + str(i), filesystem=fs)
        self.mount('8:1', '/bind-subfolder', root='/folder')
        self.assertEqual(self.detect(), {'/run/media/isaac/extnvme'})
        self.mount('8:1', '/bind-full-root')
        self.assertEqual(self.detect(), set())

    @unittest.skipIf(os.name == 'nt', 'Linux sysfs fixture requires symlinks')
    def test_boot_usb_siblings_readonly_and_optical_excluded(self):
        disk, _ = self.disk('8:1', 'bootusb', usb=True)
        second = disk / 'second'
        second.mkdir()
        (second / 'partition').touch()
        (self.sys / 'dev/block/8:2').symlink_to(second)
        self.mount('8:1', '/')
        self.mount('8:2', '/media/boot-sibling')
        self.disk('8:3', 'readonly', usb=True)
        self.mount('8:3', '/media/readonly', options='ro')
        self.disk('8:4', 'cdrom', usb=True, removable=True, optical=True)
        self.mount('8:4', '/media/cdrom')
        self.assertEqual(self.detect(), set())

    @unittest.skipIf(os.name == 'nt', 'Linux sysfs fixture requires symlinks')
    def test_btrfs_source_fallback_and_escaped_spaces(self):
        _, part = self.disk('8:1', 'usb', usb=True)
        (self.sys / 'class/block').mkdir(parents=True)
        (self.sys / 'class/block/sdx1').symlink_to(part)
        self.mount('0:99', '/media/USB\\040SSD', filesystem='btrfs', source='/dev/sdx1')
        self.assertEqual(self.detect(), {'/media/USB SSD'})

    def test_windows_drive_types_and_bus_types(self):
        for kind, bus, expected in [(2, None, True), (3, 7, True), (3, 12, True), (3, 13, True), (3, 4, True), (3, 11, False), (3, 17, False), (3, None, False), (4, 7, False), (5, 7, False), (6, None, False), (0, None, False)]:
            with self.subTest(kind=kind, bus=bus), patch('minecraft_playden.drive_discovery._windows_storage', return_value=(kind, bus)):
                self.assertEqual(windows_external('E:\\', 'C:'), expected)
        with patch('minecraft_playden.drive_discovery._windows_storage') as query:
            self.assertFalse(windows_external('c:/', 'C:'))
            query.assert_not_called()

    def test_missing_metadata_fails_closed(self):
        self.mount('8:99', '/media/unknown')
        self.lines.append('malformed entry')
        self.assertEqual(self.detect(), set())

    def test_windows_native_query_reads_usb_bus_and_closes_handle(self):
        kernel = Mock()
        kernel.GetDriveTypeW.return_value = 3
        kernel.CreateFileW.return_value = 123
        def ioctl(handle, code, query, query_size, output, output_size, returned, overlapped):
            self.assertEqual(code, 0x2D1400)
            self.assertEqual(query.raw, bytes(12))
            struct.pack_into('<I', output, 28, 7)
            returned._obj.value = 36
            return 1
        kernel.DeviceIoControl.side_effect = ioctl
        with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True):
            self.assertEqual(_windows_storage('E:/'), (3, 7))
        kernel.GetDriveTypeW.assert_called_once_with('E:\\')
        self.assertEqual(kernel.CreateFileW.call_args.args[0], '\\\\.\\E:')
        kernel.CloseHandle.assert_called_once_with(123)
        self.assertEqual(kernel.CreateFileW.call_args.args[1], 0)

    def test_windows_unavailable_metadata_and_network_fail_closed(self):
        kernel = Mock()
        kernel.GetDriveTypeW.return_value = 4
        with patch.object(ctypes, 'WinDLL', return_value=kernel, create=True):
            self.assertEqual(_windows_storage('Z:\\'), (4, None))
        kernel.CreateFileW.assert_not_called()
        with patch('minecraft_playden.drive_discovery._windows_storage', side_effect=OSError('disconnected')):
            self.assertFalse(windows_external('E:\\', 'C:'))
