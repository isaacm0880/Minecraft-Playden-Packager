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
the same artifact to Windows and Linux. Each desktop
builder verifies the manifest against its exact checkout before embedding the
ELF and manifest. The content audit checks their hashes, and the frozen smoke
checks the default ELF path and runtime checksum guard. A configured external
ELF remains an explicit override. The desktop package includes the receiver
ELF and deploys it over the loader's raw TCP protocol without a PS5 Payload SDK,
`socat`, or `plink.exe` on the host. The SDK-script fallback remains for a
manually selected unmanifested ELF. The already-running PS5 ELF loader is
always an external prerequisite. The GUI can export the unpatched ELF from
its Advanced section for manual deployment.

The ELF is source and toolchain bound. Automatic deployment, authentication,
internal transfer/publication, temporary cleanup and authenticated shutdown were
physically confirmed on PS5 with the receiver from `b05c77d`. USB0 and cancellation
have not been physically retested; neither blocks candidate preparation. The pinned
SDK archive and payload licensing/notice obligations need final distribution review.
Do not substitute a same-version but different ELF; the management handshake
alone does not establish exact receiver source identity.

Physical acceptance on `101742b` reached receiver VERIFY with the generated
Vanilla 1.0 package but was rejected. The C recipe and required structure passed;
the receiver's immutable-inventory top-level key allowlist lacked the new
`legacy_java2d` marker. The receiver now accepts that marker only when it is
bound to Vanilla 1.0 LaunchWrapper metadata and both Java2D argument files.
The exact failed package passed the corrected host receiver's transfer, VERIFY,
COMMIT and stage cleanup. The rebuilt ELF subsequently passed those focused PS5 checks. No Minecraft
gameplay replay is required for the distribution candidate.

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
- **macOS:** Source-build instructions and existing build tooling remain available.
  No macOS binary is produced by the official candidate workflow, and physical
  Mac compatibility is unverified. No Apple signing or notarization work is planned.
- **All platforms:** Approve the source snapshot, review supplied-artwork rights
  and dependency/SDK notices, keep receiver source/ELF evidence, run the existing
  full release gates, then record exact artifact
  digests and clean-machine acceptance. The frozen program is GUI-only; a
  binary CLI promise needs a separate executable or an explicit source-CLI
  requirement.

## Recorded physical compatibility limits

Vanilla 1.0 package generation and LAN publication passed. Gameplay and controller
input work after the scoped Java2D workaround, but the main menu flashes and audio
is inaudible; status remains Partial. World saving appeared to work, with reload
unconfirmed. Menu rendering is a potential upstream Playden/Wine issue. Neither
perfect Vanilla 1.0 compatibility nor another gameplay cycle is a condition for
this distribution candidate. See [legacy launch evidence](LEGACY-LAUNCH.md).

The receiver built from `b05c77d` passed automatic loader deployment, encrypted
startup authentication, internal-storage transfer and publication, temporary
folder cleanup, and authenticated shutdown on a physical PS5. USB0 and
cancellation have not been physically retested. Their host regressions remain
part of the full release suite; the physical gaps are recorded without marking
them passed and do not block candidate preparation.
