# Minecraft Playden Packager

[![Beta status](https://img.shields.io/badge/beta-v0.4.0--beta.2-orange)](https://github.com/isaacm0880/Minecraft-Playden-Packager/releases/tag/v0.4.0-beta.2)
![Stable binary targets](https://img.shields.io/badge/stable%20binaries-Windows%20%7C%20Linux-blue)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-green)](LICENSE)

Package your locally installed **Minecraft Java Edition** instances for use with
**Playden on PS5**, using a desktop GUI or CLI.

**v0.1.0 is released for Windows x64 and Linux x86_64.** macOS is experimental
and source-only. Automated build checks passed; manual desktop, physical USB and
PS5 acceptance testing has not been verified.

**v0.4.0-beta.2 is an experimental, source-only beta** from
`feat/ps5-network-transfer` (Python application/package version `0.4.0b2`).
**No downloadable application builds or receiver ELF are included.** GitHub
provides only its automatic source ZIP and tar.gz archives. Existing v0.1.0
application downloads remain the stable release and lack these beta features.

This beta adds metadata-driven Minecraft packaging, automatic native Fabric
preparation, simpler build/transfer controls and automatic CLI receiver startup
with `transfer-ps5 --start-receiver`. LAN transfers target internal import storage
or USB0 with authenticated verification and safe publication. The owner reports
successful transfers and launches on a physical PS5; this does not certify every
Minecraft/loader/mod combination or every firmware and ELF loader.
See the [beta release notes](https://github.com/isaacm0880/Minecraft-Playden-Packager/blob/v0.4.0-beta.2/release/notes/v0.4.0-beta.2.md),
[Receiver Manager and loader limitations](docs/PS5_RECEIVER_MANAGER.md) and
[PS5 LAN setup](docs/PS5_LAN_TRANSFER.md).

## What it does

- Finds installations from Minecraft Launcher, Prism Launcher and MultiMC.
- Packages Vanilla and installed Fabric instances, preserving worlds, mods and ordinary settings.
- Builds multiple selected instances sequentially, with individual results and safe output names.
- Shows compatibility warnings and blocks known-broken or technically unsupported builds.
- Cleans known private data from package copies by default in the GUI; original instances stay untouched.
- Copies every successful queue output to USB, with progress, cancellation and checksum verification enabled by default.
- Sends newly built or existing validated packages over paired LAN, using the current output folder name, progress, smoothed speed/ETA and cancellation; CLI `--name` is an explicit override.
- Manages the receiver through explicit Start/Stop controls, encrypted automatic startup pairing or a six-digit PAKE code. CLI `--start-receiver` uses the same secure startup path. The PS5 ELF loader must already be running.

It does **not** sign in, download a replacement Minecraft client, collect console
logs or deploy a payload without an explicit GUI Start Receiver action or CLI `--start-receiver`. Public metadata, dependencies,
assets may still need downloads. Windows Java downloads are enabled when needed; matching verified local/cached runtimes are preferred. Advanced offline/no-download controls remain available. You need your own Minecraft files
and a working Playden installation.

## Compatibility

| Installation | Packaging |
| --- | --- |
| Vanilla | Metadata-driven Windows x64 packaging; Java 17/21/25 generations covered by source tests |
| Fabric | Supported with installed Fabric metadata and required local dependencies |
| Other untested Minecraft versions | Best-effort packaging where the engine supports the format; warning shown |
| Forge | **Broken** — known unresolved Playden launch issues; building blocked |
| NeoForge / Quilt | **Not supported** for launch packaging |

Confirmed PS5 configurations include **Vanilla 1.20.1**, **Vanilla 26.3** and **26.3 with Fabric
Loader 0.20.1**. This does not certify every mod or modpack. A
successful package validation is not proof that the game will launch on PS5.

**1.20.1 + Fabric Loader 0.20.1** is confirmed reaching the PS5 main menu with native pre-remapping.
The GUI automatically [prepares Fabric when required](docs/FABRIC_REMAP.md), without
Wine or a Minecraft session. This confirms startup, not world loading or every mod;
the original unprepared package failed during first-launch remapping.

Untested combinations show:

> Unsupported version — not tested on PS5. Packaging will be attempted, but the game or some mods may not work.

[Compatibility and limitations](docs/COMPATIBILITY.md) - version and mod compatability

## Download and install

Download **v0.1.0** from the [GitHub Release](https://github.com/isaacm0880/Minecraft-Playden-Packager/releases/tag/v0.1.0).
Verify the download using its `SHA256SUMS`. No installer or Python setup is needed
for the Windows/Linux binaries.

| Host platform | Download |
| --- | --- |
| Windows x64 | [Portable ZIP](https://github.com/isaacm0880/Minecraft-Playden-Packager/releases/download/v0.1.0/Minecraft-Playden-Packager-v0.1.0-Windows-x64.zip) — extract the whole folder, then open `MinecraftPlaydenPackager.exe` |
| Linux x86_64 | [AppImage](https://github.com/isaacm0880/Minecraft-Playden-Packager/releases/download/v0.1.0/Minecraft-Playden-Packager-v0.1.0-Linux-x86_64.AppImage) — make it executable, then run it |
| macOS | **Experimental, source-only, not yet verified** — no v0.1.0 application download |

Windows/Linux candidates have passed automated build checks; manual acceptance is
still required. macOS is experimental and not yet verified; see
[macOS source instructions](docs/INSTALLATION.md#macos-experimental-source-only). The Linux build targets
glibc 2.36+; musl/Alpine and Windows/Linux ARM builds are not targets. See the
[installation guide](docs/INSTALLATION.md) for source setup, checksums and platform notes.

## Build and copy

1. Finish installing your instance in its normal launcher, then open the packager.
2. Select an instance, or use **Add source folder**. Ctrl/Cmd-click or Shift-click selects several.
3. Check the version, loader and compatibility status. Any blocked selected instance disables the whole Build action.
4. Choose an output folder, then click **Build Playden package**. Its folder name becomes the default PS5 destination name. Required Fabric preparation runs automatically.
   Choose **Send to PS5 (LAN)** for a receiver transfer, or use USB copying below.
5. Use **Copy to USB** in the completion popup or main window. Both copy all successful, available outputs from the latest queue. Check the destination preview and keep the drive connected through verification.
6. Review individual USB results. Retry copies only failed/cancelled/pending items; a new Copy action uses fresh destination names. Safely eject before unplugging.

USB checksum verification can be skipped for speed, with reduced integrity checking;
package validation still runs. Optional local-output cleanup is **off by default**
and requires a fully verified copy plus confirmation of the exact directory. Skipped
verification, failures and cancellation cannot authorise cleanup of that package.

[Detailed usage, privacy and USB behaviour](docs/USAGE.md)

## Configure the game in Playden

Packages are copied under `playden/import/<package-name>/`. Use the supported local
game/folder import in Playden. On builds with game-folder recipe detection, accept
the offered `playden/recipe.json` to set the launch configuration. Accepting replaces the
game's current settings; choose **Skip** to retain your custom settings.
If no recipe is offered, configure the executable and arguments below manually.

Vanilla and Fabric recipes include a Minecraft keyboard/mouse controller layout
by default. Turn off **Use Minecraft keyboard/mouse controller layout** in the
GUI (remembered), or pass `--no-controller-layout` when building from the CLI,
for Controlify or other controller mods. This assumes default Minecraft keybinds
and needs Playden's `controller_layout` setting and a runtime kit with `input-map`.
[Bindings and runtime requirements](docs/PLAYDEN-RECIPES.md#optional-minecraft-controller-layout).

```text
Playden configuration:
  Program: java/bin/java.exe
  Launch arguments: @../../playden.args

Note: Online play limited to offline mode servers, authentication is not yet implemented
```

Packages also include `playden/icon.png` and `playden/background.png`, using the
bundled Minecraft artwork for Vanilla and the supplied Fabric icon for Fabric.
Choose custom PNGs in **Build advanced options** (remembered), or use CLI
`--icon PATH --background PATH`. **Use default** restores automatic loader artwork.
[Artwork requirements and legacy packages](docs/ARTWORK-METADATA.md).

Java 8 packages use a dedicated adapter instead of the argument-file launch
shown above. Use the generated recipe or copy its exact command from `PLAYDEN.txt`.
[Java 8 support and physical testing](docs/JAVA8-LAUNCH.md).

Recipe discovery is verified against Playden's `develop` source, not its current
`main` release line. See [the recipe contract and manual checks](docs/PLAYDEN-RECIPES.md).

## Troubleshooting and bug reports

Missing client or loader files? Finish installation in the original launcher and
rescan. Low USB space? Free space and use **Check again**. A failed copy retains
safe staging for retry and does not overwrite existing USB packages.

See [troubleshooting](docs/TROUBLESHOOTING.md). Report bugs through the
[public issue tracker](https://github.com/isaacm0880/Minecraft-Playden-Packager/issues/new/choose)
with the application version, OS, launcher, Minecraft/loader versions and error.
Review logs for personal paths. **Do not upload credentials, account databases,
Minecraft client files, worlds or populated packages.**

## CLI, credits and licence

The CLI uses locally installed clients only:

```sh
python -m minecraft_playden build --source local --instance /path/to/instance --output ./My-Package
python -m minecraft_playden validate ./My-Package
```

Fabric preparation runs automatically when its mappings require it, using a
separate native JDK 17+. Select that tool with `--remap-java PATH` if discovery
cannot find it. The packaged game runtime keeps its required Java major.
`--no-prepare-fabric` is an explicit troubleshooting opt-out with a warning.

[Source installation and CLI](docs/INSTALLATION.md#source-installation) ·
[Changelog](CHANGELOG.md) · [Release provenance](docs/RELEASE.md)

Thanks to the Playden, Python, Qt for Python, Fabric and Prism/MultiMC projects.
Project code is [MIT licensed](LICENSE); see [third-party notices](THIRD_PARTY.md).
Minecraft, Java and other downloaded components keep their own licences. Do not
redistribute populated game packages without permission. This independent project
is not affiliated with Mojang, Microsoft or Sony.

> **AI disclosure:** built with the assistance of AI.

LAN development: [staging, pairing and manual PS5 testing](docs/PS5_LAN_TRANSFER.md),
[performance diagnostics and benchmarks](docs/PS5_LAN_PERFORMANCE.md), and
[authenticated receiver shutdown](docs/PS5_RECEIVER_SHUTDOWN.md).
