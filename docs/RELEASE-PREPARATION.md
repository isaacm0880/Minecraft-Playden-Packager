# Next desktop candidate: decisions and physical checks

This is preparation for a new candidate from `feat/ps5-network-transfer`, not
approval to publish. The historical v0.1.0 artifacts are unchanged. No working
Minecraft version needs another gameplay test.

## Source approval gate

`release/provenance.py` compares every application file against the hash in
`release/source-policy.json` before allowing even a dirty preview. The maintainer
authorised approval of the reviewed source. The application changes from verified
baseline `4584b3c` through `b8b85cb` were reviewed with targeted tests and the
snapshot `44b4c9d5ffde54a8a90fbc1c726f8a9fa2cd4a75a1ffa4b3f3a31e72f99759f6`
was recorded in the existing policy. Dirty-source, exact-commit, content-audit,
test and frozen-smoke gates remain active. Any later application edit requires a
new review and policy hash.

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
the same artifact to Windows, Linux and both Mac jobs. Each desktop
builder verifies the manifest against its exact checkout before embedding the
ELF and manifest. The content audit checks their hashes, and the frozen smoke
checks the default ELF path and runtime checksum guard. A configured external
ELF remains an explicit override. The desktop package includes the receiver
ELF and deploys it over the loader's raw TCP protocol without a PS5 Payload SDK,
`socat`, or `plink.exe` on the host. The SDK-script fallback remains for a
manually selected unmanifested ELF. The already-running PS5 ELF loader is
always an external prerequisite. The GUI can export the unpatched ELF from
its Advanced section for manual deployment.

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

**Decision for this candidate:** an installed host-native JDK 17+ is required.
Standard PrismLauncher managed JDK locations are also searched. Users can
select a portable launcher JDK manually. No JDK is bundled or downloaded.

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
  run frozen smoke and native GUI/USB/path-alias acceptance. Test direct loader
  deployment from the bundled receiver on Windows.
- **macOS ARM64 and x86_64:** Jobs are required in the candidate workflow and
  use numeric Apple bundle version fields. Run Cocoa/Finder, external-volume,
  cancellation and Gatekeeper acceptance on both architectures. Decide whether
  ad-hoc signing without Developer ID notarization is an acceptable experimental
  distribution policy; do not advertise stable Mac binaries yet.
- **All platforms:** Approve the source snapshot, review supplied-artwork rights
  and dependency/SDK notices, keep receiver source/ELF evidence, run the existing
  full release gates only when the pipeline is ready, then record exact artifact
  digests and clean-machine acceptance. The frozen program is GUI-only; a
  binary CLI promise needs a separate executable or an explicit source-CLI
  requirement.

## Recorded Vanilla 1.0 physical check and optional follow-up

The maintainer has completed the first physical test: package generation and
LAN transfer passed; gameplay/controller input work after two Java2D properties,
but menu flashing and inaudible audio make compatibility Partial. The steps
below are a reference for any focused receiver/audio follow-up, not a release
gate or a request to replay working Minecraft versions. The original candidate
requires the two manual properties; fresh 1.0 builds add them automatically.

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
4. Import the internal Vanilla 1.0 folder through Playden's folder flow.
   Program should be `java/bin/java.exe`. For a fresh package built with this
   update, choose **Use recipe** for `playden/recipe.json`. For the original
   candidate, use manual arguments from `PLAYDEN.txt` or
   `playden.java8-command` and add `-Dsun.java2d.d3d=false` and
   `-Dsun.java2d.opengl=false` before the main class. Do not use
   `@../../playden.args` for Java 8.
5. For an optional focused audio check, confirm the imported game folder retains
   `instances/default/minecraft/resources/sound3/` and the other indexed sound
   directories. Capture post-S3-fallback sound registration or Wine audio errors
   and the game's sound settings. If convenient, reload the saved disposable
   world; the earlier report established only that saving appeared to work.
   The menu flashing is a Playden/Wine developer follow-up, not a packager
   release gate. Previously working versions need no retest.
