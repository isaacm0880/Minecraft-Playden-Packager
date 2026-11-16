"""Deterministically resize the supplied PNG; never alter its original bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from minecraft_playden.branding import ICON_SIZES


def contained_image(source, size):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    scaled = source.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
    canvas = QImage(size, size, QImage.Format.Format_ARGB32)
    canvas.setColorSpace(source.colorSpace())
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    painter.end()
    return canvas


MACOS_ICON_TYPES = {16: b'icp4', 32: b'icp5', 64: b'icp6', 128: b'ic07', 256: b'ic08', 512: b'ic09'}


def generate_icns(directory: Path) -> None:
    from PySide6.QtGui import QImage
    source = QImage(str(directory / 'playdencreeper.png'))
    if source.isNull() or not contained_image(source, 512).save(str(directory / 'playdencreeper-512.png'), 'PNG'):
        raise ValueError('Cannot generate macOS icon')
    chunks = []
    for size, kind in MACOS_ICON_TYPES.items():
        payload = (directory / f'playdencreeper-{size}.png').read_bytes()
        chunks.append(kind + struct.pack('>I', len(payload) + 8) + payload)
    body = b''.join(chunks)
    (directory / 'playdencreeper.icns').write_bytes(b'icns' + struct.pack('>I', len(body) + 8) + body)


def generate(directory: Path) -> dict:
    from PySide6.QtGui import QImage
    original = directory / 'playdencreeper.png'
    source = QImage(str(original))
    if source.isNull():
        raise ValueError('Official source PNG is missing or unreadable')
    blobs = []
    for size in ICON_SIZES:
        target = directory / f'playdencreeper-{size}.png'
        if not contained_image(source, size).save(str(target), 'PNG'):
            raise ValueError(f'Cannot save icon size {size}')
        blobs.append(target.read_bytes())
    offset = 6 + 16 * len(ICON_SIZES)
    entries = []
    for size, blob in zip(ICON_SIZES, blobs):
        entries.append(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(blob), offset))
        offset += len(blob)
    (directory / 'playdencreeper.ico').write_bytes(struct.pack('<HHH', 0, 1, len(blobs)) + b''.join(entries) + b''.join(blobs))
    generate_icns(directory)
    names = ['playdencreeper.icns', 'playdencreeper-512.png', 'playdencreeper.png', 'playdencreeper.ico', *[f'playdencreeper-{size}.png' for size in ICON_SIZES]]
    manifest = {'source_file': original.name, 'source_width': source.width(), 'source_height': source.height(),
                'source_has_alpha': source.hasAlphaChannel(), 'macos_icon_sizes': list(MACOS_ICON_TYPES), 'icon_sizes': list(ICON_SIZES),
                'resize': 'keep aspect ratio, transparent padding, smooth downsampling',
                'files': {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names}}
    (directory / 'icon-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    return manifest


def verify(directory: Path) -> dict:
    from PySide6.QtGui import QImage
    manifest = json.loads((directory / 'icon-manifest.json').read_text())
    expected = {'playdencreeper.icns', 'playdencreeper-512.png', 'playdencreeper.png', 'playdencreeper.ico', *[f'playdencreeper-{size}.png' for size in ICON_SIZES]}
    if set(manifest['files']) != expected or manifest['icon_sizes'] != list(ICON_SIZES):
        raise ValueError('Official icon inventory/sizes do not match the required branding assets')
    for name, checksum in manifest['files'].items():
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != checksum:
            raise ValueError(f'Branding asset changed; regenerate and review icons: {name}')
    ico = (directory / 'playdencreeper.ico').read_bytes()
    if struct.unpack_from('<HHH', ico) != (0, 1, len(ICON_SIZES)):
        raise ValueError('Invalid multi-resolution ICO header')
    for index, size in enumerate(ICON_SIZES):
        width, height, _, _, planes, depth, length, offset = struct.unpack_from('<BBBBHHII', ico, 6 + index * 16)
        png = (directory / f'playdencreeper-{size}.png').read_bytes()
        image = QImage.fromData(png)
        if ((width or 256, height or 256, planes, depth) != (size, size, 1, 32)
                or ico[offset:offset + length] != png or image.width() != size or image.height() != size
                or not image.hasAlphaChannel()):
            raise ValueError(f'ICO frame verification failed: {size}')
    data = (directory / 'playdencreeper.icns').read_bytes()
    if data[:4] != b'icns' or struct.unpack_from('>I', data, 4)[0] != len(data):
        raise ValueError('Invalid macOS ICNS header')
    offset = 8
    for size, kind in MACOS_ICON_TYPES.items():
        png = (directory / f'playdencreeper-{size}.png').read_bytes()
        if data[offset:offset + 4] != kind or struct.unpack_from('>I', data, offset + 4)[0] != len(png) + 8 or data[offset + 8:offset + 8 + len(png)] != png:
            raise ValueError(f'Invalid macOS ICNS frame: {size}')
        offset += 8 + len(png)
    if offset != len(data) or manifest['macos_icon_sizes'] != list(MACOS_ICON_TYPES):
        raise ValueError('Unexpected macOS ICNS frames')
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent.parent / 'minecraft_playden/resources/icons'
    info = verify(directory) if args.check else generate(directory)
    print(json.dumps(info, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
