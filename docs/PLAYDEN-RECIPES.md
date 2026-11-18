# Playden game-folder recipes

Verified against upstream Playden `develop` commit
[`472997d009c4cb6a07a707b6b8dc9bff9e4bb135`](https://github.com/leolobato/playden-ps5/commit/472997d009c4cb6a07a707b6b8dc9bff9e4bb135),
on 2026-11-18. This implements upstream [issue #1034](https://github.com/leolobato/playden-ps5/issues/1034).
The current `main` commit `a455e4c0ec4918a0473f5b9e86e8413622c1ddb6` does
not contain this discovery path. No released version number is assumed here.

## Discovery and consent

[`LocalLibrarySource::KeepFolderRecipe`](https://github.com/leolobato/playden-ps5/blob/472997d009c4cb6a07a707b6b8dc9bff9e4bb135/src/stores/local/library_source.cpp)
reads exactly `recipe.json` at the imported game folder's root after adding the
game. It requires a regular file, refuses symlinks and limits the file to 16 KiB.
`FolderRecipeKeeper` routes it through `WebRecipeImports::Keep`, which calls
[`PrepareImportedRecipe`](https://github.com/leolobato/playden-ps5/blob/472997d009c4cb6a07a707b6b8dc9bff9e4bb135/src/community/recipe_import.cpp).
That uses the community parser, field allowlists and community refusals, rewrites
the ID to an imported content hash, and drops locks. A refused recipe leaves the
game imported and reports the problem. The original file stays in the folder.

[`Simulation::OfferFolderRecipe` and `ImportRecipe`](https://github.com/leolobato/playden-ps5/blob/472997d009c4cb6a07a707b6b8dc9bff9e4bb135/src/homebrew/simulation.cpp)
offer **Use recipe** or **Skip**, including for imports started from web Files.
The folder's recipe applies to that copy even when its game key differs from the
folder name. Accepting replaces existing game settings; the prompt warns when
settings already exist. Skipping preserves them. The packager itself never
edits Playden's settings or overwrites an existing output directory or recipe.

## Generated contract

The existing package layout already matches discovery; no second recipe or move
into `instances/default/minecraft` is needed. Both Vanilla and Fabric include:

```text
<package>/recipe.json
<package>/playden.args
<package>/java/bin/java.exe
<package>/instances/default/minecraft/
```

The [format-1 schema](https://github.com/leolobato/playden-ps5/blob/472997d009c4cb6a07a707b6b8dc9bff9e4bb135/docs/community-recipes.md)
uses `recipe_id`, `recipe_version: 1`, a `game` object with `store`, `key`, `name`,
`launch_entries`, matching `executable`, and a `community` block with
`format_version: 1` and `status: "untested"`. Our recipe selects
`java/bin/java.exe`, arguments `@../../playden.args`, `architecture: "pe64"`,
and `graphics: "opengl"`. No environment overrides are needed or generated.
Omitting `working_directory` uses the executable's parent (`java/bin`), so both
the argument file reference and all its portable paths resolve correctly.
Fabric's argument file selects KnotClient; Vanilla selects its metadata main class.
Compatibility ratings remain unchanged.

USB copies preserve this root under `playden/import/<package>/`; LAN transfers
preserve it under `/data/playden-import/<package>/` or
`/mnt/usb0/playden/import/<package>/`. Recipe bytes stay in the immutable SHA-256
inventory. Path, symlink, integrity, staging and no-overwrite checks still apply.
Older Playden builds can use the documented manual launch settings, or their web
recipe import where supported.

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
Java 8 remains blocked by its lack of launcher argument-file support.

## Automated and physical verification

`tests/test_jvm_defaults.py` checks defaults, runtime gating and explicit additions.
GUI/CLI parity tests build Vanilla and Fabric from Prism, MultiMC and official
layouts with real default metadata. Recipe tests check schema, paths, size and
custom recipe preservation; USB and loopback C receiver tests check layout and
integrity. Existing package tests check inventory tampering and missing files.

For integration, build `tools/playden_recipe_check.cpp` against the pinned,
unmodified upstream source. It exercises the actual folder import preparation,
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
  "$PLAYDEN_SOURCE"/src/runtime/{game_settings,community_rules,prospero_profile,kit_version,artifact_hash}.cpp \
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

Physical PS5 checks remain: use a build containing upstream #1034, import fresh
Vanilla/Fabric packages through USB and LAN, accept the recipe, and check launch,
world loading/saving and controls. Reimport a customised game and verify Skip
keeps settings and Use recipe replaces them after its warning. Confirm manual
setup still works on an older Playden. Host tests do not establish PS5 launch
compatibility. No console deployment or release is part of this change.
