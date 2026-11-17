# Compatibility

Packaging targets Windows x64 Java for Playden regardless of the host platform.
v0.1.0 binary targets are Windows x64 and Linux x86_64; manual acceptance is
pending. macOS Apple Silicon/Intel support is experimental, source-only and not
yet verified; there is no required macOS binary. See [installation](INSTALLATION.md).

| Status | Meaning |
| --- | --- |
| Tested | A specific configuration has confirmed PS5 success |
| Unsupported / Untested | No PS5 confirmation; best-effort packaging allowed if technically possible |
| Failed on PS5 / Retest required | A physical failure is recorded; candidate packages may build, but need a new physical test |
| Broken | Known unresolved compatibility problems; building blocked |
| Cannot package | Missing required files/dependencies or unsupported packaging format |

Explicit Tested entries are Vanilla Minecraft 1.20.1, Vanilla Minecraft 26.3 and Minecraft 26.3 with Fabric
Loader 0.20.1 in a configuration without Ixeris. Other loader versions and arbitrary
modpacks are not certified. Successful packaging or checksums never add Tested status.

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
| 1.20.1 | 17 | Automated structural pass; confirmed PS5 working | 0.16.10: automated structural pass; 0.20.1: physical remapping failure | Fabric headless preparation requires PS5 retesting |
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

## Runtime selection and remaining blockers

Selection requires the metadata's exact Java major, a Windows x64 release identity
and AMD64 PE `java.exe`/`server/jvm.dll`, without executing Java on the host.
It prefers explicit and known launcher-local runtimes, then a cached Java home or
previously verified runtime archive. A Linux, macOS, ARM64 or different-major Java
installation is not a substitute. These structural checks do not establish that
Temurin or its graphics/native dependencies run through Playden.

Runtime downloads are off by default. Use CLI `--java-runtime PATH`, a Java home
under `CACHE/runtimes/java-MAJOR-windows-x64`, or explicitly enable
`--allow-runtime-download` / the GUI Windows Java download checkbox. The download
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
| 1.20.1 | Vanilla | N/A | ✅ Working | User-confirmed on Playden 0.12.0-nightly.20261116.1 |
| 1.20.1 | Fabric | 0.20.1 | ❌ Launch failure; retest required | Fails during client JAR remapping; headless-prepared candidate is not PS5-verified |
| 26.3 | Vanilla | N/A | ✅ Working | Confirmed working |
| 26.3 | Fabric | 0.20.1 | ✅ Working | Confirmed working |

For the 1.20.1 Fabric failure, see [headless preparation, investigation and PS5 retest instructions](https://github.com/isaacm0880/Minecraft-Playden-Packager/blob/feat/ps5-network-transfer/docs/FABRIC_REMAP.md). The failure reaches a Java ZIP filesystem reporting read-only; USB location alone does not establish its cause.

**Status key:**

- ✅ Working — Tested and confirmed working on PS5.
- ⚠️ Partially working — Runs with known limitations.
- ❌ Not working — Tested and confirmed broken.

**Note:** Compatibility is specific to the tested configuration. Other versions, Fabric Loader versions and mod combinations are not guaranteed to work.

## Known Mod Compatibility

This table tracks mods that cause issues or break the package on playden. Compatibility may vary depending on the Minecraft version, Fabric Loader version and mod version.

| Mod                                       | Status        | Notes                                                          |
| ----------------------------------------- | ------------- | -------------------------------------------------------------- |
| [Ixeris](https://modrinth.com/mod/ixeris) | ❌ Not working | Known compatibility issues with Playden. Avoid using this mod. |

**Status key:**

- ✅ Working — Tested and confirmed working on PS5.
- ⚠️ Partially working — Works with limitations or known issues.
- ❌ Not working — Known to cause issues or prevent the game from running.

### Report Mod Compatibility

Tested a mod that isn't listed? [Open an issue](https://github.com/isaacm0880/Minecraft-Playden-Packager/issues/new) with \:borked-mod

- Mod name and version
- Minecraft and Fabric Loader versions
- Whether it works, partially works or fails
- Any errors or limitations encountered

**Note:** Successfully packaging a mod does not guarantee it will work when launched through Playden.


