# Compatibility

Packaging targets Windows x64 Java for Playden regardless of the host platform.
v0.1.0 binary targets are Windows x64 and Linux x86_64; manual acceptance is
pending. macOS Apple Silicon/Intel support is experimental, source-only and not
yet verified; there is no required macOS binary. See [installation](INSTALLATION.md).

| Status | Meaning |
| --- | --- |
| Tested | A specific configuration has confirmed PS5 success |
| Unsupported / Untested | No PS5 confirmation; best-effort packaging allowed if technically possible |
| Broken | Known unresolved compatibility problems; building blocked |
| Cannot package | Missing required files/dependencies or unsupported packaging format |

Explicit Tested entries are Vanilla Minecraft 26.3 and Minecraft 26.3 with Fabric
Loader 0.20.1 in a configuration without Ixeris. Other loader versions and arbitrary
modpacks are not certified. Successful packaging or checksums never add Tested status.

Vanilla packaging supports modern structured metadata requiring Java 17+. Fabric
supports installed modern profiles from Prism/MultiMC and Minecraft Launcher,
including exact selected loader versions and required local artifacts. Public
checksum-pinned dependencies may still be acquired by the existing resolver.

Untested combinations, including best-effort Fabric 1.20.1 and 26.2, display:

> Unsupported version — not tested on PS5. Packaging will be attempted, but the game or some mods may not work.

Forge is Broken due to known unresolved Playden launch issues and cannot build.
NeoForge/Quilt launch packaging is not implemented; they are Cannot package, not
inferred Broken from Forge. Unverified CLI file-only imports do not provide launch
support. Missing clients/artifacts must be installed through the original launcher.

Legacy Java 8/virtual-asset layouts, arbitrary patched clients and unsupported
custom metadata are technical blockers. There is no authenticated multiplayer,
Realms, online client acquisition, PS5 helper, direct console transfer or console
log collection. Console game/mod/runtime acceptance remains configuration-specific.
