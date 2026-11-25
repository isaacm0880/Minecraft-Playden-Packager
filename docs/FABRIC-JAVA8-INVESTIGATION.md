# Fabric Java 8 remapping investigation, 2026-11-25

## Original package provenance

The retained Codex execution transcript establishes the following sequence:

1. On November 24 at 23:37–23:38 UTC, the three Java 8 candidates were generated
   with the public `python -m minecraft_playden build` CLI, not a private exporter
   script. Fabric 1.14.4 used its installed Prism instance. The installed 1.16
   Prism instance was Vanilla: a separate minimal `mmc-pack.json` selected existing
   Minecraft 1.16 and Fabric Loader 0.20.1 components from the installed libraries.
   This was source-selection metadata, not a modified client or generated package.
2. On November 25 at 00:06 UTC, a Python loop invoked that same public CLI again
   for the artwork/metadata-directory rebuild. Each subprocess used the existing
   verified download cache, `--offline build --source local`, its instance path,
   `--libraries-root`, an explicit Windows x64 Java 8 home and a fresh `--output`.
3. Neither generation supplied `--prepare-fabric`. The CLI and shared exporter
   then defaulted preparation to false. The GUI's `LocalBuildRequest` already
   defaulted to automatic preparation. Preparation was skipped, not attempted
   unsuccessfully. No post-build package patching or remap validation bypass was
   found in the recorded procedure. Source/adapter changes happened before builds.
4. Both retained Fabric packages lack `metadata/fabric-remap.json` and the completed
   remapped client cache. Their preparation status is required=true, prepared=false.
   The old validation report checked structural validity, adapter bytes, runtime,
   recipe and artwork; it did not assert preparation status.

Before implementation changes, repeating the original public CLI command produced
an identical complete package inventory for Fabric 1.16, including all immutable
file hashes, and the same unprepared status. Explicitly adding the then-supported
`--prepare-fabric` successfully prepared the real 1.16 inputs with the existing
worker. Therefore this was an omitted preparation step plus inconsistent frontend
defaults, not evidence that the Java 8 launch adapter or remapper needed replacing.
New tests compare automatic GUI and CLI preparation for both affected versions.

## Confirmed failure condition and limits

Fabric Loader 0.20.1 embeds TinyRemapper 0.15.0. Its exact source archive has
SHA-256 `81ece619daae89abb2eb7057fa0087fc180856cf49786c231694673963333478`.
At `OutputConsumerPath.java:102`, the exception is thrown only when the opened
output ZIP filesystem reports read-only. It is not a generic failure to construct
its parent path. OpenJDK 8 ZIP filesystem initialization checks `Files.isWritable`
on the archive and records read-only when that query returns false.

The relative path in the report is the normal portable game directory resolved
from `java/bin`. It is not itself proof of a bad working directory. Desktop Java 8
and desktop Wine cache-reuse probes use this same relative game-directory shape.
The supplied console trace does not establish why the write-access query failed:
Playden/Wine access-check behavior, storage attributes/permissions or a reused ZIP
filesystem remain possible. Linux/desktop Wine results cannot establish the PS5's
USB semantics. No access-check bypass, runtime patch, storage migration or newer
game runtime is introduced.

Further desktop tests reproduced the exact `OutputConsumerPath.java:102` failure
with Windows Java 8u512 under Wine for both clients. Native Linux Java 8 can remap
the same inputs. An independently written small JDK probe found that, under this
desktop Wine setup, both relative and absolute archive paths report:

```text
File.canWrite() = true
opening a WRITE channel = success
DOS read-only attribute = false
Files.isWritable() = false
WindowsFileStore/checkAccess = FileSystemException: Not top-level directory
ZIP filesystem isReadOnly() = true
```

Thus this desktop reproduction is a Windows Java/Wine file-store access-query
failure, rather than an inability to write the archive or relative paths alone.
The same prepared caches are successfully reused by both native Java 8 and
Windows Java 8 under Wine, with no `.tmp` creation or cache rewrite. This is strong
evidence for avoiding that operation through preparation, not proof that PS5 uses
the same failing native call. The probe source/log are retained with the local
physical candidates under `diagnostics/`.

Fabric's `GameProviderHelper.deobfuscate` returns the completed cache before
constructing its temporary ZIP output. Preparing the exact cache on the host
therefore avoids this particular write operation. A neighboring `.tmp` would
invalidate that cache at startup; preparation and validation already reject it.
Caches stay bound to client, mappings, loader, libraries and launch arguments by
SHA-256, CRC and mapped-class completeness checks. Missing preparation remains a
separate readiness state: older packages are still structurally valid.

The older development checkout's Fabric implementation and documentation were
inspected read-only. It records the installed-component export and original 26.3
packages, without a separate headless remapping module to transplant. The current
branch already contains the more suitable verified preparation implementation.

The reported working Fabric 1.17.1 and 1.18.2 configurations use Java 16 and 17
respectively in Mojang metadata, rather than Java 8. Their supplied obfuscated
clients can still require intermediary remapping. The currently installed Prism
instances bearing those names are Vanilla, so their exact working Fabric loader
versions and previous preparation state cannot be reconstructed from those folders.
No physical retest of those configurations is required by this change.

## Implementation

GUI, CLI and the shared exporter now default to automatic preparation based on
actual mappings, without a Minecraft version allowlist. `--prepare-fabric` remains
accepted. `--no-prepare-fabric` explicitly skips preparation and warns that the
package may fail on first-launch remapping. CLI build and validation report the
preparation status. Missing/incompatible host JDK or preparation failures publish
no unprepared fallback. Frozen builds explicitly bundle the existing source helper.

