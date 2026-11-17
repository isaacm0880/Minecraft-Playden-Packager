# Multi-generation metadata fixtures

Captured 2026-11-17. `provenance.json` records the exact HTTPS endpoint and SHA-256
of every unmodified metadata snapshot. Mojang snapshots also record their official
manifest SHA-1. The manifest source is
https://piston-meta.mojang.com/mc/game/version_manifest_v2.json ; per-version JSON
URLs identify its immutable metadata packages. Fabric snapshots come from exact
Minecraft/loader profile endpoints at https://meta.fabricmc.net/ .

These are metadata documents, not game, library, native or Java binaries. The
packaging matrix reads actual Java requirements, rules, libraries, asset-index
identities, logging, JVM/game arguments and Fabric inheritance. In-memory copies
replace file hashes/sizes with synthetic payloads. Network calls are mocked; a
second export runs with networking disabled. No physical PS5 or Java game execution
is involved, and no PS5 compatibility is inferred.

Coverage includes Vanilla/Fabric 1.18.2, 1.19.2, 1.20.1, 1.21.1, 1.21.11, 26.1.2,
26.2 and 26.3 across Minecraft Launcher, Prism and MultiMC layouts. The Prism/
MultiMC matrix converts the captured Fabric libraries/arguments into representative
component documents; the existing separate `fabric/` fixtures cover actual cached
Prism component formats. 1.16.5 metadata is investigated but blocked because its
Java 8 launcher cannot use the existing Playden argument-file configuration.

Synthetic inheritance, snapshot identities and legacy asset maps are identified
as such in the tests. The older `fabric/` 26.2 generalisation test remains synthetic;
this directory adds captured 26.2 metadata rather than replacing its provenance.
`metadata-26.3-playden.args` in the parent directory is the updated golden launch
plan; the older console-reference argument fixtures remain unchanged.
