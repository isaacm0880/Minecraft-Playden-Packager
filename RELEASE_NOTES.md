# Minecraft Playden Packager v0.1.0

Package locally installed Minecraft Java Edition instances for Playden on PS5.
This release includes GUI instance discovery, Vanilla/Fabric packaging, sequential
multi-instance builds, compatibility warnings/blocking, default privacy cleanup,
queue-aware USB copying with optional checksum verification, progress/cancellation,
retry/storage rechecking, confirmed optional local cleanup, persistent offline
player names and an offline-only CLI.

## Install

- **Windows x64:** download the portable ZIP, extract the complete folder and open
  `MinecraftPlaydenPackager.exe`. Keep its dependencies alongside the EXE.
- **Linux x86_64:** download the AppImage, run `chmod +x Minecraft-Playden-Packager-v0.1.0-Linux-x86_64.AppImage`, then launch it.
  A graphical desktop with glibc 2.36+ is required. If FUSE is unavailable, use
  `APPIMAGE_EXTRACT_AND_RUN=1 ./Minecraft-Playden-Packager-v0.1.0-Linux-x86_64.AppImage`.
- Compare each download with `SHA256SUMS`: Windows `Get-FileHash FILE -Algorithm SHA256`,
  Linux `sha256sum FILE`. Matching hashes verify integrity, not publisher identity.

**Build provenance:** Windows and Linux binaries were built and automatically validated from commit `70daa32`. Subsequent commits updated documentation and release infrastructure without changing application functionality.

**macOS:** Experimental, source-only. No prebuilt macOS application is available. Users can run from source or build their own application using the installation guide.

See the [installation guide](https://github.com/isaacm0880/Minecraft-Playden-Packager/blob/v0.1.0/docs/INSTALLATION.md),
including Mac source and standalone application instructions, and the
[usage guide](https://github.com/isaacm0880/Minecraft-Playden-Packager/blob/v0.1.0/docs/USAGE.md).

Select installed instances, choose an output location, build, then use **Copy to USB**.
Both USB actions copy every available successful output from the latest queue.
Privacy cleanup and USB verification are enabled by default. Cleanup of locally
generated packages is off by default and requires explicit confirmation and
successful full USB verification. Original Minecraft instances remain untouched.

Configure the copied game manually in Playden:

```text
Playden configuration:
  Program: java/bin/java.exe
  Launch arguments: @../../playden.args

Note: Online play limited to offline mode servers, authentication is not yet implemented
```

## Compatibility and limitations

Vanilla and installed Fabric packaging are supported where technically possible.
Vanilla 26.3 and 26.3 with Fabric Loader 0.20.1 without Ixeris have specific PS5
success reports; this does not certify every modpack. Untested versions use
best-effort packaging with a warning. Forge is Broken and blocked; NeoForge/Quilt
launch packaging is unsupported. There is no Microsoft authentication, online
client acquisition, direct PS5 transfer, helper/payload integration or log collection.
Internal recipe files remain validation artifacts; Playden configuration is manual.

Automated Windows/Linux build, test, content-audit and frozen smoke checks passed
for the retained binaries. **Manual Windows/Linux desktop, physical USB and PS5
acceptance tests have not been verified.** These limitations remain documented in
[acceptance](https://github.com/isaacm0880/Minecraft-Playden-Packager/blob/v0.1.0/docs/ACCEPTANCE.md).
macOS remains unverified, and its experimental self-built applications are not notarized.

The original archives were not modified or repackaged. Their embedded guide
contains earlier Mac download planning text; the current installation guide and
these notes supersede it. Published metadata/audit records preserve the original
build and pre-publication verification snapshots, including their historical
publication flags. The version tag identifies the newer approved documentation
and release-infrastructure commit on `main`; binary metadata identifies `70daa32`.

Source code is MIT licensed. Bundled components retain their own licences and
notices. Report bugs through the [issue tracker](https://github.com/isaacm0880/Minecraft-Playden-Packager/issues/new/choose);
review logs and do not upload credentials, game files, worlds or populated packages.
