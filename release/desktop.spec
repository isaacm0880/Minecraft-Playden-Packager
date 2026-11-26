"""One-folder builds only. Never collect the checkout, caches or installed games."""
import os
import sys
from pathlib import Path
from minecraft_playden import __version__
from release.macos_bundle import bundle_version

root = Path(SPECPATH).parent
stage = Path(os.environ['PLAYDEN_RELEASE_STAGE'])
icon_root = root / 'minecraft_playden/resources/icons'
icon_names = ['playdencreeper.png', 'playdencreeper.ico', 'playdencreeper.icns', 'playdencreeper-512.png', 'icon-manifest.json',
              *[f'playdencreeper-{size}.png' for size in (16, 24, 32, 48, 64, 128, 256)]]
a = Analysis(
    [str(root / 'release/desktop_entry.py')],
    pathex=[str(root)],
    datas=[(str(stage / 'build-metadata.json'), '.'),
           (str(stage / 'receiver/minecraft_playden_receiver.elf'), 'receiver'),
           (str(stage / 'receiver/receiver-manifest.json'), 'receiver'),
           *[(str(icon_root / name), 'resources/icons') for name in icon_names],
           (str(root / 'minecraft_playden/resources/java8/Java8Launch.class'), 'minecraft_playden/resources/java8'),
           (str(root / 'minecraft_playden/resources/remap/FabricRemap.java'), 'minecraft_playden/resources/remap'),
           *[(str(root / 'minecraft_playden/resources/artwork' / name), 'minecraft_playden/resources/artwork')
             for name in ('icon.png', 'background.png')],
           *[(str(root / 'minecraft_playden/resources/artwork/modloaders' / (loader + '.png')),
              'minecraft_playden/resources/artwork/modloaders')
             for loader in ('fabric', 'forge', 'neoforge', 'quilt')],
           (str(root / 'LICENSE'), 'licenses'),
           (str(stage / 'licenses'), 'licenses/third-party')],
    hiddenimports=['PySide6.QtSvg', 'certifi'],
    excludes=['pytest', 'mypy', 'tkinter', 'PySide6.QtTest', 'PySide6.QtWebEngineCore',
              'PySide6.QtWebEngineWidgets', 'PySide6.QtQml', 'PySide6.QtQuick'],
    noarchive=False,
)
if sys.platform.startswith('linux'):
    # Keep glibc and graphics drivers on the host. The supported baseline already
    # supplies GCC 12's C++ runtime; bundling an older copy can break host Mesa.
    host_libraries = ('libstdc++.so', 'libgcc_s.so', 'libfontconfig.so', 'libfreetype.so', 'libexpat.so')
    a.binaries = [entry for entry in a.binaries
                  if not Path(entry[0]).name.startswith(host_libraries)
                  and Path(entry[0]).name != 'libqgtk3.so']
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MinecraftPlaydenPackager',
          debug=False, strip=False, upx=False, console=False,
          target_arch=os.environ.get('PLAYDEN_TARGET_ARCH') if sys.platform == 'darwin' else None,
          icon=str(stage / 'icon.ico') if sys.platform == 'win32' else None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MinecraftPlaydenPackager')

if sys.platform == 'darwin':
    app = BUNDLE(coll, name='Minecraft Playden Packager.app',
                 icon=str(icon_root / 'playdencreeper.icns'),
                 bundle_identifier='io.github.isaacm0880.minecraft-playden-packager',
                 version=bundle_version(__version__),
                 info_plist={'CFBundleVersion': bundle_version(__version__),
                             'CFBundleName': 'Minecraft Playden Packager',
                             'CFBundleDisplayName': 'Minecraft Playden Packager',
                             'NSHighResolutionCapable': True,
                             'NSRemovableVolumesUsageDescription': 'Copy and verify Playden packages on your selected external drive.',
                             'NSDocumentsFolderUsageDescription': 'Read the Minecraft installation or output folder you select.',
                             'NSDownloadsFolderUsageDescription': 'Read a locally installed Minecraft instance you select.'})
