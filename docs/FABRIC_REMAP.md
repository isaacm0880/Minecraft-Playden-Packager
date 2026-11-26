# Headless Fabric remapping preparation

## Failure and evidence

Physical testing on Playden `0.12.0-nightly.20261116.1` confirmed Vanilla 1.20.1
reaching the main menu. Fabric 1.20.1 with Loader 0.20.1 originally failed while
opening `client-intermediary.jar.tmp`. Physical retesting now confirms **1.20.1 +
Fabric Loader 0.20.1 reaching the main menu on PS5 with native pre-remapping**.
This establishes startup, not world loading or every Fabric mod. Existing 26.3
confirmations are unchanged. The 2026-11-25 physical results additionally confirm
automatically prepared Fabric 1.14.4 and 1.16 (Loader 0.20.1) working through
gameplay and save/reload, plus reported Fabric 1.17.1 and 1.18.2 success (exact
loader versions not recorded). Automatic preparation and recipe/controller/artwork
integration are confirmed; see [physical results](COMPATIBILITY.md).

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

The later [Java 8 investigation](FABRIC-JAVA8-INVESTIGATION.md) reproduced this
exact leaf failure on desktop Wine: a Windows file-store query fails even though
an ordinary write channel opens successfully. Both absolute and relative paths
are affected there. Prepared caches reuse successfully in that environment;
the PS5's underlying native access-check failure still needs console evidence.

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
are removed. The GUI uses this existing remapper automatically when required.
The Prepare Fabric for Playden checkbox allows an explicit troubleshooting opt-out;
CLI builds now make the same automatic decision by default. `--prepare-fabric`
remains accepted; `--no-prepare-fabric` is an explicit troubleshooting opt-out
with a warning. The shared exporter defaults to automatic preparation too.

A bundled Java **source** adapter uses the native JDK source-file launcher, so no
helper executable, downloaded remapper version or Java build framework is needed.
Use a host-native JDK 17+ with its compiler module available. On Windows, the
packaged Windows JDK is a discovery candidate alongside native installed JDKs.
Linux/macOS check JAVA_HOME, PATH and common platform-specific JDK directories.
The worker probes Java 17+, the compiler module and native OS before preparation;
a foreign Windows executable is not launched through Wine on Linux/macOS. Use the
GUI Host preparation JDK field or CLI `--remap-java` to select an executable.
No native runtime is acquired automatically. The actual
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
cache on its next launch. A matching cache with a valid preparation record is
reused without launching Java again. An arbitrary installed first-launch cache is
never trusted; changed Minecraft, Fabric, mappings or dependencies require fresh
preparation.
Worlds, mods, settings and original source files remain separate from this cache.

26.3 profiles without intermediary remapping need no Java preparation worker;
automatic GUI builds skip preparation and leave their launch configuration intact;
explicit CLI preparation can record `not-required`.
Legacy/unprepared packages remain structurally valid without a preparation record.

## Automatic GUI workflow

1. Run the source GUI (`python -m minecraft_playden gui`) and rescan/add the source.
2. Select the installed Fabric instance. **Prepare Fabric for Playden** defaults
   on if the metadata requires remapping or the requirement still needs resolving.
   Vanilla and known configurations without remapping do not enable the checkbox.
3. Leave **Host preparation JDK** blank for discovery, or select native JDK 17+
   with **Choose Java…**. This is separate from the Windows runtime packaged for PS5.
4. Click **Build**. The same background worker resolves dependencies, builds,
   validates native Java, prepares Fabric, validates the cache and finalises the
   package. Cancellation/failure cleans staging and publishes no incomplete output.
5. Transfer the successful output through the existing USB/LAN actions.

For an external package, use **Validate existing package…** followed by **Prepare
Fabric…**, choose an output parent, and transfer the resulting separate copy.
The status clearly reports when a matching validated cache already needs no work.
Legacy unprepared packages remain transferable with a warning.

## CLI workflow and PS5 testing

Close Minecraft and use the original installed **1.20.1 + Fabric Loader 0.20.1**
instance. For a GUI-built or existing validated package, create a prepared copy:

