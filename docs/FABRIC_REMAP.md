# Headless Fabric remapping preparation

## Failure and evidence

Physical testing on Playden `0.12.0-nightly.20261116.1` confirmed Vanilla 1.20.1
working, and Fabric 1.20.1 with Loader 0.20.1 failing while opening
`client-intermediary.jar.tmp`. The corrected preparation below is **not yet verified
on PS5**. Existing 26.3 confirmations are unchanged.

For obfuscated Minecraft, Fabric normally transforms the official client into the
intermediary namespace on first launch. The official client, matching intermediary
JAR, Knot main class and loader dependencies belong in the package; shipping the
official client is expected. Structural validation alone never exercised this
first-launch Java operation.

Loader 0.20.1 bundles Tiny Remapper 0.15.0. Its `OutputConsumerPath` opens the output
as a Java ZIP filesystem and explicitly throws the reported "can't be written"
exception when that filesystem reports `isReadOnly()`. This particular throw has
no nested cause. In OpenJDK 17, the ZIP filesystem initializes its read-only flag
from `Files.isWritable` on the archive. Directory creation/opening has already
progressed to the ZIP filesystem by that point. These facts distinguish this
failure from missing intermediary mappings or an absent client JAR.

**The precise reason Playden's Windows Java filesystem reports that archive as
read-only is not established by the supplied trace.** Windows access checks,
attributes or a reused filesystem may be involved. We have not reproduced the
physical console's access-check behavior. USB storage alone is not evidence of
cause, and moving to internal storage is not presented as a fix. Other errors
from the same outer remapping wrapper may have a different leaf cause; retain the
complete exception chain when reporting them.

Source references:

