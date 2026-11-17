# Compatibility

Packaging targets Windows x64 Java for Playden regardless of the host platform.
v0.1.0 binary targets are Windows x64 and Linux x86_64; manual acceptance is
pending. macOS Apple Silicon/Intel support is experimental, source-only and not
yet verified; there is no required macOS binary. See [installation](INSTALLATION.md).

| Status | Meaning |
| --- | --- |
| Works | A specific Minecraft version and loader combination has confirmed PS5 success; see its evidence and limits |
| Untested | No applicable PS5 confirmation; packaging still requires supported metadata, dependencies and runtime |
| Broken | A known launch failure or unresolved compatibility issue is recorded |

These short labels do not replace packaging readiness. Missing files or unsupported
formats block Build with a detailed reason, even when a row is labelled Untested.
Physical failures awaiting a retest remain Broken; automated packaging never promotes
a configuration to Works. Tooltips retain the detailed evidence. Internal states
also retain whether building is blocked or a candidate may be built for retesting.

Explicit Tested entries are Vanilla Minecraft 1.20.1, Vanilla Minecraft 26.3 and Minecraft 26.3 with Fabric
Loader 0.20.1 in a configuration without Ixeris. The 1.20.1 Fabric confirmation requires native pre-remapping and establishes reaching the main menu, not world loading or every mod. Other loader versions and arbitrary
modpacks are not certified. Successful packaging or checksums never add Works status.

The development source resolves Minecraft metadata, not a version allowlist.
It adapts structured and legacy game arguments, bounded metadata inheritance,
Windows x64 rules and native classifiers, current default JVM arguments, official
logging and indexed/virtual/resource assets. Fabric uses installed Minecraft
Launcher inheritance or ordered Prism/MultiMC components with the exact selected
loader and dependency overrides; no latest-loader substitution occurs. Java
requirements come from metadata, including supported Fabric overrides.

Three results remain distinct: **can package** is metadata capability and local
source readiness; **package validated** means the generated files/dependencies and
launch plan pass structural checks; **confirmed on PS5** requires separate physical
configuration evidence. Neither automated result updates the PS5 registry.

## Automated source packaging matrix

The following captured Mojang and Fabric profiles pass complete synthetic-payload
packaging, final validation and a second build with networking disabled across
Minecraft Launcher, Prism Launcher and MultiMC layouts. The actual metadata rules,
coordinates, launch arguments, client identity and runtime requirements are retained;
checksums are replaced in memory to match synthetic payloads. No game or mod runs.

| Minecraft | Required Windows x64 Java | Vanilla | Fabric profile tested | PS5 evidence |
| --- | --- | --- | --- | --- |
| 1.18.2 | 17 | Automated structural pass | 0.16.10: automated structural pass | Physical testing needed |
| 1.19.2 | 17 | Automated structural pass | 0.16.10: automated structural pass | Physical testing needed |
| 1.20.1 | 17 | Confirmed reaching the PS5 main menu | 0.16.10: automated structural pass; 0.20.1: confirmed reaching main menu with native preparation | Startup only; world loading and arbitrary mods remain unverified |
| 1.21.1 | 21 | Automated structural pass | 0.16.10: automated structural pass | Physical testing needed |
| 1.21.11 | 21 | Automated structural pass | 0.20.1: automated structural pass | Physical testing needed |
| 26.1.2 | 25 | Automated structural pass | 0.20.1: automated structural pass | Physical testing needed |
| 26.2 | 25 | Automated structural pass | 0.20.1: automated structural pass | Physical testing needed |
| 26.3 | 25 | Automated structural pass | 0.20.1: automated structural pass | Existing exact reports below; new launch defaults still need acceptance |
| 1.16.5 | 8 | Blocked: Java launcher argument files unavailable | Same base-runtime blocker | No new claim |

