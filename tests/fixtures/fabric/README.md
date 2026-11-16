# Fabric profile fixture provenance

Captured on 2026-11-15 from public Fabric and Prism metadata endpoints.
These JSON documents are metadata snapshots, not Minecraft or Fabric binaries.
Tests build synthetic JAR/class and Java files and adjust checksums in in-memory
copies to those payloads. They never execute a loader, a mod or Java.

| Fixture | Source | SHA-256 |
| --- | --- | --- |
| official-1.20.1-0.16.10.json | [metadata](https://meta.fabricmc.net/v2/versions/loader/1.20.1/0.16.10/profile/json) | `b64cd1b6ded68178698f64b96d4fb9de0b012185e9905d54e24de7213790a748` |
| official-26.3-0.20.1.json | [metadata](https://meta.fabricmc.net/v2/versions/loader/26.3/0.20.1/profile/json) | `9eaf9ed53536e07b0392ac3cc1df714bfdac06da17ac89ddd9967c85ca985756` |
| prism-1.20.1-0.16.10.json | [metadata](https://meta.prismlauncher.org/v1/net.fabricmc.fabric-loader/0.16.10.json) | `4676c66ef3dacfc0e5c2278aaabb102aba7164116f0cd154d6ce7052cc6f6ee6` |
| prism-26.3-0.20.1.json | [metadata](https://meta.prismlauncher.org/v1/net.fabricmc.fabric-loader/0.20.1.json) | `23958675c39a14a63aba9c2ee4dea5cefb81d08ef6e2dee1efff6afb106f9456` |

The 26.2 regression derives a synthetic 26.2 identity in memory from the 26.3
metadata fixtures. It tests version-independent packaging and warning behavior;
it is not a captured 26.2 profile or evidence of PS5 compatibility.
