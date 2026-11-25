# Next desktop candidate: decisions and physical checks

This is preparation for a new candidate from `feat/ps5-network-transfer`, not
approval to publish. The historical v0.1.0 source policy and artifacts are
unchanged. No working Minecraft version needs another gameplay test.

## Source approval gate

`release/provenance.py` compares every application file against the hash in
`release/source-policy.json` before allowing even a dirty preview. That policy
names the old `6fbc4d3` review. The intended process is to review the complete
new application diff, tests, resource/license inventory and exact application
hash; record the maintainer's approval; then update the policy to the approved
hash and reviewed commit. Keep the dirty-source, exact-commit, content-audit,
test and frozen-smoke gates active. The policy must not be changed just to make
a build pass. A release candidate must use a clean final commit after that
approval; a later application edit requires another review/hash update.

## Receiver build and bundling

`python -m release.receiver --output NEW_DIRECTORY` downloads the fixed SDK
v0.45 archive, verifies its SHA-256, builds the receiver twice with fixed
options and refuses differing bytes. `--sdk-archive PATH` uses a previously
downloaded copy with the same required checksum. The receiver Makefile disables
the SDK linker's random build ID. The output includes an ELF and a manifest
with exact source commit, per-file receiver source digests, source-tree digest,
SDK archive digest, compiler identification, options, ELF size and ELF digest.
The build command refuses dirty receiver source unless explicitly making a
local `--allow-dirty-preview` artifact.

The desktop workflow builds this receiver once after the source gate and passes
the same artifact to Windows, Linux and both optional Mac jobs. Each desktop
builder verifies the manifest against its exact checkout before embedding the
ELF and manifest. The content audit checks their hashes, and the frozen smoke
checks the default ELF path and runtime checksum guard. A configured external
ELF remains an explicit override. The desktop package includes the receiver
ELF, but automatic deployment still needs a PS5 Payload SDK deployer plus
`socat` on Unix or `plink.exe` on Windows. External receiver pairing can use
the bundled ELF through a separately operated deployer. The already-running
PS5 ELF loader is always an external prerequisite.

The ELF is source and toolchain bound, not yet console accepted. The pinned SDK
archive and payload licensing/notice obligations need final distribution review.
Do not substitute a same-version but different ELF; the management handshake
alone does not establish exact receiver source identity.

## Host JDK for automatic Fabric preparation

The existing preparation worker needs a host-native full JDK 17+ with
`jdk.compiler`; the Windows runtime packaged for PS5 is separate. It already
searches `JAVA_HOME`, `PATH` and normal platform install locations, probes the
candidate and gives a specific selection error. It does not download Java.

| Strategy | Portability and user experience | Size, updates and licensing |
| --- | --- | --- |
| Bundle a host JDK in every desktop artifact | Works offline immediately, but requires separate Windows x64, Linux x64, macOS ARM64 and Intel payloads and native acceptance. | Adds a large runtime to every download, including users who never prepare Fabric. Requires JDK update cadence and GPLv2 with Classpath Exception notices/source obligations. |
| Automatically provision a pinned JDK on first Fabric preparation | Small initial app and guided setup; needs network, cache, architecture selection, signed/hash-verified downloads, cancellation and offline error handling. | Java download shifts to first use; maintain per-platform pins, licences and updates. |
| Require an installed JDK, discover and probe it | Current implemented path; smallest app and no extra network or bundled runtime. Users may need one manual installation. | User/OS manages updates and JDK licensing; clear installation guidance is necessary. |

**Recommendation for this candidate:** keep the installed-JDK requirement and
make it explicit in downloads and GUI/CLI instructions. This preserves the
verified fail-closed worker and avoids adding four unaccepted JDK payloads to
the first binary release. Consider opt-in automatic provisioning in a later
release after pinning native archives and an update/security policy. Do not
assume the game's Windows Java 8 runtime can compile Fabric mappings.

