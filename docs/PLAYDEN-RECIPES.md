# Playden game-folder recipes

Verified against upstream Playden `develop` commit
[`3f973f4eb215cee894efa37b08a3f5bf9df4daaa`](https://github.com/leolobato/playden-ps5/commit/3f973f4eb215cee894efa37b08a3f5bf9df4daaa),
on 2026-11-25. This implements upstream [issue #1034](https://github.com/leolobato/playden-ps5/issues/1034).
This contract is pinned to the inspected source; no released version number is
assumed here.

## Discovery and consent

[`LocalLibrarySource::KeepFolderRecipe`](https://github.com/leolobato/playden-ps5/blob/3f973f4eb215cee894efa37b08a3f5bf9df4daaa/src/stores/local/library_source.cpp)
reads `playden/recipe.json` inside the imported game folder after adding the
game. It requires a regular file, refuses symlinks and limits the file to 16 KiB.
`FolderRecipeKeeper` routes it through `WebRecipeImports::Keep`, which calls
[`PrepareImportedRecipe`](https://github.com/leolobato/playden-ps5/blob/3f973f4eb215cee894efa37b08a3f5bf9df4daaa/src/community/recipe_import.cpp).
That uses the community parser, field allowlists and community refusals, rewrites
the ID to an imported content hash, and drops locks. A refused recipe leaves the
game imported and reports the problem. The original file stays in the folder.

[`Simulation::OfferFolderRecipe` and `ImportRecipe`](https://github.com/leolobato/playden-ps5/blob/3f973f4eb215cee894efa37b08a3f5bf9df4daaa/src/homebrew/simulation.cpp)
offer **Use recipe** or **Skip**, including for imports started from web Files.
The folder's recipe applies to that copy even when its game key differs from the
folder name. Accepting replaces existing game settings; the prompt warns when
settings already exist. Skipping preserves them. The packager itself never
edits Playden's settings or overwrites an existing output directory or recipe.

## Generated contract

Recipes and artwork live together in the package-root `playden/` metadata directory.
The executable and working directory remain relative to the game/package root. Both Vanilla and Fabric include:

```text
<package>/playden/recipe.json
<package>/playden/icon.png
<package>/playden/background.png
<package>/playden.args
<package>/java/bin/java.exe
<package>/instances/default/minecraft/
```

The [format-1 schema](https://github.com/leolobato/playden-ps5/blob/3f973f4eb215cee894efa37b08a3f5bf9df4daaa/docs/community-recipes.md)
uses `recipe_id`, `recipe_version: 1`, a `game` object with `store`, `key`, `name`,
`launch_entries`, matching `executable`, and a `community` block with
`format_version: 1` and `status: "untested"`. Our recipe selects
`java/bin/java.exe`, arguments `@../../playden.args`, `architecture: "pe64"`,
and `graphics: "opengl"`. No environment overrides are needed or generated.
Omitting `working_directory` uses the executable's parent (`java/bin`), so both
the argument file reference and all its portable paths resolve correctly.
Fabric's argument file selects KnotClient; Vanilla selects its metadata main class.
For Java 8, the recipe instead selects the generated manifest/bootstrap adapter
while preserving those original arguments; see [Java 8 launch support](JAVA8-LAUNCH.md).
Compatibility ratings remain unchanged.

USB copies preserve this root under `playden/import/<package>/`; LAN transfers
preserve it under `/data/playden-import/<package>/` or
`/mnt/usb0/playden/import/<package>/`. Recipe bytes stay in the immutable SHA-256
inventory. Path, symlink, integrity, staging and no-overwrite checks still apply.
Older Playden builds can use the documented manual launch settings, or their web
recipe import where supported.

## Metadata directory and artwork

See [artwork inputs, decoder limits and legacy compatibility](ARTWORK-METADATA.md).
New packages contain only `playden/recipe.json`, with the supplied default icon
and background PNGs alongside it. The GUI remembers optional custom PNG paths;
CLI overrides are `--icon PATH` and `--background PATH`. These images are covered
by the immutable inventory and transferred unchanged over USB or LAN. Playden
performs the icon crop and 16:9 background conversion on the console.

The packager and rebuilt receiver still validate/transfer older packages with
root-level `recipe.json` and no artwork. Both recipe locations together are
refused as ambiguous. Current upstream discovers only `playden/recipe.json`;
legacy packages need rebuilding for automatic recipe discovery on that upstream.
Nothing is rewritten in place, and no fallback root recipe is generated.

## Optional Minecraft controller layout

Vanilla and Fabric recipes now include Minecraft keyboard/mouse mappings by
default. The GUI checkbox **Use Minecraft keyboard/mouse controller layout**
remembers its value per user. CLI builds default to enabled independently of GUI
preferences; pass `--no-controller-layout` for Controlify or another controller
mod. Disabled recipes omit `defaults` entirely, preserving the previous recipe
configuration and leaving controller mode and mappings to Playden/the player.
Neither choice changes `playden.args`, Minecraft options, mods or source files.

The enabled recipe adds only `defaults.controller: "keyboard"` and
`defaults.controller_layout`, the input-map lines joined by semicolons. Verified
against current Playden `develop` commit
[`3f973f4eb215cee894efa37b08a3f5bf9df4daaa`](https://github.com/leolobato/playden-ps5/commit/3f973f4eb215cee894efa37b08a3f5bf9df4daaa)
on 2026-11-24: `src/runtime/controller_layout.{h,cpp}`, the settings catalogue in
`src/runtime/game_settings.cpp`, and profile validation in
`src/runtime/prospero_profile.cpp`. The layout in
[packager issue #63](https://github.com/isaacm0880/Minecraft-Playden-Packager/issues/63)
records the experimental Minecraft recipe's bindings.

| Input | Binding |
| --- | --- |
| Left stick | W/S/A/D (up/down/left/right) |
| Right stick | Mouse movement |
| L3 / R3 | Left Ctrl (sprint) / Left Shift (sneak) |
| Cross / Circle | Space / Q (drop) |
| Square / Triangle | E (inventory) / F (swap hands) |
| L1 / R1 | Mouse wheel up / down |
| L2 / R2 | Right / left mouse |
| D-pad up / down | L (advancements) / F3 (debug) |
| D-pad left / right | T (chat) / Middle mouse (pick block) |
| Touchpad left / right | Tab (player list) / F5 (perspective) |
| Options / Create | Escape / F2 (screenshot) |

This assumes Minecraft's default keybinds. Custom keybinds and mods can change
what those keys do; older versions may lack an action or offer different key
settings. Sprint and sneak follow Minecraft's own Hold/Toggle choices; players
can choose Toggle in Accessibility settings where available. The packager does
not alter those choices. Playden's reserved controller shortcuts remain reserved.

Playden must recognize `controller_layout` (catalogue `since: 0.13.0`) and the
runtime kit must advertise `input-map`. A current Playden with an older kit keeps
the layout waiting for a kit update and does not apply its keyboard mode early.
A Playden that predates the setting can refuse the enabled recipe; opt out to
retain the previous recipe format. No minimum physical compatibility claim is
inferred from parser acceptance. Existing Minecraft compatibility ratings and
previous physical results are unchanged; this option does not require retesting
previously working versions.

Validation accepts exactly the full built-in layout or the previous recipe
without controller defaults. Partial layouts, changed mappings and unrelated
defaults are refused. The recipe stays covered by the immutable SHA-256 inventory
and is transferred unchanged by USB and LAN.
The LAN receiver also validates these two exact configurations. Rebuild/restart
its ELF from this checkout for enabled-layout packages; an older binary's strict
allowlist rejects the added defaults. Opt-out packages retain the prior format.
No transfer protocol or pairing change is involved.

## JVM defaults

Only automatic `default-user-jvm` metadata is adapted. `-XX:+UseZGC` is omitted;
explicit `jvm` and installed `+jvmArgs` remain intact, including explicit ZGC.
Fabric retains these sections separately so provenance is not lost when merging.
Minecraft 26.3 / Java 25 defaults are `-Xms2G`, `-Xmx4G`,
`-XX:+UseCompactObjectHeaders`, `-XX:+AlwaysPreTouch`, and
`-XX:+UseStringDeduplication`, followed by normal metadata launch arguments.
Older metadata without a defaults section still uses the existing `-Xmx4G`.
The explicit `reference-26.3` profile retains its original `-Xmx4G` settings.

Compact headers require Java 25+, or Java 24 with an earlier experimental unlock
in the supplied defaults ([JEP 519](https://openjdk.org/jeps/519),
[JEP 450](https://openjdk.org/jeps/450)); otherwise that automatic option is omitted.
Java 8 uses the [dedicated adapter](JAVA8-LAUNCH.md); Java 9+ retains the
argument-file launch above. Controller defaults work with either invocation.

## Automated and physical verification

`tests/test_jvm_defaults.py` checks defaults, runtime gating and explicit additions.
GUI/CLI parity tests build Vanilla and Fabric from Prism, MultiMC and official
layouts with real default metadata. Recipe tests check schema, paths, size and
custom recipe preservation; USB and loopback C receiver tests check layout and
integrity. Existing package tests check inventory tampering and missing files.

The controller regressions also cover enabled/disabled CLI and desktop builds,
GUI persistence, unchanged argument bytes, and USB/LAN integrity. The upstream
checker verifies every rendered mapping with `input-map` and the deferred layout
without that capability (`--no-input-map`).

For integration, build `tools/playden_recipe_check.cpp` against the controller-schema commit above,
using unmodified upstream source. It exercises the actual folder import preparation,
parser, community rules, effective launch settings and rendered runtime profile.
Set `PLAYDEN_RECIPE_CHECK` to the resulting executable when running pytest.
On Linux with Mbed TLS 3 headers/library installed, from the packager root:

```sh
PLAYDEN_SOURCE=/path/to/playden-ps5
CHECK_BUILD=/tmp/playden-recipe-check
mkdir -p "$CHECK_BUILD"
cc -O2 -c "$PLAYDEN_SOURCE/third_party/utf8proc/utf8proc.c" -o "$CHECK_BUILD/utf8proc.o"
c++ -std=c++20 -O1 -ffunction-sections -fdata-sections \
  -I "$PLAYDEN_SOURCE/src" -I "$PLAYDEN_SOURCE/third_party/utf8proc" \
  -I /usr/include/mbedtls3 \
  tools/playden_recipe_check.cpp \
  "$PLAYDEN_SOURCE"/src/runtime/{game_settings,controller_layout,community_rules,prospero_profile,kit_version,artifact_hash}.cpp \
  "$PLAYDEN_SOURCE"/src/library/{game_ref,store}.cpp \
  "$PLAYDEN_SOURCE"/src/community/{recipe_import,api_client}.cpp \
  "$PLAYDEN_SOURCE"/src/steam/backend/{json,game_path}.cpp \
  "$CHECK_BUILD/utf8proc.o" -Wl,--gc-sections -L /usr/lib/mbedtls3 -lmbedcrypto \
  -o "$CHECK_BUILD/playden_recipe_check"
QT_QPA_PLATFORM=offscreen PLAYDEN_RECIPE_CHECK="$CHECK_BUILD/playden_recipe_check" \
  python -m pytest -q
```

Adjust the Mbed TLS include/library paths for the host. No Playden code or built
checker is distributed with the packager.

Optional physical checks for the new layout: on a Playden and kit supporting
`input-map`, accept the recipe for a fresh import and try its bindings, then try
an opt-out package with a controller mod. Skipping the offered recipe preserves
custom Playden settings. These are targeted checks of the new option, not a
requirement to retest previously working Minecraft versions. Host tests do not
establish new PS5 compatibility claims. No console deployment or release is part
of this change.