Preparation still runs only the installed loader's own remapping helper in an
isolated native JDK 17+ worker. This is a build tool: the packaged Windows x64 Java
8 runtime, adapter, JVM/game arguments and controller recipe remain unchanged.
Vanilla and mappings that need no transformation skip the worker. USB and LAN
preserve the same inventoried preparation record and hidden cache.

ProsperoMC was inspected as technical reference at commit
`5cdcb996421565f7b528a5245fa31d18df3922de`. Its filesystem diagnostics distinguish
write-access queries from actual writes. This supports keeping that distinction
in the diagnosis; it does not prove the console's cause. No ProsperoMC source was
copied, translated, adapted or added as a dependency.

Loader artwork is a separate change; see [artwork metadata](ARTWORK-METADATA.md).
Forge, NeoForge and Quilt launch support remains disabled.

## Reproduction and verification

With a supported installed Fabric instance, verified Windows x64 Java 8 and a
native host JDK 17+, the ordinary workflow is:

```sh
python -m minecraft_playden --cache /path/to/cache --offline build \
  --source local --instance /path/to/fabric-instance \
  --libraries-root /path/to/launcher/libraries \
  --java-runtime /path/to/windows-java8 \
  --remap-java /path/to/host-jdk/bin/java --output /path/to/new-package
python -m minecraft_playden validate /path/to/new-package
```

No preparation flag, post-processing or manual cache copying is needed. Omit
`--offline` for a first build lacking cached public metadata/assets. Install the
exact client and Fabric components in the original launcher first; these are not
downloaded as a replacement by the packager. Source selection for the retained
1.16 candidate uses the separate minimal component manifest described above.

The rebuilt candidates and machine-readable validation report are under
`dist/physical-fabric-java8-20261125/`. Both report required=true, prepared=true,
loader-cache mode, and Windows x64 Java 8. Compare against the previous packages:
`playden.args`, adapter JAR, command sidecar, recipe and `java/release` are identical.
The completed cache and its receipt are new; the icon is the supplied Fabric icon.

Regression tests cover Tiny v1/v2 metadata, both Java 8 targets, automatic/default
and opt-out workflows, preparation failure, integrity, GUI reset/queued choices,
USB and the real C receiver on loopback. Optional real-loader tests use
`PLAYDEN_FABRIC_PREPARED` (parent containing `Fabric-1.14.4` and `Fabric-1.16`),
`PLAYDEN_JAVA8`, `PLAYDEN_JAVA8_WINDOWS` and `PLAYDEN_JAVA8_JAVAC`. They compile our
own host adapter for the test JVM, then exercise the installed loader's cache
lookup using a relative game directory and read-only completed cache without
starting Knot, Minecraft or mods. Fresh native remapping must succeed; fresh
Windows remapping may succeed or reproduce only the exact known read-only ZIP
failure, while prepared reuse must always succeed. Other failures are rejected.
Production preparation remains native JDK 17+.

The following was the acceptance procedure before the successful physical retest.
To reproduce on a new configuration, transfer the two prepared Fabric candidates to fresh USB
package folders, retaining hidden `.fabric` files. Import the generated recipe.
Confirm the cache filename matches the path in the earlier error, with no `.tmp`
sibling. Check Fabric startup/menu and, if desired, a disposable world's save and
reload. Record the complete log if it remaps again or fails later. Vanilla 1.16
already works according to the supplied physical result and needs no retest.
Automated validation and desktop cache reuse do not establish physical PS5 success.

Source references:

- [Fabric cache lookup](https://github.com/FabricMC/fabric-loader/blob/0.20.1/src/main/java/net/fabricmc/loader/impl/game/GameProviderHelper.java)
- [Loader dependency versions](https://github.com/FabricMC/fabric-loader/blob/0.20.1/build.gradle)
- [Exact TinyRemapper source archive](https://maven.fabricmc.net/net/fabricmc/tiny-remapper/0.15.0/tiny-remapper-0.15.0-sources.jar)
- [OpenJDK 8 ZIP filesystem](https://github.com/openjdk/jdk8u/blob/master/jdk/src/share/demo/nio/zipfs/src/com/sun/nio/zipfs/ZipFileSystem.java)
- [OpenJDK 8 Windows access checks](https://github.com/openjdk/jdk8u/blob/master/jdk/src/windows/classes/sun/nio/fs/WindowsFileSystemProvider.java)
- [OpenJDK 8 Windows file-store lookup](https://github.com/openjdk/jdk8u/blob/master/jdk/src/windows/classes/sun/nio/fs/WindowsFileStore.java)
- [ProsperoMC reference](https://github.com/Ayoubec/ProsperoMC/tree/5cdcb996421565f7b528a5245fa31d18df3922de)

## Physical resolution — 2026-11-25

The maintainer confirms both automatically prepared Fabric 1.14.4 and 1.16
packages work on PS5, including gameplay, controller mappings, world saving and
reloading. Vanilla 1.16, Fabric 1.17.1 and Fabric 1.18.2 are also confirmed
working. Automatic recipe detection and Fabric preparation work. The exact
loader versions for 1.17.1 and 1.18.2 were not recorded.

Default and loader-specific artwork work too. Playden's retained old game data
caused the apparent Fabric icon mismatch; removing that data resolved it. Both
local package PNGs were already correct, so no artwork defect was found.
The original remapping failures remain historical evidence for preparing the
cache automatically, not the current status of the prepared packages.

Issue #20 remains open because legacy main-class detection is still unresolved;
these physical results do not fix Minecraft 1.0 or add Forge support.
