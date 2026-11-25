"""Metadata layout, exact artwork bytes, legacy compatibility and image bounds."""
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import zlib

import pytest
from PIL import Image

from minecraft_playden import artwork, recipe
from minecraft_playden.validator import validate
from lan_fixtures import build_package


def png(size=(32, 24), color='red'):
    stream = io.BytesIO()
    Image.new('RGB', size, color).save(stream, format='PNG')
    return stream.getvalue()


@pytest.mark.parametrize('name,size,digest', [
    ('icon.png', (512, 512), 'fb71a05f5ac08a30db69b510d5457cd1a5eb6ad66e3ee9054092ba9b58f8d903'),
    ('background.png', (3840, 1240), 'af06fd71b89ee2635b597391946a52bc7590f665e0d47b3c2a425d273d6dcd48'),
])
def test_bundled_artwork_is_unchanged_user_supplied_png(name, size, digest):
    data = artwork.default(name)
    assert artwork.validate_png(data) == size
    assert hashlib.sha256(data).hexdigest() == digest


def test_new_layout_contains_exact_default_artwork_and_inventory(tmp_path):
    root = build_package(tmp_path)
    assert not (root / recipe.LEGACY_PATH).exists()
    inventory = json.loads((root / 'package.json').read_text())['files']
    assert recipe.PATH in inventory
    for name in artwork.NAMES:
        data = (root / 'playden' / name).read_bytes()
        assert data == artwork.default(name, 'Fabric')
        assert inventory['playden/' + name] == hashlib.sha256(data).hexdigest()
    assert validate(root) == []


def test_custom_artwork_keeps_bytes_and_source_unchanged(tmp_path):
    root = tmp_path / 'package'
    icon, background = tmp_path / 'custom icon.png', tmp_path / 'custom background.png'
    icon.write_bytes(png((80, 50), 'blue'))
    background.write_bytes(png((8192, 1), 'green'))  # Side cap differs from total pixel budget.
    artwork.write(root, icon, background)
    assert (root / 'playden/icon.png').read_bytes() == icon.read_bytes()
    assert (root / 'playden/background.png').read_bytes() == background.read_bytes()


@pytest.mark.parametrize('size', [(8193, 1), (3000, 3000), (1, 8193)])
def test_oversized_artwork_rejected_before_decoding(size):
    # Header only: fail on dimensions before allocating image pixels.
    data = bytearray(png())
    struct.pack_into('>II', data, 16, *size)
    with pytest.raises(ValueError, match='limits'):
        artwork.validate_png(bytes(data))


@pytest.mark.parametrize('data', [b'not png', png()[:-4], png() + b'trailing data'])
def test_malformed_or_truncated_png_rejected(data):
    with pytest.raises(ValueError):
        artwork.validate_png(data)


def test_full_decoder_rejects_invalid_pixels_even_with_correct_chunk_crc():
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
    data += chunk(b'IDAT', b'not a deflate stream') + chunk(b'IEND', b'')
    with pytest.raises(ValueError, match='damaged|unsupported'):
        artwork.validate_png(data)


def test_oversized_and_symlink_sources_are_rejected(tmp_path):
    large = tmp_path / 'large.png'
    with large.open('wb') as stream:
        stream.truncate(artwork.MAX_BYTES + 1)
    with pytest.raises(ValueError, match='32 MiB'):
        artwork.read(large)
    original, link = tmp_path / 'original.png', tmp_path / 'link.png'
    original.write_bytes(png())
    try:
        link.symlink_to(original)
    except OSError:
        pytest.skip('Symlinks need privileges on this host')
    with pytest.raises(ValueError, match='symlink'):
        artwork.read(link)


@pytest.mark.parametrize('fault', ['missing-icon', 'unlisted-background', 'corrupt-rehashed-icon', 'ambiguous', 'linked-metadata'])
def test_package_rejects_missing_unlisted_corrupt_and_ambiguous_metadata(tmp_path, fault):
    root = build_package(tmp_path)
    inventory = json.loads((root / 'package.json').read_text())
    if fault == 'missing-icon':
        (root / 'playden/icon.png').unlink()
    elif fault == 'unlisted-background':
        del inventory['files']['playden/background.png']
    elif fault == 'corrupt-rehashed-icon':
        (root / 'playden/icon.png').write_bytes(b'bad png')
        inventory['files']['playden/icon.png'] = hashlib.sha256(b'bad png').hexdigest()
    elif fault == 'ambiguous':
        (root / recipe.LEGACY_PATH).write_bytes((root / recipe.PATH).read_bytes())
        inventory['files'][recipe.LEGACY_PATH] = inventory['files'][recipe.PATH]
    else:
        actual = root / 'actual-metadata'
        (root / 'playden').rename(actual)
        try:
            (root / 'playden').symlink_to(actual, target_is_directory=True)
        except OSError:
            pytest.skip('Symlinks need privileges on this host')
    (root / 'package.json').write_text(json.dumps(inventory))
    assert validate(root)


