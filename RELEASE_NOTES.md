# v0.1.0 release notes — draft

Not published. Do not attach unbuilt or unaccepted artifacts to this draft.

Minecraft Playden Packager packages existing local Minecraft Java Edition instances
for use with Playden on PS5. This first release includes a desktop GUI, local
Vanilla/Fabric packaging, sequential builds, compatibility warnings, privacy cleanup,
queue-aware USB copying and an offline-only CLI.

Planned downloads: Windows x64 portable ZIP, Linux x86_64 AppImage, macOS Apple Silicon
ARM64 application ZIP and macOS Intel x86_64 application ZIP. All four must come from
one approved public source commit and pass provenance, content audits, checksums,
frozen smoke tests and native acceptance before publication. No binaries are attached.

Known limitations: no authentication or online client acquisition; no direct PS5
transfer/helpers/log collection. Forge is Broken and blocked; NeoForge/Quilt launch
packaging is not implemented. Untested versions may package with a warning. Confirmed
PS5 configurations do not guarantee every mod works. Internal recipes remain validated
artifacts; configure Playden manually as described in the [usage guide](docs/USAGE.md).

Before publishing, record the exact source SHA, build run, four verified artifact
hashes and signed-off acceptance results. See [release procedure](docs/RELEASE.md).
