# Playden metadata directory and artwork

New launch packages for Vanilla and Fabric contain:

```text
<package>/playden/recipe.json
<package>/playden/icon.png
<package>/playden/background.png
<package>/java/bin/java.exe
<package>/playden.args
```

The metadata directory does not change executable paths or working directories.
Java 9+ retains `@../../playden.args`. Java 8 retains its manifest/bootstrap adapter
and exact command sidecar. Controller defaults remain independently selectable.
File-only instance imports still have no generated launch recipe or title art.

## Verified upstream contract

Checked 2026-11-25 against latest Playden develop commit
[`3f973f4eb215cee894efa37b08a3f5bf9df4daaa`](https://github.com/leolobato/playden-ps5/commit/3f973f4eb215cee894efa37b08a3f5bf9df4daaa):

- `src/stores/local/library_source.{h,cpp}` uses `kGameFolderDirectory = "playden"`
  and reads `recipe.json` from that directory. The recipe limit remains 16 KiB.
  It walks directories and opens the file without following links. Root recipes
  are no longer the upstream automatic discovery path.
- `src/homebrew/folder_art.{hpp,cpp}` reads `playden/icon.png` and
  `playden/background.png`, also without following links.
- `src/homebrew/artwork_backend.{hpp,cpp}` accepts PNG files up to 32 MiB,
  each dimension at most 8192, and total area at most 3840×2160 pixels. These
  limits are not a requirement that width be at most 3840 and height at most 2160;
  for example a thin 8192-pixel-wide image fits. The source's generic error text
  calls the total budget “3840x2160”; the packager follows its actual constants.
- Playden fully decodes the PNG, uses a centered square for the 512×512 title icon,
  and scales/crops the background to fill 3840×2160, compositing transparency
  onto black for its DXT1 title background. No DDS conversion is needed in the
  packager. Non-square icons and wide backgrounds may lose their outer edges.

The integration harness `tools/playden_artwork_check.cpp` links unmodified
upstream `folder_art.cpp` and `artwork_backend.cpp`, checking the constants and
calling the actual `CheckFolderArt` decoder. No upstream image implementation
is copied into or shipped with the packager.

## Default and custom images

The maintainer-supplied Minecraft artwork is stored unchanged in
`minecraft_playden/resources/artwork/`, included in wheel and frozen builds.
The original external artwork folder is not accessed by package generation.

Loader icons supplied for Fabric (fabric tag), Forge (anvil), NeoForge (fox) and
Quilt (four-color quilt) are bundled unchanged under `resources/artwork/modloaders/`.
Each is a 512×512 RGBA PNG. Fabric launch packages automatically use `fabric.png`
as `playden/icon.png`. All four variants are available to artwork selection;
this does not enable packaging for unsupported loaders. Vanilla and unknown
loaders use the Minecraft icon. These variants contain no backgrounds, so the
Minecraft background remains the automatic choice for every current loader.

| Asset | Dimensions | SHA-256 |
| --- | --- | --- |
| icon.png | 512×512 RGBA | `fb71a05f5ac08a30db69b510d5457cd1a5eb6ad66e3ee9054092ba9b58f8d903` |
| background.png | 3840×1240 RGB | `af06fd71b89ee2635b597391946a52bc7590f665e0d47b3c2a425d273d6dcd48` |
| modloaders/fabric.png | 512×512 RGBA | `3e9fe8dfe5b9a7e16a3fd4d245078ecb8e874dea35bce0cca5ce02bd3e599b82` |
| modloaders/forge.png | 512×512 RGBA | `0a4286920f4019b20bf23bf2eb36ba3cde377efb6b961c6b17388883d0315bb2` |
| modloaders/neoforge.png | 512×512 RGBA | `b3857715b41a1a9c773645d6a8bc95fceffb6e74d1a7ad8cd9e3211e3d7a27cf` |
| modloaders/quilt.png | 512×512 RGBA | `a607ffc904266ea7c35810b623f154f9c2df9ea6879582b04ca48f3ae6828fef` |

In **Build advanced options**, use **Playden icon** and **Playden background**.
Each row has **Choose PNG…** and **Use default**; an empty path selects its bundled
image for the detected loader, with Minecraft as fallback. **Use default** clears
the explicit override and restores this automatic selection. Choices are remembered immediately per user and copied into queued build
requests. Missing or invalid selections block building until corrected/reset.
The CLI defaults are independent of GUI preferences:

```sh
python -m minecraft_playden build --source local --instance /path/to/instance \
  --output /path/to/new-package --icon /path/to/icon.png \
  --background /path/to/background.png
```

Either override can be omitted independently. Explicit custom PNGs take priority
over automatic selection for that image only. Only PNG inputs are accepted;
images are validated and copied byte for byte, never resized or recompressed.
Pillow is a core dependency so CLI validation does not require Qt. It is restricted
to the PNG decoder, with its existing decompression/text guards left enabled.
The packager bounds file size and dimensions before decoding, verifies PNG chunks
and CRCs, and fully loads the pixels. Symlinks, reparse points and special files
are refused. Custom artwork paths are not written into package metadata.

Both PNGs and the recipe enter the immutable SHA-256 inventory. New-layout
validation requires all three, including valid PNG content even if a bad file's
inventory hash was recomputed. The receiver independently checks required files,
size/dimension limits, PNG structure/CRCs and inventory binding; full pixel decoding
occurs in the packager and Playden, not in the C receiver. USB and LAN preserve the
same files. Rebuild/restart the receiver from this checkout before using new
packages; no protocol or pairing format changes were made.

## Existing packages

Validation and transfers continue to support packages containing only root-level
`recipe.json`, without requiring artwork that did not exist when they were built.
If legacy packages contain artwork, those present files must still be valid and
inventoried. Both recipe locations together, or a linked metadata directory, are
refused rather than silently choosing a recipe. A broken current recipe does not
fall back to an older one.

Old packages are never rewritten automatically. Current Playden will not discover
the root recipe automatically; rebuild to a new directory for the new layout, or
retain the documented manual launch setup. Older Playden builds that only looked
at root recipes likewise need manual setup for new packages. No duplicate root
recipe is emitted, and no previous physical compatibility rating is changed.

## Testing

```sh
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

Optional actual-upstream artwork check, using the same checkout as the recipe
checker documented in [PLAYDEN-RECIPES.md](PLAYDEN-RECIPES.md):

```sh
c++ -std=c++20 -O1 -ffunction-sections -fdata-sections \
  -I "$PLAYDEN_SOURCE/src" -I "$PLAYDEN_SOURCE/third_party/stb" \
  tools/playden_artwork_check.cpp \
  "$PLAYDEN_SOURCE"/src/homebrew/{folder_art,artwork_backend}.cpp \
  -Wl,--gc-sections -o /tmp/playden_artwork_check
PLAYDEN_ARTWORK_CHECK=/tmp/playden_artwork_check python -m pytest -q tests/test_artwork.py
```

Regressions cover exact default bytes, custom GUI/CLI inputs, remembered/reset
preferences, independent defaults, malformed/truncated/oversized PNGs, symlink
refusal, missing/unlisted/rehashed artwork, current/legacy recipe ambiguity, actual
receiver transfers and Java 8/9+ launch preservation. The three Java 8 physical-test
packages are rebuilt to fresh directories with default artwork; earlier outputs
remain intact. Packaging/decoder success does not establish physical PS5 artwork
appearance or new Minecraft gameplay compatibility. Only targeted checks of the
new metadata/artwork behavior are useful; previously working versions do not
require physical retesting for this change.
