"""Build on the target OS. Refuse stale/dirty releases; isolated previews are explicit."""
import argparse
import hashlib
from importlib import metadata as packages
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from .audit import audit_tree
from .public_audit import audit_source
from .provenance import check_source, write_json
from .generate_icons import verify as verify_icons
from minecraft_playden.branding import ICON_SIZES
from minecraft_playden import __version__


def run(args, **kwargs) -> None:
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def download(spec: dict, target: Path) -> None:
    request = urllib.request.Request(spec['url'], headers={'Accept': 'application/octet-stream',
                                                        'User-Agent': 'Minecraft-Playden-Packager-build'})
    with urllib.request.urlopen(request, timeout=120) as response, target.open('xb') as stream:
        shutil.copyfileobj(response, stream)
    if hashlib.sha256(target.read_bytes()).hexdigest() != spec['sha256']:
        raise ValueError(f'Build-tool checksum mismatch: {target.name}')
    target.chmod(0o755)


def icons(root: Path, stage: Path) -> dict:
    directory = root / 'minecraft_playden/resources/icons'
    manifest = verify_icons(directory)
    shutil.copyfile(directory / 'playdencreeper-256.png', stage / 'icon.png')
    shutil.copyfile(directory / 'playdencreeper.ico', stage / 'icon.ico')
    return manifest