Temurin's [licensing FAQ](https://adoptium.net/docs/faq/?variant=openjdk16),
[download/API guidance](https://adoptium.net/installation/ci-scripts), and
[platform downloads](https://adoptium.net/temurin/releases/) are primary
references for the later provisioning decision.

## Native build requirements still open

- **Linux x86_64:** Use the pinned Debian 12/Python 3.11.14 image, build the
  AppImage, re-audit its extracted tree, run frozen artwork/receiver smoke and
  packaged X11 startup. Then test visible GUI and removable-drive access on a
  clean compatible host. Host glibc, graphics and FUSE/extraction behavior remain
  external.
- **Windows x64:** Use pinned Python 3.11.9, build/re-audit the portable ZIP,
  run frozen smoke and native GUI/USB/path-alias acceptance. Decide whether
  automatic receiver deployment is supported with an installed SDK/`plink.exe`
  or documented as external receiver pairing only.
- **macOS ARM64 and x86_64:** Jobs are opt-in and non-blocking. Choose the next
  version and align the Python version with valid `CFBundleShortVersionString`
  and `CFBundleVersion` before native builds. Run Cocoa/Finder, external-volume,
  cancellation and Gatekeeper acceptance on both architectures. Decide whether
  ad-hoc signing without Developer ID notarization is an acceptable experimental
  distribution policy; do not advertise stable Mac binaries yet.
- **All platforms:** Approve the source snapshot, review supplied-artwork rights
  and dependency/SDK notices, keep receiver source/ELF evidence, run the existing
  full release gates only when the pipeline is ready, then record exact artifact
  digests and clean-machine acceptance. The frozen program is GUI-only; a
  binary CLI promise needs a separate executable or an explicit source-CLI
  requirement.

## Physical PS5 check for the new Vanilla 1.0 candidate

Use the existing validated local folder
`dist/physical-vanilla-1.0-20261125/Vanilla-1.0/`. Keep a copy and choose a
fresh remote import name. The current receiver candidate is in
`dist/receiver-physical/` with its source-bound `receiver-manifest.json`. If the
receiver source changes, rebuild from the new clean commit with
`python -m release.receiver --output NEW_DIRECTORY` and use that new ELF and
manifest. Do not use an older installed receiver with the same handshake.

1. Record PS5 firmware, loader, Playden revision, receiver manifest/ELF SHA-256,
   storage destination, and the desktop source commit. Confirm the PS5 ELF
   loader is already running and USB0 is mounted if testing USB0.
2. From the project checkout, run the commands below with a fresh import name.
   The SDK deployer may keep its terminal open to forward receiver output; use
   another terminal for the transfer. Enter the six-digit code from the PS5
   notification at the hidden CLI prompt. Replace `PS5_IP` with its LAN IPv4:

   ```sh
   .venv/bin/python -m minecraft_playden validate dist/physical-vanilla-1.0-20261125/Vanilla-1.0
   PS5_IP=192.168.1.50
   PS5_PAYLOAD_SDK=/path/to/ps5-payload-sdk
   "$PS5_PAYLOAD_SDK/bin/prospero-deploy" -h "$PS5_IP" -p 9021 dist/receiver-physical/minecraft_playden_receiver.elf
   # In a second terminal, while the receiver is running:
   PS5_IP=192.168.1.50
   .venv/bin/python -m minecraft_playden transfer-ps5 dist/physical-vanilla-1.0-20261125/Vanilla-1.0 --host "$PS5_IP" --pair --destination internal --name Vanilla-1.0-release-check
   ```

   On Windows use the same GUI pairing/transfer controls; the shell commands
   above assume a POSIX host. If using the GUI to start the receiver, select
   `dist/receiver-physical/minecraft_playden_receiver.elf` in advanced options
   and use its paired session to send the existing package.
3. Send Vanilla 1.0 to a fresh internal import name. Confirm the success receipt,
   final folder, SHA-256 verification and absence of the owned stage and empty
   `/data/playden-packager-temp/` parent. Repeat one fresh-name transfer to
   `usb0`; verify publication under `/mnt/usb0/playden/import/` and absence of
   its owned stage/empty temporary parent. Leave other packages and files alone.
   Cancel one disposable transfer before COMMIT and confirm its private stage is
   removed and no final import appears. A lost COMMIT receipt requires inspecting
   the final folder before any retry.

   ```sh
   .venv/bin/python -m minecraft_playden transfer-ps5 dist/physical-vanilla-1.0-20261125/Vanilla-1.0 --host "$PS5_IP" --pair --destination usb0 --name Vanilla-1.0-usb0-release-check
   ```

   Restart the receiver for a new six-digit code before a second one-shot CLI
   pairing. For both destinations in one receiver session, use the GUI's
   authenticated connection instead.
4. Import the internal Vanilla 1.0 folder through Playden's folder flow and
   choose **Use recipe** for `playden/recipe.json`. Program should be
   `java/bin/java.exe`. If manual arguments are needed, copy the generated
   Java 8 command from `PLAYDEN.txt` or `playden.java8-command`; do not use
   `@../../playden.args` for this package.
5. Check startup/menu, applicable controller actions, sound and mapped resources.
   Create a disposable world, save, quit and reload it. Confirm its files are
   under this package's `instances/default/minecraft/saves`. Retain complete
   Playden/JVM logs on failure and identify whether it is adapter, wrapper/ASM,
   legacy natives/AWT, game startup or saving. Mark Vanilla 1.0 compatible only
   after this physical result; no previously working version needs retesting.
