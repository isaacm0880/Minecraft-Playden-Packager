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

## Candidate preparation checkpoint (2026-11-26)

The branch includes the Windows x64 portable ZIP and Linux x86_64 AppImage
candidate workflow. The mandatory pair check requires those two jobs and the
source gate; macOS remains a source-build option with retained tooling and no
official binary job. `release/requirements.txt` pins Pillow, and the frozen
artwork smoke checks the bundled decoder and resources. The desktop bundle
contains the verified receiver ELF, its exact-source integrity manifest,
third-party notices and the SDK GPL-3 text. Automatic loader deployment uses
raw TCP and needs no host PS5 SDK, `socat` or `plink.exe`.

On clean commit `3d24353cb948700563770cf0c89888b09282d8b4`, the source
approval gate accepted reviewed application snapshot
`44b4c9d5ffde54a8a90fbc1c726f8a9fa2cd4a75a1ffa4b3f3a31e72f99759f6`.
The receiver was rebuilt twice from pinned SDK v0.45 with reproducible ELF hash
`bfbc62bdb04ed338ca20a19a0317317f29e1fc88f07d22478479d83a253d6b13`;
its manifest names that exact commit and passed provenance verification.

The pinned Debian 12/Python 3.11.14 Linux candidate build passed its full test
gate with a C compiler present: **1,069 passed, 19 skipped, 396 subtests passed**.
The skipped tests were fixture/platform cases, not missing C receiver coverage.
The alternate Qt style gate passed **67 tests and 16 subtests**; mypy passed
for Linux, Windows and macOS targets. The source/content audits, frozen smoke,
extracted AppImage audit and clean-container X11 smoke passed. The local
AppImage is `dist/linux-candidate-3d24353/Minecraft-Playden-Packager-v0.4.0b2-Linux-x86_64.AppImage`,
SHA-256 `ebe267e6793d9c5a2db23f9897b8ea70270fae486f3d4766e26f8256054b3203`.
It is a local validation artifact, not an official or published download.
The three receiver tests that initially raced the receiver's post-transfer
cleanup now wait for authenticated idle status before Stop; no receiver or
application validation was relaxed.

**Remaining before a public binary release:** the GitHub Actions Windows x64
native ZIP build and Windows GUI/USB/path acceptance have not run in this
session. The GitHub Actions Linux build and final pair report also remain to
run against the same pushed commit. Inspect their checksums, bundled receiver,
artwork and notices before publication. A clean Linux desktop host check is
still needed for visible GUI/removable-drive behavior beyond container smoke.
Physical USB0 and cancellation remain unverified; they are recorded gaps, not
candidate-build blockers. Vanilla 1.0 remains Partial.

The receiver links PS5 Payload SDK v0.45 startup code licensed GPL-3.0-or-later.
The bundle now includes that license and a receiver notice. Before distributing
the ELF publicly, the maintainer must confirm the corresponding-source offer
and full compliance for the SDK, vendored receiver components, Qt/PySide and
the AppImage runtime, plus rights to supplied artwork. This is a publication
gate, not a reason to disable the provenance or integrity checks. The public
version is still `0.4.0b2`; the maintainer must decide whether to retain it for
this candidate or choose a new release version before publication.

**To trigger candidate builds after this branch is pushed by the maintainer:**
in GitHub Actions choose **Desktop release candidate (no publishing)**, select
`feat/ps5-network-transfer` as the workflow branch, click **Run workflow**, and
enter the exact 40-character SHA of that pushed branch tip as `source_sha`.
The source gate requires the input SHA to match the workflow ref and approved
source; it will not build an unreviewed or dirty source. Download the Receiver,
Windows-x64, Linux-x86_64 and Verified-source-pair artifacts from that run and
check `SHA256SUMS`, `build-metadata.json`, `content-audit.json`, `artifact.json`
and `release-pair.json`. Do not publish until the remaining gates above pass.

## GitHub Actions CI repair checkpoint (2026-11-26)

Run `45742364325` used `b3fa06324e4519c6e4a36adbeff929b311f24a2d`.
Its source and receiver jobs passed; Windows and Linux failed during the
desktop builder's test gate, so no distribution artifacts were produced. The
failures were source-mode smoke expecting a source-tree ELF despite the verified
receiver artifact being downloaded separately, a stale GUI expectation for
SDK fallback deployment, Windows short/long temporary-path spellings, a test
attempt to read another process's locked Windows session file, and a fixture
hash calculated before Windows text newline conversion. The production fixes
limit the receiver check to actual frozen smoke and compare transfer-directory
ancestors by filesystem identity while still rejecting traversal and reparse
points. The receiver ELF and C source are unchanged. The GUI and inventory
changes are test corrections; immutable package verification remains active.

The application edits were reviewed at `697bd437fb48f496733ab49cde9cbb0f9e8fe74e`
and approved through `release/source-policy.json` with application SHA-256
`0355a8d0b0463ead0e075d2cd08643d8635dfc0d12f66d61c49d2e48458c37ca`.
The focused local regression set passed **37 tests, 1 Windows-only skip, 11
subtests**. The pinned Debian 12/Python 3.11.14 build at clean commit
`74645e42efe5f3ecb3a69a215aeabbe6743bfa31` then passed the full gate:
**1,069 passed, 20 skipped, 396 subtests passed**; alternate Qt style
**67 passed, 16 subtests passed**; mypy passed on Linux, Windows and macOS
targets. Content audit, frozen receiver/artwork smoke, extracted AppImage
audit and clean-container X11 startup passed. The local AppImage is
`dist/linux-candidate-74645e4/Minecraft-Playden-Packager-v0.4.0b2-Linux-x86_64.AppImage`,
SHA-256 `6dfb7a006dfe06200da39b00c47724000552b7aa20ecb1628d6c9a0dc44ab6b9`.
It is a local test artifact. The receiver manifest for this commit was
verified and the ELF hash remained
`bfbc62bdb04ed338ca20a19a0317317f29e1fc88f07d22478479d83a253d6b13`.

Native Windows/Actions builds and the pair report still require a new workflow
run on the final branch tip. The workflow input must be that new commit SHA,
never the failed run's SHA. No candidate has been pushed or published from
this repair session.

## Windows receiver candidate checkpoint (2026-11-26)

The physical Windows preview confirmed raw ELF upload, encrypted startup,
receiver authentication, authenticated Stop and refusal on both receiver ports
after shutdown. Application commit `1f67dea1781e61331553669a6f6ee94d780f45d9`
contains those fixes. Commit `184cee3355fcb2333c4c4ad1611df9df130f935e`
closes frozen-bundle verification on a missing or invalid manifest, ELF or
source provenance. The reviewed application snapshot is approved by
`8c2663f8c6a3b55a763af139a256eb328124ce6e`; `check_source` and the
public source audit passed at that commit.

Desktop candidate run `45877548925` passed source and receiver jobs. Linux's
full test gate failed four assertions that still expected the prior receiver
message or Stop-button behavior (1,086 passed, 20 skipped, 396 subtests passed).
Windows and pair jobs were cancelled after the failure. No desktop candidate
was accepted from that run. The follow-up edits update only those assertions:
occupied ports still block deployment, an unpaired Stop click sends no shutdown
request, and connection-refused errors retain the endpoint and cause. The
native Windows targeted set passed 29 tests, with five C receiver cases skipped
because no native C compiler is installed. The next exact-commit Actions run
must exercise those cases and complete both desktop artifacts and pair checks.
