"""Build and verify an exact-source PS5 receiver for desktop candidates."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile

from minecraft_playden.receiver_manager import CONTROL_PORT, PORT, prepare_elf

SDK_URL = 'https://github.com/ps5-payload-dev/sdk/releases/download/v0.45/ps5-payload-sdk.zip'
SDK_SHA256 = '5fac64093d0e3e69f2584ce7935f24f703f622d9681f1495c85b9f00d1291a2c'
OPTIONS = {'DIAGNOSTICS': '0', 'SOCKET_BUFFER': '0', 'BUFFERED_READS': '1'}
ELF = 'minecraft_playden_receiver.elf'
MANIFEST = 'receiver-manifest.json'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(root: Path) -> dict[str, str]:
    names = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z', '--',
                                     'ps5/receiver', 'ps5/diagnostic/recipe_check.h']).decode().split('\0')
    return {name: digest(root / name) for name in sorted(filter(None, names))}


def source_digest(files: dict[str, str]) -> str:
    value = hashlib.sha256()
    for name, checksum in files.items():
        value.update(name.encode() + b'\0' + bytes.fromhex(checksum) + b'\0')
    return value.hexdigest()


def verify(root: Path, bundle: Path, commit: str) -> dict:
    """Reject a missing, stale or replaced receiver before it enters a desktop build."""
    manifest = json.loads((bundle / MANIFEST).read_text(encoding='utf-8'))
    files = source_files(root)
    elf = bundle / ELF
    if (manifest.get('schema') != 1 or manifest.get('source_commit') != commit
            or manifest.get('source_files') != files
            or manifest.get('receiver_source_sha256') != source_digest(files)
            or manifest.get('sdk_archive_sha256') != SDK_SHA256
            or manifest.get('build_options') != OPTIONS
            or manifest.get('elf_sha256') != digest(elf)
            or manifest.get('elf_size') != elf.stat().st_size):
        raise ValueError('Receiver ELF does not match the exact source, SDK and build manifest')
    prepare_elf(elf, b'\x80' + bytes(382) + b'\x01', PORT, CONTROL_PORT)
    return manifest


def build(root: Path, output: Path, sdk_archive: Path | None = None, *, preview: bool = False) -> dict:
    commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    changes = subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--',
                                       'ps5/receiver', 'ps5/diagnostic/recipe_check.h'], text=True).strip()
    if changes and not preview:
        raise ValueError('Receiver source is dirty; commit and review it before a release build')
    output.mkdir(parents=True, exist_ok=False)
    try:
        with tempfile.TemporaryDirectory(prefix='playden-receiver-sdk-') as temporary:
            work = Path(temporary)
            archive = sdk_archive or work / 'sdk.zip'
            if sdk_archive is None:
                with urllib.request.urlopen(SDK_URL, timeout=120) as response, archive.open('xb') as stream:
                    shutil.copyfileobj(response, stream)
            if digest(archive) != SDK_SHA256:
                raise ValueError('Pinned PS5 SDK archive checksum mismatch')
            with zipfile.ZipFile(archive) as source:
                for entry in source.infolist():
                    path = Path(entry.filename)
                    if path.is_absolute() or '..' in path.parts or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError('Unsafe PS5 SDK archive member')
                source.extractall(work)
                for entry in source.infolist():
                    path = work / entry.filename
                    if path.is_file() and (entry.external_attr >> 16) & 0o111:
                        path.chmod(path.stat().st_mode | 0o111)
            sdk = work / 'ps5-payload-sdk'
            if not (sdk / 'toolchain/prospero.mk').is_file():
                raise ValueError('Pinned PS5 SDK archive has no expected toolchain')
            compiler = subprocess.check_output([str(sdk / 'bin/prospero-clang'), '--version'], text=True).splitlines()[0]
            env = {**os.environ, 'PS5_PAYLOAD_SDK': str(sdk)}
            first, second = work / 'first.elf', work / 'second.elf'
            for destination in (first, second):
                subprocess.run(['make', '-B', '-C', str(root / 'ps5/receiver'), 'ELF=' + str(destination),
                                *[key + '=' + value for key, value in OPTIONS.items()]],
                               env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
                prepare_elf(destination, b'\x80' + bytes(382) + b'\x01', PORT, CONTROL_PORT)
            if first.read_bytes() != second.read_bytes():
                raise ValueError('Pinned PS5 receiver builds are not byte reproducible')
            shutil.copyfile(first, output / ELF)
        files = source_files(root)
        manifest = {'schema': 1, 'source_commit': commit, 'source_dirty': bool(changes),
                    'source_files': files, 'receiver_source_sha256': source_digest(files),
                    'sdk_url': SDK_URL, 'sdk_archive_sha256': SDK_SHA256,
                    'compiler': compiler, 'build_options': OPTIONS,
                    'elf_sha256': digest(output / ELF), 'elf_size': (output / ELF).stat().st_size}
        (output / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        verify(root, output, commit)
        return manifest
    except BaseException:
        shutil.rmtree(output)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--sdk-archive', type=Path, help='Previously downloaded exact pinned SDK ZIP')
    parser.add_argument('--allow-dirty-preview', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    result = build(root, args.output, args.sdk_archive, preview=args.allow_dirty_preview)
    print(result['elf_sha256'])


if __name__ == '__main__':
    main()