@pytest.mark.parametrize('legacy_java8', [False, True])
def test_existing_legacy_packages_without_artwork_still_validate(tmp_path, legacy_java8):
    root = build_package(tmp_path, legacy=legacy_java8)
    inventory = json.loads((root / 'package.json').read_text())
    (root / recipe.PATH).rename(root / recipe.LEGACY_PATH)
    inventory['files'][recipe.LEGACY_PATH] = inventory['files'].pop(recipe.PATH)
    for name in artwork.NAMES:
        (root / 'playden' / name).unlink()
        del inventory['files']['playden/' + name]
    (root / 'playden').rmdir()
    (root / 'package.json').write_text(json.dumps(inventory))
    assert validate(root) == []


@pytest.mark.parametrize('loader', [None, 'Fabric', 'Forge', 'NeoForge', 'Quilt'])
def test_actual_upstream_folder_art_decoder_accepts_defaults_and_wide_custom_png(tmp_path, loader):
    checker = os.environ.get('PLAYDEN_ARTWORK_CHECK')
    if not checker:
        pytest.skip('Set PLAYDEN_ARTWORK_CHECK for actual upstream artwork decoder')
    artwork.write(tmp_path, loader=loader)
    result = subprocess.run([checker, str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    (tmp_path / 'playden/background.png').write_bytes(png((8192, 1)))
    result = subprocess.run([checker, str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('legacy', [True, False], ids=['java8-fabric', 'java25-vanilla'])
def test_cli_custom_artwork_preserves_runtime_and_launch_mode(tmp_path, monkeypatch, legacy):
    from unittest.mock import patch
    from test_version_matrix import installation
    from minecraft_playden.cli import main
    icon, background = tmp_path / 'icon.png', tmp_path / 'background.png'
    icon.write_bytes(png())
    background.write_bytes(png((96, 48)))
    version = '1.16' if legacy else '26.3'
    source, _, blobs, _ = installation(tmp_path / 'source', version, legacy, 'prism')
    output = tmp_path / 'custom package'
    monkeypatch.setattr('sys.argv', ['packager', '--cache', str(tmp_path / 'cache'), 'build', '--source', 'local',
                                    '--instance', str(source), '--output', str(output), '--icon', str(icon), '--background', str(background)])
    with patch('urllib.request.urlopen', side_effect=lambda req, **kwargs: io.BytesIO(blobs[req.full_url])):
        assert main() == 0
    assert validate(output) == []
    assert (output / 'playden/icon.png').read_bytes() == icon.read_bytes()
    assert (output / 'playden/background.png').read_bytes() == background.read_bytes()
    document = json.loads((output / recipe.PATH).read_text())
    assert document['defaults']['controller'] == 'keyboard'
    assert (document['launch_entries'][0]['arguments'] == recipe.ARGUMENTS) is (not legacy)
    assert json.loads((output / 'metadata/runtime.json').read_text())['major'] == (8 if legacy else 25)


@pytest.mark.parametrize('loader,digest', [
    ('Fabric', '3e9fe8dfe5b9a7e16a3fd4d245078ecb8e874dea35bce0cca5ce02bd3e599b82'),
    ('Forge', '0a4286920f4019b20bf23bf2eb36ba3cde377efb6b961c6b17388883d0315bb2'),
    ('NeoForge', 'b3857715b41a1a9c773645d6a8bc95fceffb6e74d1a7ad8cd9e3211e3d7a27cf'),
    ('Quilt', 'a607ffc904266ea7c35810b623f154f9c2df9ea6879582b04ca48f3ae6828fef'),
])
def test_bundled_loader_icons_are_exact_and_keep_default_background(tmp_path, loader, digest):
    artwork.write(tmp_path, loader=loader)
    data = (tmp_path / 'playden/icon.png').read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest
    assert artwork.validate_png(data) == (512, 512)
    assert data == artwork.default('icon.png', loader.lower())
    assert (tmp_path / 'playden/background.png').read_bytes() == artwork.default('background.png')
    assert sorted(p.name for p in (tmp_path / 'playden').iterdir()) == list(reversed(artwork.NAMES))


@pytest.mark.parametrize('loader', [None, 'Vanilla', 'Unknown', '../../fabric'])
def test_missing_loader_artwork_falls_back_to_minecraft(tmp_path, loader):
    artwork.write(tmp_path, loader=loader)
    for name in artwork.NAMES:
        assert (tmp_path / 'playden' / name).read_bytes() == artwork.default(name)


@pytest.mark.parametrize('custom_icon,custom_background', [(True, False), (False, True), (True, True)])
def test_explicit_overrides_take_priority_independently(tmp_path, custom_icon, custom_background):
    icon, background = tmp_path / 'custom-icon.png', tmp_path / 'custom-background.png'
    icon.write_bytes(png((48, 48)))
    background.write_bytes(png((96, 48)))
    root = tmp_path / 'package'
    artwork.write(root, icon if custom_icon else None, background if custom_background else None, loader='Fabric')
    assert (root / 'playden/icon.png').read_bytes() == (icon.read_bytes() if custom_icon else artwork.default('icon.png', 'Fabric'))
    assert (root / 'playden/background.png').read_bytes() == (background.read_bytes() if custom_background else artwork.default('background.png'))


@pytest.mark.parametrize('loader', ['Forge', 'NeoForge', 'Quilt'])
def test_artwork_does_not_enable_unsupported_loaders(loader):
    from minecraft_playden.compatibility import assess
    from minecraft_playden.modloaders import ModLoader
    assert not assess('1.16', (ModLoader(loader, 'fixture'),)).permits_build
