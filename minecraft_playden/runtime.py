"""Install Windows x64 Temurin from Eclipse Adoptium's official API."""
import zipfile
from pathlib import Path
from typing import Any
from .downloads import Downloader, safe_path


def install(metadata: dict[str, Any], downloader: Downloader, root: Path) -> dict[str, Any]:
    major = metadata.get('javaVersion', {}).get('majorVersion', 8)
    url = (f'https://api.adoptium.net/v3/assets/latest/{major}/hotspot'
           '?architecture=x64&image_type=jdk&os=windows&vendor=eclipse')
    entries = downloader.json({'url': url}, refresh=True)
    if not entries:
        raise ValueError(f'No Windows x64 Java {major} available')
    entry = entries[0]
    package = entry['binary']['package']
    archive = downloader.fetch({'url': package['link'], 'sha256': package['checksum'], 'size': package['size']})
    with zipfile.ZipFile(archive) as zip:
        prefixes = {i.filename.split('/')[0] for i in zip.infolist() if '/' in i.filename}
        if len(prefixes) != 1:
            raise ValueError('Unexpected Java archive layout')
        prefix = prefixes.pop() + '/'
        for info in zip.infolist():
            downloader.check_cancelled()
            safe_path(root, info.filename)
            if info.is_dir():
                continue
            if not info.filename.startswith(prefix):
                raise ValueError('Unexpected Java archive member')
            path = safe_path(root, info.filename[len(prefix):])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(zip.read(info))
    return {'major': major, 'release': entry['release_name'], 'package': package}
