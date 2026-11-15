# Installation — v0.1.0

No binary release has been published yet. Planned downloads will appear on the
[public Releases page](https://github.com/isaacm0880/Minecraft-Playden-Packager/releases).
Do not treat workflow artifacts or source previews as an accepted public release.

## Planned downloads

| Platform | Filename |
| --- | --- |
| Windows x64 | `Minecraft-Playden-Packager-v0.1.0-Windows-x64.zip` |
| Linux x86_64 | `Minecraft-Playden-Packager-v0.1.0-Linux-x86_64.AppImage` |
| macOS Apple Silicon | `Minecraft-Playden-Packager-v0.1.0-macOS-ARM64.zip` |
| macOS Intel | `Minecraft-Playden-Packager-v0.1.0-macOS-x86_64.zip` |

All four native artifacts still require acceptance. The Linux toolchain uses
Debian 12/glibc 2.36; older glibc and musl/Alpine are not supported build targets.

After download, compare its SHA-256 with the published `SHA256SUMS`. On Windows use
`Get-FileHash <file> -Algorithm SHA256`; on Linux use `sha256sum <file>`; on macOS use
`shasum -a 256 <file>`. A matching checksum checks integrity, not publisher identity.

- **Windows:** extract the complete ZIP into a writable directory and run the EXE.
  Retain its dependencies and licence files; the EXE is not a standalone file.
- **Linux:** mark the AppImage executable (`chmod +x <file>.AppImage`) and run it.
  A graphical desktop and compatible host graphics/platform libraries are required.
  If FUSE is unavailable, AppImage extraction can be used on compatible hosts.
- **macOS:** select your native CPU architecture, extract the ZIP, then open the app.
  The current build recipe uses ad-hoc signing, not Developer ID signing or
  notarisation. Gatekeeper/Finder acceptance must be checked before publication;
  do not disable system security globally to run an unverified build.

## Source installation

Use Python 3.11+ and a graphical desktop. From a checkout of this public repository:

```sh
python -m venv .venv
.venv/bin/python -m pip install -e ".[gui]"
.venv/bin/python -m minecraft_playden gui
```

On Windows, use `py -3 -m venv .venv`, then `.venv\Scripts\python.exe` instead of
`.venv/bin/python`. Qt remains optional for CLI-only use: install with `pip install .`.
The GUI dependency excludes PySide6 6.12.0 due to a known Python 3.11 signal crash;
release builds pin their toolchain separately in `release/requirements.txt`.

The CLI never signs in or downloads missing Minecraft clients:

```sh
python -m minecraft_playden build --source local --instance /path/to/instance --output ./My-Package
python -m minecraft_playden build --source local --client-jar /path/to/client.jar --version 26.3 --output ./My-Package
python -m minecraft_playden validate ./My-Package
```

Put `--cache PATH` before the command to choose a download cache. `--username NAME`
sets the CLI's offline display name independently of GUI preferences. `versions`
queries the public version catalog; listing is not a compatibility guarantee.
Advanced diagnostic launch profiles and unverified file-only imports remain CLI
features; run `--help` for their scope. They do not make unsupported loaders launchable.

## Playden configuration

Follow [usage](USAGE.md), then add the copied package through your installed
Playden's supported local-game flow and configure manually:

```text
Playden configuration:
  Program: java/bin/java.exe
  Launch arguments: @../../playden.args

Note: Online play limited to offline mode servers, authentication is not yet implemented
```

The generated internal recipe artifact remains validated but requires no upload
workflow. Installing the packager does not install Playden, a PS5 payload or Minecraft.
