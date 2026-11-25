# Older native classifier metadata

Captured 2026-11-24 from the public Mojang, Fabric and Prism endpoints below.
These files contain metadata only. Tests use synthetic JARs and runtime files;
package validation is not evidence of physical PS5 compatibility.

| File | Source | SHA-256 |
| --- | --- | --- |
| fabric-1.14.4.json | [metadata](https://meta.fabricmc.net/v2/versions/loader/1.14.4/0.20.1/profile/json) | `b8fe8625189eaea99c3ed6028ea661dff95f598f8256ca572e91156e4b5e3d2c` |
| fabric-1.16.json | [metadata](https://meta.fabricmc.net/v2/versions/loader/1.16/0.20.1/profile/json) | `fdad9ad5a58f25a846d34dd6708543546384ccfc5ee8b6b5b8684088ca809acc` |
| fabric-1.18.2.json | [metadata](https://meta.fabricmc.net/v2/versions/loader/1.18.2/0.20.1/profile/json) | `40d052bf1328931cbaa5fd5a0998bd2ebda3739bb4f759abe90a664fc3ad767a` |
| mojang-1.14.4.json | [metadata](https://piston-meta.mojang.com/mc/game/version_manifest_v2.json) | `cc747066ac1329bf7d9639dc01049ae8148222d2b8527b5f112cdeda43969ae7` |
| mojang-1.16.json | [metadata](https://piston-meta.mojang.com/mc/game/version_manifest_v2.json) | `d9bf75e60744bc8758df529499f59e01ac763ecd512c026e1d314e527b0fcf8c` |
| mojang-1.18.2.json | [metadata](https://piston-meta.mojang.com/mc/game/version_manifest_v2.json) | `23aec01c7a56ac6a42f9bcc7d8a6a94275859215b398c0cef95c2aea19125455` |
| prism-1.14.4.json | [metadata](https://meta.prismlauncher.org/v1/net.minecraft/1.14.4.json) | `37469f3722f63c86f5b4087ef0d4b8ae2285853a8cbffd3fc46d794db5f61446` |
| prism-1.16.json | [metadata](https://meta.prismlauncher.org/v1/net.minecraft/1.16.json) | `579ca94e8f5defc701f75cd5f33e93103adc409891849e782a8b1c46c2627f74` |
| prism-1.18.2.json | [metadata](https://meta.prismlauncher.org/v1/net.minecraft/1.18.2.json) | `ba2be71f444bff290c8e6138a29a432d7b4e3c716ea9eb28b317930649aaa2e8` |

Mojang documents were selected by exact version ID from the linked manifest.
Prism removes Maven paths from classifier downloads and repeats the Java artifact
beside its native map. Fabric Meta profiles inherit the corresponding Mojang base.

Live inspection also confirmed the Prism map format in every stable release from
1.14 through 1.18.2 (including intermediate patch releases). 1.19, 1.19.1 and
1.19.2 use standalone native artifacts instead. This is a metadata-format boundary,
not a hardcoded supported-version boundary. 1.14.4 and 1.16 now use the dedicated Java 8 adapter. No runtime substitution
is made; package success is not physical compatibility evidence.
