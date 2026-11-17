"""Cached official metadata, with bounded inheritance and reusable format adapters."""
from typing import Any
from .downloads import Downloader
from .metadata import merge, normalize

MANIFEST = 'https://piston-meta.mojang.com/mc/game/version_manifest_v2.json'


class VersionResolver:
    def __init__(self, downloader: Downloader):
        self.downloader = downloader

    def versions(self, refresh: bool = False) -> list[dict[str, Any]]:
        return self.downloader.json({'url': MANIFEST}, refresh=refresh)['versions']

    def resolve(self, version: str) -> dict[str, Any]:
        entries = {entry['id']: entry for entry in self.versions()}
        seen: set[str] = set()
        def load(identifier: str) -> dict[str, Any]:
            if identifier in seen or len(seen) >= 32:
                raise ValueError('Cyclic or excessive Minecraft version inheritance')
            seen.add(identifier)
            entry = entries.get(identifier)
            if entry is None:
                raise ValueError(f'Unknown Minecraft version: {identifier}; refresh the versions list to update cached metadata')
            metadata = self.downloader.json(entry)
            if not isinstance(metadata, dict) or metadata.get('id') != identifier:
                raise ValueError('Official version metadata does not match the selected Minecraft version')
            if 'inheritsFrom' in metadata:
                parent = metadata['inheritsFrom']
                if not isinstance(parent, str):
                    raise ValueError('Invalid inherited Minecraft version')
                metadata = merge(load(parent), metadata)
            return normalize(metadata)
        return load(version)
