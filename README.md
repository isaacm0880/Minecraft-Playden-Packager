# Minecraft Playden Packager

[![Release status](https://img.shields.io/badge/release-v0.1.0-blue)](https://github.com/isaacm0880/Minecraft-Playden-Packager/releases)
![Platform targets](https://img.shields.io/badge/binaries-Windows%20%7C%20Linux-blue)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-green)](LICENSE)

Package your locally installed **Minecraft Java Edition** instances for use with
**Playden on PS5**, using a desktop GUI or an offline-only CLI.

**v0.1.0 is released for Windows x64 and Linux x86_64.** macOS is experimental
and source-only. Automated build checks passed; manual desktop, physical USB and
PS5 acceptance testing has not been verified.

## What it does

- Finds installations from Minecraft Launcher, Prism Launcher and MultiMC.
- Packages Vanilla and installed Fabric instances, preserving worlds, mods and ordinary settings.
- Builds multiple selected instances sequentially, with individual results and safe output names.
- Shows compatibility warnings and blocks known-broken or technically unsupported builds.
- Cleans known private data from package copies by default in the GUI; original instances stay untouched.
- Copies every successful queue output to USB, with progress, cancellation and checksum verification enabled by default.

It does **not** sign in, download a replacement Minecraft client, send files directly
to a PS5, collect console logs or install a payload. Public metadata, dependencies,
assets and Windows Java may still need downloads. You need your own Minecraft files
and a working Playden installation.

## Compatibility

| Installation | Packaging |
| --- | --- |
| Vanilla | Supported for modern versions requiring Java 17+ |
| Fabric | Supported with installed Fabric metadata and required local dependencies |
| Other untested Minecraft versions | Best-effort packaging where the engine supports the format; warning shown |
| Forge | **Broken** — known unresolved Playden launch issues; building blocked |
| NeoForge / Quilt | **Not supported** for launch packaging |

Confirmed PS5 configurations are **Vanilla 26.3** and, separately, **26.3 with Fabric
Loader 0.20.1 without Ixeris**. This does not certify every mod or modpack. A
successful package validation is not proof that the game will launch on PS5.

Untested combinations show:

> Unsupported version — not tested on PS5. Packaging will be attempted, but the game or some mods may not work.

[Compatibility and limitations](docs/COMPATIBILITY.md)

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

1. Finish installing your instance in its normal launcher, close Minecraft, then open the packager.
2. Select an instance, or use **Add source folder**. Ctrl/Cmd-click or Shift-click selects several.
3. Check the version, loader and compatibility status. Any blocked selected instance disables the whole Build action; deselect it or fix the reported problem.
4. Choose an output location and a separate download cache. Leave **Privacy cleanup** enabled. Build and review every result.
5. Use **Copy to USB** in the completion popup or main window. Both copy all successful, available outputs from the latest queue. Check the destination preview and keep the drive connected through verification.
6. Review individual USB results. Retry copies only failed/cancelled/pending items; a new Copy action uses fresh destination names. Safely eject before unplugging.

USB checksum verification can be skipped for speed, with reduced integrity checking;
package validation still runs. Optional local-output cleanup is **off by default**
and requires a fully verified copy plus confirmation of the exact directory. Skipped
verification, failures and cancellation cannot authorise cleanup of that package.

[Detailed usage, privacy and USB behaviour](docs/USAGE.md)

## Configure the game in Playden

Packages are copied under `playden/import/<package-name>/`. Use the supported local
game/folder import or add-game flow in your installed Playden version, then select
the executable and arguments below. Check Playden's own instructions for its UI.

```text
Playden configuration:
  Program: java/bin/java.exe
  Launch arguments: @../../playden.args

Note: Online play limited to offline mode servers, authentication is not yet implemented
```

Configure these settings manually. Internal `recipe.json` files remain part of
package validation; no recipe-upload feature is required or advertised.

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

[Source installation and CLI](docs/INSTALLATION.md#source-installation) ·
[Changelog](CHANGELOG.md) · [Release provenance](docs/RELEASE.md)

Thanks to the Playden, Python, Qt for Python, Fabric and Prism/MultiMC projects.
Project code is [MIT licensed](LICENSE); see [third-party notices](THIRD_PARTY.md).
Minecraft, Java and other downloaded components keep their own licences. Do not
redistribute populated game packages without permission. This independent project
is not affiliated with Mojang, Microsoft or Sony.