```bash
python -m minecraft_playden prepare-fabric "/path/to/Fabric 1.20.1 Package" \
  --output "/path/to/Fabric 1.20.1 Prepared" --remap-java /usr/bin/java
python -m minecraft_playden validate "/path/to/Fabric 1.20.1 Prepared"
```

New CLI builds prepare automatically when required:

```bash
python -m minecraft_playden build --source local --instance "/path/to/instance" \
  --output "/path/to/Fabric 1.20.1 Prepared" \
  --remap-java /usr/bin/java --java-runtime "/path/to/windows-x64-java-17"
```

On Windows, omit `--remap-java` to discover an installed host JDK 17+ or supply
the path to its `java.exe`. A first build still needs the same public metadata/assets
as before; once installed/cached, `build --offline` with these preparation flags
makes a prepared rebuild without HTTP. `prepare-fabric` itself has no downloader.
Neither command overwrites an existing output. A failed/cancelled preparation
publishes no partial package.

Transfer **the prepared output** using the existing GUI's Validate existing
package / USB / LAN flow. Choose a fresh PS5 import name. Keep the complete
package, including the hidden `.fabric` directory and `metadata/fabric-remap.json`.
For Java 9+, use the unchanged Playden configuration:

```text
Program: java/bin/java.exe
Launch arguments: @../../playden.args
```

Java 8 uses its unchanged adapter command from `playden.java8-command` and
`PLAYDEN.txt`, automatically included in the recipe. A host JDK 17+ performing
preparation does not replace the metadata-selected Windows Java 8 game runtime.
See [Java 8 build provenance and investigation](FABRIC-JAVA8-INVESTIGATION.md).

Test on the same external USB destination first; no storage migration is assumed.
Inspect the selected import for the completed remapped JAR and absence of its
`.tmp` sibling. Launch once and record the full log and Playden version. Fabric
should reuse the prepared game JAR rather than report first-launch game remapping.
If it still attempts that operation, preserve the actual cache path and complete
exception chain. The prepared 1.20.1/0.20.1 configuration has reached the main menu on PS5; repeat
startup and world/mod tests for a new configuration. Mod discovery, mod-specific
caches, graphics and other Playden filesystem
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
These historical automated results do not establish native Windows preparation
acceptance. Subsequent owner testing confirmed PS5 main-menu startup for prepared
1.20.1/0.20.1; world loading, other mod combinations and new platform setups still
need acceptance testing.

## Automatic GUI integration verification

The GUI integration adds native-JDK discovery/probing and uses the same remapper.
Its source regression run passed **805 tests** with **3 skipped**, plus **345
subtests**. Enabling local real-loader inputs passed **43 host-JDK, GUI and cache
checks**, including the actual Fabric remapper. A further **43 receiver lifecycle,
GUI receiver and prepared-cache publication checks** passed under address and
undefined-behavior sanitizers. All three mypy platform targets passed for 62 source
files, and the public-source audit passed for 265 source files.

GUI tests cover automatic preparation, metadata changes after scanning, Vanilla/
26.3 bypass, explicit opt-out, missing Java, manual selection, cancellation of
builds and existing-package preparation, validated cache reuse, legacy transfer
warnings and prepared cache preservation during verified USB copying. The existing
actual-C-receiver regression also preserves the prepared cache over LAN.

To check the source GUI manually on Linux:

```bash
.venv/bin/python -m minecraft_playden gui
```

Select a complete 1.20.1/Fabric 0.20.1 installation. The preparation checkbox should
be checked. Leave Host preparation JDK blank to use automatic discovery (or select
a native JDK 17+ executable), choose a new output and click Build. Confirm Java
selection, Preparing Fabric, cache validation and finalisation appear in the log;
only then should transfer be offered. Select Vanilla or a 26.3 profile without
remapping and confirm no remapper is launched. For an existing unprepared package,
validate it, prepare a separate copy, then validate that copy again: it should
report a matching cache and no further preparation necessary.

Native Windows/macOS GUI and JDK acceptance remain platform-specific manual
checks; mocked discovery/type checks do not establish desktop acceptance there.
