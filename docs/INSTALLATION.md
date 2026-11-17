# Installation — v0.1.0

Download the Windows/Linux binaries and `SHA256SUMS` from the
[v0.1.0 release](https://github.com/isaacm0880/Minecraft-Playden-Packager/releases/tag/v0.1.0).
They retain their original build source, `70daa32`, and are portable downloads.
Manual desktop, physical USB and PS5 acceptance testing has not been verified.

## Release downloads

| Platform | Filename |
| --- | --- |
| Windows x64 | `Minecraft-Playden-Packager-v0.1.0-Windows-x64.zip` |
| Linux x86_64 | `Minecraft-Playden-Packager-v0.1.0-Linux-x86_64.AppImage` |
| macOS | Experimental source-only support; not yet verified; no application ZIP |

Only Windows x64 and Linux x86_64 are required v0.1.0 binary artifacts.
Both still require manual desktop and USB acceptance. The Linux toolchain uses
Debian 12/glibc 2.36; older glibc and musl/Alpine are not supported build targets.

After download, compare its SHA-256 with the published `SHA256SUMS`. On Windows use
`Get-FileHash <file> -Algorithm SHA256`; on Linux use `sha256sum <file>`; on macOS use
`shasum -a 256 <file>`. A matching checksum checks integrity, not publisher identity.

- **Windows:** extract the complete ZIP into a writable directory and run the EXE.
  Retain its dependencies and licence files; the EXE is not a standalone file.
- **Linux:** mark the AppImage executable (`chmod +x <file>.AppImage`) and run it.
  A graphical desktop and compatible host graphics/platform libraries are required.
  If FUSE is unavailable, AppImage extraction can be used on compatible hosts.

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

In the development source, Windows Java downloads require `--allow-runtime-download`
or the GUI's explicit download checkbox. Prefer `--java-runtime /path/to/windows-jdk`
or `CACHE/runtimes/java-MAJOR-windows-x64`. `build --offline` permits no HTTP and
requires installed/cached metadata and dependencies. Java is selected by metadata
major and verified as Windows x64; host Java is never used for the package.
See [runtime limitations](COMPATIBILITY.md). The retained v0.1.0 binaries use their
original behavior.

Put `--cache PATH` before the command to choose a download cache. `--username NAME`
sets the CLI's offline display name independently of GUI preferences. `versions`
queries the public version catalog; listing is not a compatibility guarantee.
Advanced diagnostic launch profiles and unverified file-only imports remain CLI
features; run `--help` for their scope. They do not make unsupported loaders launchable.

## macOS: experimental source-only

**Not yet verified on Apple Silicon or Intel Macs.** v0.1.0 has no supported macOS
application ZIP. Source execution or a self-built app may fail; report the exact
macOS, CPU, Python and Qt versions with any bug. This does not change the package's
Windows Java target or guarantee that a game/modpack works on PS5.

Install Python 3.11+ from [python.org](https://www.python.org/downloads/macos/).
Open Terminal in a checkout of this repository, then run:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[gui]"
.venv/bin/python -m minecraft_playden gui
```

For a self-built `.app`, use a **clean committed checkout**, native Python **3.11.9**
matching your CPU, and a new output directory. The pinned release builder runs its
full tests, all type targets, privacy/content audits and frozen smoke tests:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r release/requirements.txt
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/python -m release.build_desktop --source-sha "$(git rev-parse HEAD)" --output ../my-macos-candidate
```

Run this on the actual Mac architecture; cross-compilation is not a substitute.
The output is an experimental local ZIP containing an ad-hoc signed app, not a
notarized or supported release. Gatekeeper, Finder, Cocoa startup and external-drive
permissions remain unverified. Do not disable system security globally. A failed
check is a blocker for that self-build, not a reason to bypass the builder's gates.

## Playden configuration

Follow [usage](https://github.com/isaacm0880/Minecraft-Playden-Packager/blob/v0.1.0/docs/USAGE.md), then add the copied package through your installed
Playden's supported local-game flow and configure manually:

```text
Playden configuration:
  Program: java/bin/java.exe
  Launch arguments: @../../playden.args

Note: Online play limited to offline mode servers, authentication is not yet implemented
```

The generated internal recipe artifact remains validated but requires no upload
workflow. Installing the packager does not install Playden, a PS5 payload or Minecraft.
