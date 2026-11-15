"""Mojang version discovery. Future import adapters can supply resolved metadata."""
from typing import Any
from .downloads import Downloader

MANIFEST = 'https://piston-meta.mojang.com/mc/game/version_manifest_v2.json'


class VersionResolver:
    def __init__(self, downloader: Downloader):
        self.downloader = downloader

    def versions(self) -> list[dict[str, Any]]:
        return self.downloader.json({'url': MANIFEST}, refresh=True)['versions']

    def resolve(self, version: str) -> dict[str, Any]:
        entry = next((v for v in self.versions() if v['id'] == version), None)
        if entry is None:
            raise ValueError(f"Unknown Minecraft version: {version}")
        metadata = self.downloader.json(entry)
        if metadata.get('id') != version:
            raise ValueError('Official version metadata does not match the selected Minecraft version')
        if 'inheritsFrom' in metadata or 'arguments' not in metadata:
            raise ValueError('Only standalone modern versions with structured arguments are supported')
        if metadata.get('javaVersion', {}).get('majorVersion', 8) < 17:
            raise ValueError('This release supports Java 17+ Minecraft versions only')
        return metadata
