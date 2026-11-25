"""Select a metadata-matched Windows x64 runtime without executing host binaries."""
import re
import shutil
import struct
import zipfile
from pathlib import Path
from typing import Any
from .downloads import Downloader, safe_path
from .metadata import check_runtime_requirement


def pe_x64(path: Path) -> bool:
    try:
        with path.open('rb') as stream:
            header = stream.read(64)
            if len(header) != 64 or header[:2] != b'MZ':
                return False
            offset = struct.unpack_from('<I', header, 60)[0]
            stream.seek(offset)
            return stream.read(6) == b'PE\x00\x00\x64\x86'
    except (OSError, ValueError):
        return False


def runtime_home(root: Path, major: int) -> Path:
    # Java 8 JDKs keep the runnable JRE under jre/; later JDKs are flat.
    return root / 'jre' if major == 8 and (root / 'jre/bin/java.exe').is_file() else root


def inspect(root: Path, major: int) -> dict[str, Any]:
    if root.is_symlink() or any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('Java runtime symlinks are unsupported')
    fields = dict(re.findall(r'^(\w+)="([^"\r\n]*)"', (root / 'release').read_text(encoding='utf-8'), re.M))
    version = fields.get('JAVA_VERSION', '')
    match = re.match(r'(?:1\.)?(\d+)', version)
    if match is None or int(match[1]) != major:
        raise ValueError(f'Java runtime must match metadata requirement Java {major}; found {version or "unknown"}')
    if fields.get('OS_NAME', '').lower() != 'windows' or fields.get('OS_ARCH', '').lower() not in ('x86_64', 'amd64'):
        raise ValueError('Java runtime must target Windows x64, regardless of the host OS')
    home = runtime_home(root, major)
    if not all(pe_x64(home / relative) for relative in ('bin/java.exe', 'bin/server/jvm.dll')):
        raise ValueError('Java runtime requires Windows AMD64 PE java.exe and server/jvm.dll')
    return {'major': major, 'release': version, 'target': 'windows-x64'}


def install(metadata: dict[str, Any], downloader: Downloader, root: Path) -> dict[str, Any]:
    major = check_runtime_requirement(metadata)
    cached_root = downloader.cache / 'runtimes' / f'java-{major}-windows-x64'
    for candidate in [*downloader.runtime_paths, cached_root]:
        if not candidate.is_dir():
            continue
        try:
            record = inspect(candidate, major)
        except (OSError, ValueError):
            continue
        def copy(source: str, destination: str) -> str:
            downloader.check_cancelled()
            with open(source, 'rb') as incoming, open(destination, 'wb') as outgoing:
                downloader._copy(incoming, outgoing)
            return destination
        home = runtime_home(candidate, major)
        shutil.copytree(home, root, copy_function=copy, dirs_exist_ok=True)
        if home != candidate:
            copy(str(candidate / 'release'), str(root / 'release'))
        inspect(root, major)
        return {**record, 'source': 'local-or-cache'}
    url = (f'https://api.adoptium.net/v3/assets/latest/{major}/hotspot'
           '?architecture=x64&image_type=jdk&os=windows&vendor=eclipse')
    try:
        entries = downloader.json({'url': url}, cached_only=not downloader.allow_runtime_download)
        if not entries:
            raise ValueError(f'No Windows x64 Java {major} available')
        entry = entries[0]
        package = entry['binary']['package']
        archive = downloader.fetch({'url': package['link'], 'sha256': package['checksum'], 'size': package['size']},
                                   cached_only=not downloader.allow_runtime_download, progress=downloader.progress)
    except (OSError, ValueError) as exc:
        action = 'Runtime download failed; retry the build or supply a local verified runtime.' if downloader.allow_runtime_download and not downloader.offline else 'Downloads are disabled/offline.'
        raise ValueError(f'Missing compatible Windows x64 Java {major}. {action} Supply --java-runtime PATH, place it in '
                         f'the cache runtimes/java-{major}-windows-x64 directory, or explicitly allow runtime downloads. {exc}') from exc
    with zipfile.ZipFile(archive) as zip:
        prefixes = {i.filename.split('/')[0] for i in zip.infolist() if '/' in i.filename}
        if len(prefixes) != 1:
            raise ValueError('Unexpected Java archive layout')
        prefix = prefixes.pop() + '/'
        nested_jre = major == 8 and prefix + 'jre/bin/java.exe' in zip.namelist()
        for info in zip.infolist():
            downloader.check_cancelled()
            safe_path(root, info.filename)
            if info.is_dir():
                continue
            if not info.filename.startswith(prefix) or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unexpected Java archive member')
            relative = info.filename[len(prefix):]
            if nested_jre and relative != 'release':
                if not relative.startswith('jre/'):
                    continue
                relative = relative[len('jre/'):]
            path = safe_path(root, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(zip.read(info))
    record = inspect(root, major)
    return {**record, 'source': 'verified-archive', 'package': package}