- [Fabric 0.20.1 game initialization](https://github.com/FabricMC/fabric-loader/blob/0.20.1/minecraft/src/main/java/net/fabricmc/loader/impl/game/minecraft/MinecraftGameProvider.java)
- [Fabric cache lookup and remapping](https://github.com/FabricMC/fabric-loader/blob/0.20.1/src/main/java/net/fabricmc/loader/impl/game/GameProviderHelper.java)
- [Bundled remapper version](https://github.com/FabricMC/fabric-loader/blob/0.20.1/build.gradle)
- [Exact Tiny Remapper 0.15.0 source archive](https://maven.fabricmc.net/net/fabricmc/tiny-remapper/0.15.0/tiny-remapper-0.15.0-sources.jar) — SHA-256 `81ece619daae89abb2eb7057fa0087fc180856cf49786c231694673963333478`
- [OpenJDK 17 ZIP filesystem](https://github.com/openjdk/jdk17u/blob/master/src/jdk.zipfs/share/classes/jdk/nio/zipfs/ZipFileSystem.java)

## Selected approach

The explicit preparation step runs the **selected installed loader's own
`GameProviderHelper.deobfuscate` method** in an isolated native Java worker. It
never calls Knot, initializes Minecraft, scans mods, signs in or fetches anything.
Only selected local library JARs enter the worker classloader; the client JAR is
read as remapping input. Java option/classpath injection environment variables
are removed. This is an opt-in execution of the installed loader/remapper code;
ordinary builds remain file-only packaging.

A bundled Java **source** adapter uses the native JDK source-file launcher, so no
helper executable, downloaded remapper version or Java build framework is needed.
Use a host-native JDK 17+ with its compiler module available. On Windows, the
packaged Windows JDK is the default preparation executable. Linux/macOS use native
`java` on PATH or `--remap-java`; no runtime is acquired automatically. The actual
Playden launch still uses the package's metadata-selected Windows runtime.

Preparation starts in a fresh temporary game directory, with the exact client,
intermediary mappings and installed loader/classpath. The same loader normalizes
the Minecraft version and chooses its cache filename; this avoids inventing
snapshot/version-specific cache rules. Both current and older method signatures
are adapted by capability, not a Minecraft/loader version allowlist. An unknown
API fails explicitly. Custom game-JAR/namespace/version overrides require a
separate adapter rather than silently preparing the wrong cache.

The result is copied into the normal relative game cache:

```text
instances/default/minecraft/.fabric/remappedJars/
  minecraft-1.20.1-0.20.1/client-intermediary.jar
```

Fabric checks for this completed cache before constructing its temporary output
ZIP filesystem. Consequently this approach avoids the specific failing game-JAR
remapping write operation on PS5; it does not suppress an exception or disable
loader/package validation. It retains the official client and original launch
arguments/classpath. No absolute host path is stored in the preparation record.

`metadata/fabric-remap.json` binds preparation to SHA-256 hashes of the client,
selected libraries/mappings, metadata and launch arguments. The output JAR is
inventoried, all ZIP members/CRCs are checked, and expected mapped client classes
must be present. Missing, changed, incomplete or stale inputs fail validation.
A neighboring `.jar.tmp` is rejected because Fabric would delete the completed
cache on its next launch. Preparation never reuses an installed remapping cache;
changing Minecraft, Fabric, mappings or dependencies requires a fresh preparation.
Worlds, mods, settings and original source files remain separate from this cache.

26.3 profiles without intermediary remapping need no Java preparation worker;
preparation records `not-required` and leaves their launch configuration intact.
Legacy/unprepared packages remain structurally valid without a preparation record.

## Rebuild and test on PS5

Close Minecraft and use the original installed **1.20.1 + Fabric Loader 0.20.1**
instance. For a GUI-built or existing validated package, create a prepared copy:

```bash
python -m minecraft_playden prepare-fabric "/path/to/Fabric 1.20.1 Package" \
  --output "/path/to/Fabric 1.20.1 Prepared" --remap-java /usr/bin/java
python -m minecraft_playden validate "/path/to/Fabric 1.20.1 Prepared"
```

Alternatively prepare during a new CLI build:

```bash
python -m minecraft_playden build --source local --instance "/path/to/instance" \
  --output "/path/to/Fabric 1.20.1 Prepared" --prepare-fabric \
  --remap-java /usr/bin/java --java-runtime "/path/to/windows-x64-java-17"
```

On Windows, omit `--remap-java` to use the packaged JDK or supply the path to a
native JDK's `java.exe`. A first build still needs the same public metadata/assets
as before; once installed/cached, `build --offline` with these preparation flags
makes a prepared rebuild without HTTP. `prepare-fabric` itself has no downloader.
Neither command overwrites an existing output. A failed/cancelled preparation
publishes no partial package.

Transfer **the prepared output** using the existing GUI's Validate existing
package / USB / LAN flow. Choose a fresh PS5 import name. Keep the complete
package, including the hidden `.fabric` directory and `metadata/fabric-remap.json`.
Use the unchanged Playden configuration:

```text
Program: java/bin/java.exe
Launch arguments: @../../playden.args
```

Test on the same external USB destination first; no storage migration is assumed.
Inspect the selected import for the completed remapped JAR and absence of its
`.tmp` sibling. Launch once and record the full log and Playden version. Fabric
should reuse the prepared game JAR rather than report first-launch game remapping.
If it still attempts that operation, preserve the actual cache path and complete
exception chain. Do not mark the configuration working until a physical launch
succeeds. Mod discovery, mod-specific caches, graphics and other Playden filesystem
operations can still expose independent runtime issues.

## Why Wine is not required

Launching Windows Java through Wine could also generate Fabric's cache, but a full
Minecraft launch executes installed mods and may make network requests or write
unrelated first-launch state. Wine success does not establish Playden success,
and copying an arbitrary `.fabric` tree does not validate its inputs. The native
headless path prepares just the relevant portable game cache without graphics,
authentication or Wine. No automatic Wine launch or mandatory Wine dependency has
been added. A separate Wine/Playden filesystem probe would be useful if diagnosing
why the target's writable-access query fails; it is unnecessary for cache creation.

## Regression evidence

The suite exercises 1.20.1/0.20.1 preparation and offline rebuilds, stale/incomplete
cache rejection, input invalidation, preserved user files, failure/cancellation,
26.3/0.20.1 with no remapping, and legacy package validation. The existing matrix
still covers Vanilla 1.20.1 and other Minecraft/Fabric generations.

An opt-in integration test uses actual installed Fabric/ASM/intermediary JARs and
the real local Minecraft 1.20.1 client, with no network acquisition:

```bash
PLAYDEN_FABRIC_LIBRARIES=/path/to/launcher/libraries \
PLAYDEN_FABRIC_CLIENT=/path/to/minecraft-1.20.1-client.jar \
python -m pytest -q tests/test_fabric_remap.py -k real_loader
```

The integration checks real output class mappings/CRC completeness and a second
loader invocation that reuses the cache without rewriting it. It does not execute
the game or establish PS5 runtime compatibility. Proprietary client files,
remapped JARs and compiled helpers are never committed to the repository.

Verification for this change on the Linux development host:

- Full regression run: 780 passed, 3 skipped, 344 subtests passed. Two skips are
  the opt-in real-loader integrations; the other is an existing environment skip.
- With local real inputs enabled: all 19 Fabric preparation tests passed,
  including actual Loader 0.20.1 and the older 0.14.23 API with their declared
  dependencies. The real 1.20.1 output contained 7,436 classes and was reused
  without rewriting on a second invocation.
- The subsequently added prepared-cache LAN publication regression passed against
  the actual host C receiver with address/undefined-behavior sanitizers enabled;
  every transferred byte matched and the destination package validated.
- Linux/Windows/macOS mypy targets passed for 61 source files. The public-source
  audit passed. No physical console was contacted, no Minecraft/mod entry point
  was launched and no desktop application binary or release was produced.

The optional older-loader integration skips when that local loader is absent.
These results do not establish native Windows preparation acceptance or PS5
launch success; both remain physical follow-up checks.
