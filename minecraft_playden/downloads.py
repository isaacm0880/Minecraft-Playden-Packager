"""Atomic, content-verified HTTP downloads with a reusable cache."""
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any
from .operations import CancellationToken, OperationCancelled


def default_cache() -> Path:
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Caches/minecraft-playden'
    return Path(os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'minecraft-playden'


def digest(path: Path, algorithm: str = "sha1", cancellation: CancellationToken | None = None) -> str:
    with path.open('rb') as stream:
        if cancellation is None:
            return hashlib.file_digest(stream, algorithm).hexdigest()
        checksum = hashlib.new(algorithm)
        while True:
            cancellation.check()
            block = stream.read(1024 * 1024)
            if not block:
                return checksum.hexdigest()
            checksum.update(block)


def safe_path(root: Path, relative: str) -> Path:
    if '\\' in relative or ':' in relative:
        raise ValueError(f"Unsafe path: {relative}")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()) or relative.startswith('/'):
        raise ValueError(f"Unsafe path: {relative}")
    return path


class Downloader:
    def __init__(self, cache: Path, cancellation: CancellationToken | None = None, *,
                 offline: bool = False, allow_runtime_download: bool = False,
                 runtime_paths: tuple[Path, ...] = ()):
        self.offline = offline
        self.allow_runtime_download = allow_runtime_download
        self.runtime_paths = list(runtime_paths)
        self.local_roots: list[Path] = []
        self.version_files: dict[str, Path] = {}
        self.cancellation = cancellation
        self.cache = cache
        cache.mkdir(parents=True, exist_ok=True)

    def check_cancelled(self) -> None:
        if self.cancellation is not None:
            self.cancellation.check()

    def _copy(self, source, target) -> None:
        while True:
            self.check_cancelled()
            block = source.read(1024 * 1024)
            if not block:
                return
            target.write(block)

    def fetch(self, spec: dict[str, Any], target: Path | None = None, refresh: bool = False, *, cached_only: bool = False) -> Path:
        self.check_cancelled()
        url = spec['url']
        if not url.startswith('https://'):
            raise ValueError(f"Only HTTPS downloads allowed: {url}")
        algorithm = 'sha256' if 'sha256' in spec else 'sha1'
        expected = spec.get(algorithm)
        key = hashlib.sha256(url.encode()).hexdigest()
        cached = self.cache / key

        def valid(path: Path) -> bool:
            return (path.is_file() and (spec.get('size') is None or path.stat().st_size == spec['size'])
                    and (not expected or digest(path, algorithm, self.cancellation) == expected))

        if expected and not valid(cached):
            candidates: list[Path] = []
            if isinstance(spec.get('path'), str):
                candidates.extend(safe_path(root, spec['path']) for root in self.local_roots)
            if spec.get('id') in self.version_files:
                candidates.append(self.version_files[spec['id']])
            for candidate in candidates:
                if valid(candidate):
                    with tempfile.NamedTemporaryFile(dir=self.cache, delete=False) as out:
                        temp_seed = Path(out.name)
                        try:
                            with candidate.open('rb') as incoming:
                                self._copy(incoming, out)
                        except BaseException:
                            temp_seed.unlink(missing_ok=True)
                            raise
                    if valid(temp_seed):
                        temp_seed.replace(cached)
                        break
                    temp_seed.unlink(missing_ok=True)
        if self.offline or cached_only:
            if not valid(cached):
                raise ValueError('Required verified file is not available locally or in cache: ' + url)
            refresh = False
        if refresh or not valid(cached):
            for attempt in range(3):
                temp: Path | None = None
                try:
                    with tempfile.NamedTemporaryFile(dir=self.cache, delete=False) as out:
                        temp = Path(out.name)
                        req = urllib.request.Request(url, headers={'User-Agent': 'Minecraft-Playden-Packager/0.1'})
                        with urllib.request.urlopen(req, timeout=60) as response:
                            self._copy(response, out)
                    if not valid(temp):
                        raise ValueError(f"Hash or size mismatch: {url}")
                    temp.replace(cached)
                    break
                except OperationCancelled:
                    raise
                except Exception:
                    self.check_cancelled()
                    if attempt == 2:
                        raise
                    if self.cancellation is not None:
                        self.cancellation.wait(attempt + 1)
                    else:
                        time.sleep(attempt + 1)
                finally:
                    if temp is not None:
                        temp.unlink(missing_ok=True)
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            with cached.open('rb') as source, target.open('wb') as destination:
                self._copy(source, destination)
            return target
        return cached

    def json(self, spec: dict[str, Any], refresh: bool = False, *, cached_only: bool = False) -> Any:
        return json.loads(self.fetch(spec, refresh=refresh, cached_only=cached_only).read_text(encoding='utf-8'))
