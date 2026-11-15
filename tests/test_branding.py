"""Official image preservation, ICO payloads and source/frozen Qt icon loading."""
import ctypes
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from minecraft_playden.branding import ICON_SIZES, configure_desktop_identity, icon_directory

HAS_QT = importlib.util.find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QColor, QColorSpace, QImage
    from PySide6.QtWidgets import QApplication
    from minecraft_playden.branding import application_icon
    from release.generate_icons import contained_image, generate, verify


@unittest.skipUnless(HAS_QT, 'Install .[gui] for icon rendering checks')
class BrandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        cls.app = QApplication.instance() or QApplication([])

    def test_official_source_bytes_and_all_ico_frames_are_preserved(self):
        directory = icon_directory()
        manifest = verify(directory)
        self.assertEqual(hashlib.sha256((directory / 'playdencreeper.png').read_bytes()).hexdigest(),
                         'ee1033d02411db5eafd2e298f8d6b1d68c2c2093a7126690502cec8d2d499380')
        self.assertEqual(manifest['icon_sizes'], list(ICON_SIZES))
        self.assertEqual((manifest['source_width'], manifest['source_height']), (768, 768))
        self.assertTrue(manifest['source_has_alpha'])
        ico = QImage(str(directory / 'playdencreeper.ico'))
        self.assertFalse(ico.isNull())
        self.assertTrue(ico.hasAlphaChannel())

    def test_non_square_input_uses_transparent_padding_and_preserves_colour(self):
        source = QImage(120, 60, QImage.Format.Format_ARGB32)
        source.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.DisplayP3))
        source.fill(QColor(12, 80, 160, 128))
        contained = contained_image(source, 64)
        self.assertEqual(contained.size(), QSize(64, 64))
        self.assertEqual(contained.colorSpace(), source.colorSpace())
        self.assertEqual(contained.pixelColor(32, 0).alpha(), 0)
        self.assertEqual(contained.pixelColor(32, 63).alpha(), 0)
        centre = contained.pixelColor(32, 32)
        self.assertEqual(centre.alpha(), 128)
        for actual, expected in zip(centre.getRgb()[:3], (12, 80, 160)):
            self.assertLessEqual(abs(actual - expected), 1)

    def test_qt_icon_has_exact_pixels_for_every_requested_resolution(self):
        icon = application_icon()
        for size in ICON_SIZES:
            with self.subTest(size=size):
                actual = icon.pixmap(QSize(size, size), 1.0).toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                expected = QImage(str(icon_directory() / f'playdencreeper-{size}.png')).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                self.assertEqual(actual, expected)
                self.assertEqual(actual.pixelColor(0, 0).alpha(), 0)

    def test_frozen_resource_path_is_independent_of_working_directory(self):
        import shutil
        source = icon_directory()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(source, root / 'resources/icons')
            with patch('minecraft_playden.branding.sys._MEIPASS', str(root), create=True):
                self.assertEqual(icon_directory(), root / 'resources/icons')
                self.assertFalse(application_icon().isNull())

    def test_generator_preserves_non_square_original_png_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            image = QImage(80, 40, QImage.Format.Format_ARGB32)
            image.fill(QColor(240, 100, 20, 128))
            original = directory / 'playdencreeper.png'
            image.save(str(original))
            before = original.read_bytes()
            generate(directory)
            verify(directory)
            self.assertEqual(original.read_bytes(), before)


class DesktopIdentityTests(unittest.TestCase):
    def test_windows_taskbar_identity_uses_wide_api_without_elevation(self):
        shell = Mock()
        shell.SetCurrentProcessExplicitAppUserModelID.return_value = 0
        with patch('minecraft_playden.branding.sys.platform', 'win32'), patch.object(ctypes, 'WinDLL', return_value=shell, create=True):
            configure_desktop_identity()
        shell.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with('MinecraftPlayden.Packager')
