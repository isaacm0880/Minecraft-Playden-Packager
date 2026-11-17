"""Content-addressed assets plus metadata-selected legacy resource views."""
import re
import shutil
from pathlib import Path
from typing import Any

from .downloads import digest, safe_path
from .operations import CancellationToken


def files(index: dict[str, Any], identifier: str, game_directory: str) -> list[tuple[str, dict[str, Any]]]:
    objects = index.get('objects')
    if not isinstance(objects, dict):
        raise ValueError('Invalid Minecraft asset object map')
    safe_path(Path('/assets'), identifier)
    result = []
    for name, item in objects.items():
        if not isinstance(name, str) or not isinstance(item, dict):
            raise ValueError('Invalid Minecraft asset entry')
        sha = item.get('hash')
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha) or type(item.get('size')) is not int or item['size'] < 0:
            raise ValueError('Invalid Minecraft asset hash or size')
        spec = {'sha1': sha, 'size': item['size']}
        result.append((f'assets/objects/{sha[:2]}/{sha}', spec))
        if index.get('virtual'):
            relative = f'assets/virtual/{identifier}/{name}'
            safe_path(Path('/package') / f'assets/virtual/{identifier}', name)
            result.append((relative, spec))
        if index.get('map_to_resources'):
            safe_path(Path('/package') / game_directory / 'resources', name)
            result.append((f'{game_directory}/resources/{name}', spec))
    return result


def materialize(root: Path, index: dict[str, Any], identifier: str, game_directory: str,
                cancellation: CancellationToken | None = None) -> None:
    for relative, spec in files(index, identifier, game_directory):
        if cancellation is not None:
            cancellation.check()
        if relative.startswith('assets/objects/'):
            continue
        source = safe_path(root, f'assets/objects/{spec["sha1"][:2]}/{spec["sha1"]}')
        target = safe_path(root, relative)
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise ValueError(f'Conflicting mapped Minecraft asset: {relative}')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def validate(root: Path, index: dict[str, Any], identifier: str, game_directory: str,
             cancellation: CancellationToken | None = None) -> list[str]:
    errors = []
    seen: set[str] = set()
    for relative, spec in files(index, identifier, game_directory):
        if cancellation is not None:
            cancellation.check()
        if relative in seen:
            continue
        seen.add(relative)
        path = safe_path(root, relative)
        if not path.is_file() or path.stat().st_size != spec['size'] or digest(path, cancellation=cancellation) != spec['sha1']:
            errors.append(f'Missing or invalid Minecraft asset: {relative}')
    return errors
