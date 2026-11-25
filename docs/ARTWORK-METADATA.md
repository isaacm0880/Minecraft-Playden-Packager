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

## Fabric icon investigation, 2026-11-25

The maintainer reports successful physical PS5 gameplay, controller mapping,
world saving and reloading for prepared Fabric 1.14.4 and 1.16, with the default
Minecraft background and automatic recipe detection working, but the regular
Minecraft icon displayed. This investigation did not rerun Minecraft.

The actual files under `dist/physical-fabric-java8-20261125/` were inspected
before considering a packager change. Both packages contain the supplied Fabric
tag icon, not the regular Minecraft icon. Their `package.json` records Fabric
Loader 0.20.1 and binds the correct PNG hashes in its immutable inventory.

| File or reference | SHA-256 |
| --- | --- |
| Both packages' `playden/icon.png`, supplied `playden/modloaders/fabric.png`, bundled `resources/artwork/modloaders/fabric.png` | `3e9fe8dfe5b9a7e16a3fd4d245078ecb8e874dea35bce0cca5ce02bd3e599b82` |
| Supplied and bundled regular Minecraft `icon.png` | `fb71a05f5ac08a30db69b510d5457cd1a5eb6ad66e3ee9054092ba9b58f8d903` |
| Both packages' `playden/background.png`, supplied and bundled Minecraft background | `af06fd71b89ee2635b597391946a52bc7590f665e0d47b3c2a425d273d6dcd48` |

The four bundled loader icons also match their supplied originals exactly.
Vanilla selects the Minecraft icon; Fabric selection follows the actual Fabric
launch plan in `exporter.build`, rather than a title/folder-name guess. Forge,
NeoForge and Quilt icons remain included in wheel/frozen resource declarations
without enabling those loaders. Every automatic background is Minecraft's.
Custom icon/background paths independently override the corresponding default.
GUI selections are remembered and snapshotted in queued requests; **Use default**
clears the override. The inspected native GUI settings had no saved artwork
overrides. CLI builds do not read those settings. A remembered explicit Minecraft
icon could override Fabric on another build, but it did not produce the two
inspected icons.

Current upstream develop was pinned at
[`c324021adda124641e7f3fa1f8252a4f46fde569`](https://github.com/leolobato/playden-ps5/commit/c324021adda124641e7f3fa1f8252a4f46fde569).
Its actual `CheckFolderArt` decoder accepts the supplied art. Linking unmodified
`MakeFolderArt`, `DecodePlayerArt` and `MakeTitleIcon` code against the pinned UI
dependency also converted both physical packages successfully. Each produced the
same 512×512 RGB `icon0.png`, hash
`7d8ac6c041584727aab0b9a709a068ae331614ace9e3d0119b794d5e63fe9222`.
Decoded pixels retain the Fabric icon, with transparency composited onto black
(at most one channel value different from Pillow's rounding). The native title
PNG is re-encoded, so its hash is not expected to match the input PNG.

### Playden's first-title artwork and retained state

[Upstream importing documentation](https://github.com/leolobato/playden-ps5/blob/c324021adda124641e7f3fa1f8252a4f46fde569/docs/importing-games.md#title-art-in-the-game-folder)
describes these files as **PS5 title artwork**, read once for a game's first
title, not automatically reapplied when the folder changes. The Playden app's
own library posters use a separate store/program-icon artwork path.

The implementation confirms the precedence:

- `runtime_host.cpp` first restores kept custom art and adopts art from an
  existing native title, then attempts folder-art seeding.
- [`FilesystemPackageInstaller::SeedCustomTitleArt`](https://github.com/leolobato/playden-ps5/blob/c324021adda124641e7f3fa1f8252a4f46fde569/src/runtime/package_installer.cpp#L1065)
  returns without reading the package when the native title already exists,
  `title-metadata/custom/` exists, or `title-metadata/.installed` exists.
- Fresh folder `icon.png` becomes saved `custom/icon0.png`; the background becomes
  `custom/sce_sys/pic0.dds` and `pic1.dds`. Saved/player art takes precedence over
  generated fallback title art. Without usable folder art the ordinary fallback
  considers store cover, game icon, program icon, then generic title icon.
- Repairs, kit updates and moves preserve custom art. Removing and re-adding the
  same import is not a reliable reset: `ImportStore::Add` reuses the removed
  folder's ID, and kept art may be restored.
- The PS5 Home screen additionally has installed artwork copies and a rendered
  cache. Editing a package PNG does not update those copies or invalidate it.

The maintainer subsequently confirmed the physical cause: Playden had retained
old game data. Removing that data resolved the icon mismatch, and both default
and loader-specific artwork now work. There is no packager artwork defect.
The earlier read-only investigation could not access the configured PS5 HTTP
endpoint; this resolution is the maintainer's subsequent physical confirmation,
not a console-file inspection performed by the packager tests. No further
Minecraft gameplay retest or packager artwork fix is required.

### Verify or update an existing PS5 title without rebuilding

First inspect/download `playden/icon.png` from the actual installed game folder
over existing file access and compare its hash with the Fabric hash above. If
that copy differs, the console does not have the inspected local PNG. If it
matches, inspect the existing native title/Home artwork to distinguish retained
Minecraft art from a Home-screen cache that has not refreshed.

For an artwork-only visual check, use
[Art Swap's own-picture icon replacement](https://github.com/griches/PS5_ArtSwap#your-own-pictures)
on one existing Fabric title: supply the inspected package's `playden/icon.png`,
select it on the **Icons** tab, preview and apply it, then **Apply changes** to
refresh Home. Its documented icon path updates both title and Home-screen copies
and retains originals. Change only the icon; no game reinstall, recipe change,
Java/preparation change or Minecraft gameplay test is needed for this check.
This verifies the artwork/display path, not that automatic first-import seeding
ran on the previous import. A separate genuinely fresh title identity would be
needed to test that seeding physically.

No packager defect was reproduced and no runtime/build/transfer behavior was
changed. Existing artwork, GUI preferences and GUI preparation tests: 79 passed,
5 optional upstream tests skipped; rerunning the artwork suite with the actual
upstream decoder enabled: 40 passed, none skipped. Local hashes and converted
outputs are recorded in `dist/artwork-investigation-20261125/findings.json` and
its sibling output directories. The original physical packages were not edited.