def licenses(root: Path, stage: Path) -> None:
    destination = stage / 'licenses'
    destination.mkdir()
    shutil.copytree(root / 'release/licenses', destination / 'Qt')
    if sys.platform == 'linux':
        native = destination / 'Linux-native-notices'
        native.mkdir()
        for copyright_file in Path('/usr/share/doc').glob('*/copyright'):
            if copyright_file.is_file():
                shutil.copyfile(copyright_file, native / (copyright_file.parent.name + '.txt'))
    notices = []
    for name in ('PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'certifi', 'PyInstaller', 'cryptography', 'cffi', 'pycparser'):
        distribution = packages.distribution(name)
        headers = distribution.metadata.json
        notices.append(f'{name} {distribution.version}\n{headers.get("license_expression") or headers.get("license", "See bundled license files")}')
        for file in distribution.files or []:
            if any(part.casefold().startswith(('license', 'copying', 'copyright')) for part in file.parts):
                source = Path(str(distribution.locate_file(file)))
                if source.is_file():
                    target = destination / name / str(file).replace('../', '')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
    (destination / 'NOTICE.txt').write_text('\n\n'.join(notices) + '\n\n'
        'Python: https://docs.python.org/3/license.html\nQt/PySide LGPL source and relinking information: '
        'https://doc.qt.io/qtforpython-6/licenses.html\n'
        'The one-folder distribution retains replaceable Qt shared libraries.\n', encoding='utf-8')
    # Python's installed license file is not a pip distribution.
    for candidate in (Path(sys.base_prefix) / 'LICENSE.txt', Path(sys.base_prefix) / 'lib/python3.11/LICENSE.txt'):
        if candidate.is_file():
            shutil.copyfile(candidate, destination / 'PYTHON-LICENSE.txt')
            break



def bundle_documentation(root: Path, destination: Path, macos: bool = False) -> None:
    """Retain the installation guide's local links and licence/credit notices."""
    for name in ('INSTALLATION.md', 'USAGE.md', 'COMPATIBILITY.md', 'TROUBLESHOOTING.md',
                 'PS5_LAN_TRANSFER.md', 'PS5_LAN_PERFORMANCE.md', 'PS5_RECEIVER_SHUTDOWN.md', 'PS5_RECEIVER_MANAGER.md'):
        text = (root / 'docs' / name).read_text(encoding='utf-8')
        text = text.replace('../ps5/receiver/PROTOCOL.md', 'receiver/PROTOCOL.md')
        (destination / name).write_text(text, encoding='utf-8')
    (destination / 'receiver').mkdir()
    shutil.copyfile(root / 'ps5/receiver/PROTOCOL.md', destination / 'receiver/PROTOCOL.md')
    (destination / 'benchmarks').mkdir()
    for name in ('lan-loopback-2026-11-16.jsonl', 'lan-profile-loopback-2026-11-16.jsonl'):
        shutil.copyfile(root / 'docs/benchmarks' / name, destination / 'benchmarks' / name)
    shutil.copyfile(root / 'docs/INSTALLATION.md', destination / 'README.md')
    for name in ('CHANGELOG.md', 'THIRD_PARTY.md', 'LICENSE'):
        shutil.copyfile(root / name, destination / name)
    if macos:
        shutil.copyfile(root / 'docs/INSTALLATION.md', destination / 'README-MACOS.md')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='NEW directory, never an existing build')
    parser.add_argument('--source-sha')
    parser.add_argument('--architecture', choices=('arm64', 'x86_64'), help='Require a native target architecture')
    parser.add_argument('--allow-dirty-preview', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    architecture = {'amd64': 'x86_64'}.get(platform.machine().lower(), platform.machine().lower())
    if sys.platform not in ('linux', 'win32', 'darwin') or architecture not in ('x86_64', 'arm64') or (architecture == 'arm64' and sys.platform != 'darwin'):
        parser.error('Builds require native Windows/Linux x64 or macOS arm64/x86_64')
    if args.architecture and args.architecture != architecture:
        parser.error('Runner/Python architecture does not match the requested native target')
    source = check_source(root, args.source_sha, args.allow_dirty_preview)
    audit_source(root)
    toolchain = json.loads((root / 'release/toolchain.json').read_text())
    if platform.python_version() != toolchain['python'][sys.platform]:
        parser.error('Use the pinned Python version from release/toolchain.json')
    if sys.platform == 'linux' and platform.libc_ver() != ('glibc', '2.36'):
        parser.error('Use the pinned Debian 12 build container (glibc 2.36), not a newer host distribution')
    env = os.environ.copy()
    env['QT_QPA_PLATFORM'] = 'offscreen'
    # Every artifact is gated on full tests, all platform type targets and whitespace.
    run([sys.executable, '-m', 'pytest', '-v', '-o', 'faulthandler_timeout=90'],
        cwd=root, env=env, timeout=600)
    run([sys.executable, '-m', 'pytest', 'tests/test_gui.py', 'tests/test_gui_components.py', '-q'],
        cwd=root, env={**env, 'QT_QPA_PLATFORM': 'minimal', 'QT_STYLE_OVERRIDE': 'Windows'}, timeout=180)
    for target in ('linux', 'win32', 'darwin'):
        run([sys.executable, '-m', 'mypy', '--platform', target, 'minecraft_playden', 'release'], cwd=root)
    run(['git', 'diff', '--check'], cwd=root)
    run(['git', 'diff', '--cached', '--check'], cwd=root)
    # Tests must not have changed source before the build.
    if check_source(root, args.source_sha, args.allow_dirty_preview)['source_tree_sha256'] != source['source_tree_sha256']:
        raise ValueError('Source changed during validation')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    stage = output / 'work'
    stage.mkdir()
    source.update({'version': __version__, 'python': platform.python_version(), 'platform': sys.platform,
                   'machine': platform.machine(), 'architecture': architecture, 'libc': platform.libc_ver(),
                   'python_distributions': subprocess.check_output([sys.executable, '-m', 'pip', 'freeze', '--all'], text=True).splitlines(),
                   'dependencies': {name: packages.version(name) for name in
                                    ('PyInstaller', 'pyinstaller-hooks-contrib', 'PySide6', 'certifi')}})
    source['branding'] = icons(root, stage)
    write_json(stage / 'build-metadata.json', source)
    licenses(root, stage)
    env.update(PLAYDEN_RELEASE_STAGE=str(stage), PLAYDEN_TARGET_ARCH=architecture, SOURCE_DATE_EPOCH=str(source['source_commit_time']),
               PYINSTALLER_CONFIG_DIR=str(stage / 'pyinstaller-cache'))
    run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', output / 'frozen',
         '--workpath', stage / 'pyinstaller', root / 'release/desktop.spec'], cwd=root, env=env)
    bundle = output / ('frozen/Minecraft Playden Packager.app' if sys.platform == 'darwin' else 'frozen/MinecraftPlaydenPackager')
    suffix = '.exe' if sys.platform == 'win32' else ''
    executable = (bundle / 'Contents/MacOS/MinecraftPlaydenPackager' if sys.platform == 'darwin'
                  else bundle / ('MinecraftPlaydenPackager' + suffix))
    documentation = bundle / 'Contents/Resources' if sys.platform == 'darwin' else bundle
    bundle_documentation(root, documentation, macos=sys.platform == 'darwin')
    write_json(documentation / 'BUILD-INFO.json', source)
    if sys.platform == 'darwin':
        run(['/usr/bin/codesign', '--force', '--deep', '--sign', '-', bundle])
        run(['/usr/bin/codesign', '--verify', '--deep', '--strict', bundle])
    audit = audit_tree(bundle, executable, source)
    write_json(output / 'content-audit.json', audit)
    run([sys.executable, '-m', 'release.smoke', str(executable)], cwd=root)
    label = '-PREVIEW' if args.allow_dirty_preview else ''
    stem = f'Minecraft-Playden-Packager-v{__version__}'
    if sys.platform == 'darwin':
        tag = 'ARM64' if architecture == 'arm64' else 'x86_64'
        artifact = output / f'{stem}-macOS-{tag}{label}.zip'
        # ditto preserves executable bits, framework links and app bundle metadata.
        run(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', bundle, artifact])
        with tempfile.TemporaryDirectory(prefix='playden-macos-zip-audit-') as temporary:
            run(['/usr/bin/ditto', '-x', '-k', artifact, temporary])
            extracted = Path(temporary) / bundle.name
            extracted_exe = extracted / 'Contents/MacOS/MinecraftPlaydenPackager'
            verified = audit_tree(extracted, extracted_exe, source)
            if verified['files'] != audit['files'] or verified['symlinks'] != audit['symlinks']:
                raise ValueError('macOS ZIP contents differ from audited app')
            run(['/usr/bin/codesign', '--verify', '--deep', '--strict', extracted])
            run([sys.executable, '-m', 'release.smoke', str(extracted_exe)], cwd=root)
    elif sys.platform == 'win32':
        artifact = output / f'{stem}-Windows-x64{label}.zip'
        with zipfile.ZipFile(artifact, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(bundle.rglob('*')):
                if path.is_file():
                    archive.write(path, 'MinecraftPlaydenPackager/' + path.relative_to(bundle).as_posix())
        with tempfile.TemporaryDirectory(prefix='playden-windows-zip-audit-') as temporary:
            with zipfile.ZipFile(artifact) as archive:
                archive.extractall(temporary)
            extracted = Path(temporary) / 'MinecraftPlaydenPackager'
            verified = audit_tree(extracted, extracted / 'MinecraftPlaydenPackager.exe', source)
            if verified['files'] != audit['files'] or verified['symlinks'] != audit['symlinks']:
                raise ValueError('ZIP contents differ from audited bundle')
            run([sys.executable, '-m', 'release.smoke', str(extracted / 'MinecraftPlaydenPackager.exe')], cwd=root)
    else:
        appdir = output / 'AppDir'
        target = appdir / 'usr/lib/minecraft-playden-packager'
        # Preserve PyInstaller's internal library symlinks and their inventory.
        shutil.copytree(bundle, target, symlinks=True)
        for origin, name in ((root / 'release/AppRun', 'AppRun'),
                             (root / 'release/minecraft-playden-packager.desktop', 'minecraft-playden-packager.desktop'),
                             (stage / 'icon.png', 'minecraft-playden-packager.png'), (stage / 'icon.png', '.DirIcon')):
            shutil.copyfile(origin, appdir / name)
        (appdir / 'AppRun').chmod(0o755)
        applications = appdir / 'usr/share/applications'
        applications.mkdir(parents=True)
        shutil.copyfile(root / 'release/minecraft-playden-packager.desktop', applications / 'minecraft-playden-packager.desktop')
        for size in ICON_SIZES:
            theme = appdir / f'usr/share/icons/hicolor/{size}x{size}/apps'
            theme.mkdir(parents=True)
            shutil.copyfile(root / f'minecraft_playden/resources/icons/playdencreeper-{size}.png',
                            theme / 'minecraft-playden-packager.png')
        audit = audit_tree(appdir, target / 'MinecraftPlaydenPackager', source)
        write_json(output / 'content-audit.json', audit)
        download(toolchain['appimagetool'], stage / 'appimagetool.AppImage')
        download(toolchain['appimage_runtime'], stage / 'runtime-x86_64')
        run([stage / 'appimagetool.AppImage', '--appimage-extract'], cwd=stage, stdout=subprocess.DEVNULL)
        artifact = output / f'{stem}-Linux-x86_64{label}.AppImage'
        run([stage / 'squashfs-root/AppRun', '--runtime-file', stage / 'runtime-x86_64',
             '--no-appstream', appdir, artifact], env={**env, 'ARCH': 'x86_64'})
        artifact.chmod(0o755)
        with tempfile.TemporaryDirectory(prefix='playden-appimage-audit-') as temporary:
            run([artifact, '--appimage-extract'], cwd=temporary, stdout=subprocess.DEVNULL)
            extracted = Path(temporary) / 'squashfs-root'
            verified = audit_tree(extracted,
                                  extracted / 'usr/lib/minecraft-playden-packager/MinecraftPlaydenPackager', source)
            if verified['files'] != audit['files'] or verified['symlinks'] != audit['symlinks']:
                raise ValueError('AppImage contents differ from audited bundle')
            run([sys.executable, '-m', 'release.smoke', str(extracted / 'AppRun')], cwd=root)
        run([sys.executable, '-m', 'release.smoke', str(artifact)], cwd=root,
            env={**env, 'APPIMAGE_EXTRACT_AND_RUN': '1'})
    write_json(output / 'build-metadata.json', source)
    write_json(output / 'artifact.json', {'filename': artifact.name, 'size': artifact.stat().st_size,
               'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
               'source_commit': source['source_commit'], 'source_tree_sha256': source['source_tree_sha256'],
               'preview': source['preview'], 'content_audit_passed': True, 'frozen_smoke_passed': True})
    (output / 'SHA256SUMS').write_text(hashlib.sha256(artifact.read_bytes()).hexdigest() + '  ' + artifact.name + '\n', encoding='utf-8')
    print(artifact)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