These loader versions are test inputs, not a support allowlist. Other releases
and snapshots can package when their actual metadata, installed client, selected
loader, runtime, dependencies and placeholders are supported. Tests also cover
synthetic inherited/snapshot identities, legacy argument strings and mapped asset
layouts. See [snapshot provenance](https://github.com/isaacm0880/Minecraft-Playden-Packager/blob/feat/ps5-network-transfer/tests/fixtures/versions/README.md),
[architecture audit](https://github.com/isaacm0880/Minecraft-Playden-Packager/blob/feat/ps5-network-transfer/docs/VERSION_COMPATIBILITY_AUDIT.md), `tests/test_version_matrix.py`
and `tests/test_runtime_selection.py`.

## Automatic GUI preparation

Select a Fabric instance and click Build. **Prepare Fabric for Playden** defaults
on when installed metadata indicates remapping is required (or still needs to be
resolved). The background exporter makes the final decision from the verified
mappings, never a Minecraft version list. Vanilla and Fabric with no remapping
work, including supported 26.3 configurations, bypass the Java worker. The checkbox
can be disabled explicitly for troubleshooting; the resulting unprepared package
receives a warning before GUI USB/LAN transfer.

Preparation uses a separate host-native JDK 17+ with `jdk.compiler`, discovered
from JAVA_HOME/PATH and common JDK locations on Linux, Windows and macOS. Windows
can also use the packaged JDK. **Host preparation JDK → Choose Java…** allows manual
selection. The worker validates the selected executable's major version, compiler
module and native OS. No native Java is downloaded; no Wine or PS5 SDK is needed.
If no suitable JDK is found, the requested build fails without publishing an
unprepared substitute.

After Validate existing package, **Prepare Fabric…** creates a separate validated
copy in a chosen parent directory. Matching validated caches need no further
preparation; stale inputs/caches fail validation. Prepared packages retain their
hidden `.fabric/remappedJars` files and `metadata/fabric-remap.json` during USB and
LAN transfer. Optional CLI `prepare-fabric` and `build --prepare-fabric` remain
available. The PS5 receiver protocol and ELF are unchanged.

## Runtime selection and remaining blockers

Selection requires the metadata's exact Java major, a Windows x64 release identity
and AMD64 PE `java.exe`/`server/jvm.dll`, without executing Java on the host.
It prefers explicit and known launcher-local runtimes, then a cached Java home or
previously verified runtime archive. A Linux, macOS, ARM64 or different-major Java
installation is not a substitute. These structural checks do not establish that
Temurin or its graphics/native dependencies run through Playden.

Missing runtime downloads are enabled by default. Matching verified local/cache
runtimes are preferred. Use CLI `--java-runtime PATH`, a Java home under
`CACHE/runtimes/java-MAJOR-windows-x64`, or disable downloads with
`--no-runtime-download` / Build advanced options. The download
path verifies the archive's SHA-256 and checks its extracted target and major.
The runtime archive and catalog remain cached. `--offline` forbids all HTTP;
metadata and any public files absent from both the installed launcher and cache
produce a missing-file error. Refresh the catalog explicitly with `versions` when
adding a version absent from a cached manifest.

Java 8 itself is identified and its release identity can be inspected, but the
preserved Playden configuration launches `java.exe @../../playden.args`. Java 8
cannot read Java launcher argument files; support needs a separately verified
launch adapter. No automatic Java 17 substitution is made. This blocks 1.16.5 and
Java 8-era releases, rather than rejecting them because of an absent version-list
entry. See the [Java 8 launcher reference](https://docs.oracle.com/javase/8/docs/technotes/tools/windows/java.html).

Arbitrary patched client JARs, non-Fabric custom profiles, legacy tweaker/JAR-mod
loaders, unknown argument formats/placeholders and inconsistent component metadata
still fail explicitly. Installed client files must match the official checksum.
Existing `reference-26.3` remains an explicit diagnostic reproduction; packages
created before the metadata adapter retain their original argument-validation
policy. No package-layout or Playden program/argument-path migration is needed.

Unconfirmed combinations, including Fabric 1.20.1 with other loader versions and Fabric 26.2, display:

> Unsupported version — not tested on PS5. Packaging will be attempted, but the game or some mods may not work.

Forge is Broken due to known unresolved Playden launch issues and cannot build.
NeoForge/Quilt launch packaging is not implemented; they are Cannot package, not
inferred Broken from Forge. Unverified CLI file-only imports do not provide launch
support. Missing clients/artifacts must be installed through the original launcher.

There is no authenticated multiplayer, Realms, online client acquisition or console
log collection. PS5 LAN transfer and explicit receiver management are available
on the development branch; they do not establish game/runtime compatibility.
Console game/mod/runtime acceptance remains configuration-specific.

## Known Minecraft Version Compatibility

This table tracks Minecraft Java Edition versions tested with **Playden on PS5**. Compatibility depends on the Minecraft version, mod loader and installed mods.

| Minecraft Version | Mod Loader | Loader Version | Status | Notes |
|---|---|---|---|---|
| 1.20.1 | Vanilla | N/A | Works | Confirmed startup on Playden 0.12.0-nightly.20261116.1; world loading not established |
| 1.20.1 | Fabric | 0.20.1 | Works | User-confirmed startup with native pre-remapping; world loading and every Fabric mod are not certified. The original unprepared package failed during first-launch remapping |
| 26.3 | Vanilla | N/A | Works | Confirmed working |
| 26.3 | Fabric | 0.20.1 | Works | Confirmed working |

For automatic GUI preparation and the original 1.20.1 failure investigation, see [Fabric preparation and physical test instructions](https://github.com/isaacm0880/Minecraft-Playden-Packager/blob/feat/ps5-network-transfer/docs/FABRIC_REMAP.md). The failure reaches a Java ZIP filesystem reporting read-only; USB location alone does not establish its cause.

**Status key:**

- Works — This version/loader combination has confirmed PS5 success within the recorded limits.
- Untested — No applicable physical PS5 result.
- Broken — A known launch failure or unresolved issue is recorded.

**Note:** Compatibility is specific to the tested configuration. Other versions, Fabric Loader versions and mod combinations are not guaranteed to work.

## Known Mod Compatibility

This table tracks mods that cause issues or break the package on playden. Compatibility may vary depending on the Minecraft version, Fabric Loader version and mod version.

| Mod                                       | Status        | Notes                                                          |
| ----------------------------------------- | ------------- | -------------------------------------------------------------- |
| [Ixeris](https://modrinth.com/mod/ixeris) | Broken | Known compatibility issues with Playden. Avoid using this mod. |

An unlisted mod means no known issue is recorded; it has not been verified working.
This tracker records **only known issues**, not a catalogue of working mods.
Minecraft/loader Works status never certifies every installed mod.

### Report Mod Issues

If a mod causes a reproducible issue, [open an issue](https://github.com/isaacm0880/Minecraft-Playden-Packager/issues/new)
with `:borked-mod` and include:

- Mod name and version
- Minecraft and Fabric Loader versions
- Failure symptoms or specific limitations
- Relevant errors and reproduction steps, with credentials removed

Successfully packaging a mod does not establish Playden runtime compatibility.
