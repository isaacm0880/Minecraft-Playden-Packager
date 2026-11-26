"""Bundle/architecture audit fixtures; never claim to build or execute macOS here."""
import hashlib
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch
from release.macos_bundle import BUNDLE_ID, bundle_version, verify_bundle


class MacBundleTests(unittest.TestCase):
    def test_identity_icon_native_architecture_and_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / 'Minecraft Playden Packager.app'
            resources = app / 'Contents/Resources'
            resources.mkdir(parents=True)
            executable = app / 'Contents/MacOS/MinecraftPlaydenPackager'
            executable.parent.mkdir()
            executable.write_bytes(b'\xcf\xfa\xed\xfe' + b'fixture')
            (resources / 'playdencreeper.icns').write_bytes(b'fixture icon')
            metadata = {'architecture': 'arm64', 'version': '0.1.0',
                        'branding': {'files': {'playdencreeper.icns': hashlib.sha256(b'fixture icon').hexdigest()}}}
            info = {'CFBundleIdentifier': BUNDLE_ID, 'CFBundleDisplayName': 'Minecraft Playden Packager',
                    'CFBundleShortVersionString': '0.1.0', 'CFBundleVersion': '0.1.0',
                    'CFBundleExecutable': executable.name,
                    'CFBundleIconFile': 'playdencreeper.icns', 'NSRemovableVolumesUsageDescription': 'fixture',
                    'NSDocumentsFolderUsageDescription': 'fixture', 'NSDownloadsFolderUsageDescription': 'fixture'}
            path = app / 'Contents/Info.plist'
            path.write_bytes(plistlib.dumps(info))
            with patch('release.macos_bundle.subprocess.check_output', return_value='arm64\n'):
                result = verify_bundle(app, metadata)
                self.assertTrue(result['icon_verified'])
                self.assertFalse(result['notarized'])
            with patch('release.macos_bundle.subprocess.check_output', return_value='x86_64\n'), self.assertRaisesRegex(ValueError, 'architecture'):
                verify_bundle(app, metadata)
            path.write_bytes(plistlib.dumps({**info, 'CFBundleIdentifier': 'wrong.id'}))
            with self.assertRaisesRegex(ValueError, 'identity'):
                verify_bundle(app, metadata)
            path.write_bytes(plistlib.dumps(info))
            (resources / 'playdencreeper.icns').write_bytes(b'wrong icon')
            with self.assertRaisesRegex(ValueError, 'official ICNS'):
                verify_bundle(app, metadata)

    def test_beta_package_version_uses_numeric_apple_bundle_fields(self):
        self.assertEqual(bundle_version('0.4.0b2'), '0.4.0')
        with self.assertRaises(ValueError):
            bundle_version('0.4.0-beta.2')
