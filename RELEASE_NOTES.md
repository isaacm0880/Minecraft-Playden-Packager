# v0.1.0 release notes — draft

Not published. Windows/Linux candidates are prepared for review, not yet approved
for public release.

Minecraft Playden Packager packages locally installed Minecraft Java Edition
instances for Playden on PS5. It includes the GUI, Vanilla/Fabric packaging,
sequential builds, compatibility warnings/blocking, default privacy cleanup,
queue-aware USB copying, verification/retries/cancellation, confirmed optional
local cleanup, persistent offline names and an offline-only CLI.

Binary downloads: **Windows x64 portable ZIP** and **Linux x86_64 AppImage** only.
Both retained candidates were built from
`70daa32e92c2fe7cac47737c640ca19c2838bbd4` in
[workflow run 43760353213](https://github.com/isaacm0880/Minecraft-Playden-Packager/actions/runs/43760353213).
The preparation branch contains newer documentation and verification tooling;
it does not relabel those binaries as a newer build. See
`release/v0.1.0-candidate.json` for exact file hashes, sizes and workflow provenance.

**macOS is experimental, source-only and not yet verified.** No supported macOS
application ZIP is included in v0.1.0. Apple Silicon/Intel users can
[run from source or build locally](docs/INSTALLATION.md#macos-experimental-source-only).
Optional Mac jobs remain available for future validation and are not binary-release
gates. A self-build is not a verified or notarized release.

Retained archives contain an earlier guide listing planned Mac downloads. Ship the
current installation guide with these release notes; they supersede that plan.
The original archives and SHA-256 values remain unchanged. Actual package layout
and Playden launch configuration are unchanged.

Known limits: no authentication/online client acquisition or direct PS5
transfer/helpers/log collection. Forge is Broken and blocked; NeoForge/Quilt launch
packaging is unsupported. Untested versions may package with a warning. Confirmed
PS5 configurations do not guarantee every mod works. Internal recipes remain
validation artifacts; configure Playden manually using the [usage guide](docs/USAGE.md).

Before publication, complete Windows/Linux desktop and physical USB acceptance,
inspect licences/privacy, and confirm real game/PS5 behaviour as recorded in
[acceptance](docs/ACCEPTANCE.md). Successful automated verification is not manual
acceptance. Do not merge, tag, publish or change repository visibility without approval.
