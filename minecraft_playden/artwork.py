"""Bounded PNG artwork for Playden's game metadata directory; no image changes."""
import hashlib
import io
import os
import stat
import struct
import threading
import zlib
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

from PIL import Image

from .downloads import safe_path

NAMES = ('icon.png', 'background.png')
# Artwork availability does not grant launch support to these loaders.
LOADER_ARTWORK = {
    loader: {'icon.png': f'modloaders/{loader}.png'}
    for loader in ('fabric', 'forge', 'neoforge', 'quilt')
}
MAX_BYTES = 32 * 1024 * 1024
MAX_SIDE = 8192
MAX_PIXELS = 3840 * 2160
_checked: dict[bytes, tuple[int, int]] = {}
_lock = threading.Lock()


def validate_png(data: bytes) -> tuple[int, int]:
    if not 33 <= len(data) <= MAX_BYTES or data[:8] != b'\x89PNG\r\n\x1a\n' or data[8:16] != b'\0\0\0\rIHDR':
        raise ValueError('Artwork must be a PNG picture of at most 32 MiB')
    width, height = struct.unpack_from('>II', data, 16)
    if not width or not height or max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
        raise ValueError('Artwork exceeds Playden limits: 8192 per side and 3840×2160 pixels total')
    digest = hashlib.sha256(data).digest()
    with _lock:
        cached = _checked.get(digest)
    if cached is not None:
        return cached
    position, idat = 8, False
    while position < len(data):
        if len(data) - position < 12:
            raise ValueError('Artwork has a truncated PNG chunk')
        length = struct.unpack_from('>I', data, position)[0]
        if length > len(data) - position - 12:
            raise ValueError('Artwork has a truncated PNG chunk')
        kind = data[position + 4:position + 8]
        crc = zlib.crc32(memoryview(data)[position + 4:position + 8 + length])
        if crc != struct.unpack_from('>I', data, position + 8 + length)[0]:
            raise ValueError('Artwork PNG checksum mismatch')
        if kind == b'IHDR' and position != 8:
            raise ValueError('Artwork contains duplicate PNG headers')
        if kind == b'IDAT' and length:
            idat = True
        position += length + 12
        if kind == b'IEND':
            if not idat or length or position != len(data):
                raise ValueError('Artwork has invalid PNG end data')
            break
    else:
        raise ValueError('Artwork has no PNG end marker')
    try:
        # Restrict the decoder to PNG. Keep Pillow's decompression/text guards;
        # inspect bounded dimensions before asking it to decode anything.
        with Image.open(io.BytesIO(data), formats=['PNG']) as image:
            image.verify()
        with Image.open(io.BytesIO(data), formats=['PNG']) as image:
            image.load()
    except (OSError, ValueError, SyntaxError) as exc:
        raise ValueError('Artwork is a damaged or unsupported PNG picture') from exc
    with _lock:
        if len(_checked) >= 32:
            _checked.pop(next(iter(_checked)))
        _checked[digest] = (width, height)
    return width, height


def read(path: Path) -> bytes:
    path = path.expanduser().absolute()
    for part in (path, *path.parents):
        info = part.lstat()
        reparse = getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
        if stat.S_ISLNK(info.st_mode) or reparse:
            raise ValueError('Artwork must be a regular file without symlinks or reparse points')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
        raise ValueError('Artwork must be a regular PNG file of at most 32 MiB')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError('Artwork file changed while opening')
        data = stream.read(MAX_BYTES + 1)
    validate_png(data)
    return data


@lru_cache(maxsize=16)
def default(name: str, loader: str | None = None) -> bytes:
    if name not in NAMES:
        raise ValueError('Unknown default artwork')
    relative = LOADER_ARTWORK.get((loader or '').casefold(), {}).get(name, name)
    data = files('minecraft_playden').joinpath('resources/artwork/' + relative).read_bytes()
    validate_png(data)
    return data


def check_sources(icon: Path | None, background: Path | None) -> None:
    for path in (icon, background):
        if path is not None:
            read(path)


def write(root: Path, icon: Path | None = None, background: Path | None = None, *, loader: str | None = None) -> None:
    from .recipe import location
    location(root)
    for name, source in zip(NAMES, (icon, background)):
        data = read(source) if source is not None else default(name, loader)
        target = safe_path(root, 'playden/' + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(data)


def validate(root: Path, inventory: dict, *, required: bool) -> list[str]:
    errors = []
    for name in NAMES:
        relative = 'playden/' + name
        path = root / relative
        if required or path.exists() or path.is_symlink():
            if relative not in inventory:
                errors.append(f'Required file absent from inventory: {relative}')
            try:
                read(path)
            except (OSError, ValueError) as exc:
                errors.append(f'Invalid artwork {relative}: {exc}')
    return errors
