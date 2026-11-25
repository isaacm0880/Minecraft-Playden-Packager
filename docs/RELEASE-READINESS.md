# Next major release readiness audit

Audited 2026-11-25 against verified development baseline
`4584b3cd8622ebcc89e3e1b029e61ec8564a93b1`. This is a source/configuration audit,
not a new Windows/macOS build, PS5 run, security certification or redistribution
permission. It does not authorize a push, merge or release. The existing stable
artifacts and their historical approval remain unchanged.

The maintainer has confirmed Fabric 1.14.4, 1.16, 1.17.1 and 1.18.2, and Vanilla
1.16 working on PS5 in their reported configurations. Automatic recipes,
controller mappings, Fabric preparation and default/loader artwork work. The
Fabric icon mismatch was retained Playden game data, resolved by removing that
data. These working game configurations do not need another gameplay acceptance
cycle merely because a new desktop distribution is prepared.

## Release blockers, in priority order

### P0 — current release gate and clean build inputs

1. **The approved application snapshot is stale.** `release/provenance.py` refuses
   any application hash different from `release/source-policy.json`, including
   dirty-preview builds. The policy still names reviewed GUI snapshot `6fbc4d3`
   and hash `0b2f7500e9b1558b767ed89a22e8ee1619fd979053777d0514a723cb58acdc78`.
   Recomputing the hash from the committed `4584b3c` application files gives
   `998a18fdde5c415f5566cee2d84dc3f1d38bc00b4f381ecb2bfaa3ef9c43b8d5`.
   Consequently the candidate workflow stops in its source job before building
   any platform. After the final fixes and explicit source review, record the
   actual reviewed snapshot/hash through the existing approval procedure. Do not
   bypass this guard or relabel older binaries as the new source.
2. **The pinned desktop environment omits Pillow.** `pyproject.toml` requires
   `Pillow>=12,<13`, while `release/requirements.txt` has no Pillow entry. The
   workflow installs that file, then installs this project with `--no-deps`.
   Artwork tests and release licence/dependency collection require Pillow.
   Include a reviewed version in the release environment and verify a clean
   build; an existing developer environment is not evidence that the pinned
   installation is complete. The source package's normal dependency declaration
   is already present.

### P1 — self-contained feature delivery and platform acceptance

3. **The receiver is not part of desktop or wheel distributions.**
   `release/desktop.spec` contains no PS5 receiver ELF or receiver C/Makefile tree;
   setuptools packages only `minecraft_playden`. The default receiver path is
   checkout-relative `ps5/receiver/minecraft_playden_receiver.elf`. Thus a fresh
   frozen/wheel installation cannot build that default missing receiver from its
   own files. A source checkout can build a missing/stale receiver, but requires
   an installed PS5 Payload SDK, `make`, its deployer and `socat` on Unix or
   `plink.exe` on Windows. Select and audit a matching receiver binary plus
   supported deployment prerequisites for a self-contained LAN feature, or
   explicitly scope the release as requiring external tools/ELF configuration.
   The PS5 ELF loader remains an already-running external prerequisite either
   way; the packager does not install or manage the jailbreak/loader.
4. **Receiver identity needs an exact source/artifact binding.** The management
   string remains `v0.4-manager-2`, although recipe/controller/Java 8/artwork
   acceptance has evolved. `ensure_receiver_elf` uses source mtimes for local
   staleness and `prepare_elf` checks the ELF/bootstrap slot; an externally
   selected ELF can pass the version handshake without proving it contains the
   latest package checks. Produce an ELF from the final source, retain compiler,
   SDK and build-option provenance plus SHA-256, and verify its package-feature
   contract. Document compatibility and replacement of older receivers. Host
   `HOST_TEST`/sanitizer success is valuable but does not establish SDK/PS5 ABI,
   USB mount behavior or native durability. Target a short receiver-specific
   transfer/cleanup acceptance check, not replays of working Minecraft gameplay.
5. **Fabric preparation still needs host Java.** The helper source is bundled,
   but `host_java.py` requires a host-native full JDK 17+ with `jdk.compiler` and
   never acquires one. A Windows game runtime is a candidate only on Windows;
   Java 8 cannot supply the required source-launcher/compiler capability. Linux
   and macOS Windows game runtimes cannot substitute for a native build JDK.
   Either supply a reviewed/licensed host-JDK strategy or clearly declare this
   prerequisite. The current automatic worker fails closed rather than
   publishing an unprepared fallback; preserve that behavior.
6. **Fresh native desktop candidates and acceptance are outstanding.** Windows
   x64 ZIP and Linux x86_64 AppImage build/audit/smoke paths exist. They must run
   from the same final approved commit, with extracted-artifact re-audits,
   checksums, dependency notices and clean-machine visible GUI/USB tests. The
   Linux target is Debian 12/glibc 2.36+, x86_64; host graphics libraries are
   deliberately not bundled, so “self-contained” does not mean independent of a
   graphical host, glibc or GPU drivers. Windows path aliases, filesystem errors
   and security prompts require native acceptance beyond Linux Qt test styles.
7. **macOS cannot yet be advertised as a stable binary target.** The native
   ARM64/x86_64 jobs are optional and `continue-on-error`; source docs still say
   unverified. The spec sets bundle short version `0.4.0`, while the bundle audit
   requires the application version (`0.4.0b2` at the baseline): this is a
   concrete identity mismatch for the current beta configuration. Choose the
   next version once and keep Python, plist and artifact labels consistent.
   Run both native architectures, Cocoa/Finder launch, external-drive
   permissions, cancellation and USB tests before support claims. The current
   app is ad-hoc signed, not Developer-ID signed/notarized; decide and document
   the distribution policy and expected Gatekeeper behavior. If macOS remains
   experimental, state that as release scope rather than treating a green
   Windows/Linux pair as macOS acceptance.
8. **Redistribution evidence is incomplete for the supplied artwork.** Hashes
   prove fidelity to supplied images, not authorship or a redistribution grant.
   `THIRD_PARTY.md` calls the icons/background maintainer-supplied but does not
   retain per-image origins, licences/permissions or permitted branding uses.
   Record those for app branding, Minecraft art and each loader icon before
   distributing binaries containing them. Review naming/brand use against
   [Minecraft's current usage guidelines](https://www.minecraft.net/en-us/usage-guidelines);
   this audit does not decide legal entitlement. Keep playable clients,
   assets, worlds and prepared game packages out of source/application releases.

### P1 — release contract and instructions

9. **Update the release-facing documentation and scope together.** The package
   still reports `0.4.0b2`, installation starts with a source-only beta and the
   older v0.1.0 binaries, `RELEASE_NOTES.md` is v0.1.0, and `ACCEPTANCE.md` records
   historical tests. Preserve those historical results, but add a new exact
   source/artifact acceptance record and current platform/download matrix.
   Generic manual `@../../playden.args` instructions need an explicit Java 8
   exception pointing to generated `PLAYDEN.txt`/`playden.java8-command`.
   Describe `playden/recipe.json` consistently; installation still has a “root
   recipe” sentence. State the required Playden folder-recipe/controller/artwork
   capabilities and receiver source revision, rather than assuming every older
   installed build can use the new metadata.
10. **Define desktop GUI and CLI delivery separately.** Source GUI and CLI share
    the exporter/validator and have automatic preparation/custom artwork and
    controller opt-out behavior. The frozen entry point is GUI-only; it is not
    a frozen CLI executable. A “self-contained GUI and CLI” promise therefore
    needs either separate CLI distribution work or an explicit source-CLI
    installation requirement. CLI-only `pip install .` deliberately omits Qt and
    `cryptography`; encrypted automatic receiver startup reports an actionable
    GUI-extra dependency requirement. External receiver pairing and transfer are
    separate from that startup path. Keep those prerequisites clear instead of
    silently expanding dependencies or claiming full binary CLI parity.

## Findings that are already covered in the source

- The wheel resource declarations and PyInstaller data list explicitly include
  the owned, hash-pinned Java 8 bootstrap class, `FabricRemap.java`, default
  icon/background and all four loader icons. Forge/NeoForge/Quilt icon presence
  does not enable packaging those loaders. Final frozen candidates still need
  to demonstrate that importlib resources work without the source checkout.
- Source acquisition remains local-only for Minecraft clients. The downloaded
  Windows runtime is checked for exact metadata major, Windows x64 identity and
  executable format. Public dependencies, assets and runtime downloads require
  network access unless their verified cache/local inputs exist; “self-contained
  application” must not imply a bundled game or an always-offline first build.
- GUI artwork/player/controller preferences are explicit, queued snapshots;
  CLI artwork/name defaults do not inherit GUI preferences. Receiver
  configuration is intentionally shared. Advanced diagnostic launch profiles
  and file-only imports remain CLI features, rather than hidden GUI features.
- Release tooling gates the full suite, Linux/Windows/macOS type targets,
  known-pattern source/content audits, provenance and frozen smoke tests. It
  preserves internal symlinks and re-audits extracted ZIP/AppImage contents.
  These are useful gates, not evidence of a native artifact already built for
  this next release. Main test CI is not a push gate on this feature branch;
  the receiver workflow has its own feature-branch trigger.
- Owned Java 8 bytecode and the synthetic client fixture are exact-hash audit
  exceptions; unknown game/private binaries remain rejected. Python/Qt/Pillow,
  crypto/native dependency licences are collected by the builder, but the final
  per-platform inventory must be checked. The Qt one-folder layout retains
  replaceable shared libraries. Review the applicable
  [Qt/PySide component notices](https://doc.qt.io/qtforpython-6/licenses.html)
  against the actual frozen contents; source MIT licensing does not replace
  dependency obligations. Receiver SHA-256/BearSSL source notices are retained
  under `ps5/receiver/vendor`; include the relevant notices when distributing an
  ELF, not just the desktop Python notices.

## Security and transfer limits to retain in the release notes

These are declared boundaries, not requests to implement unrelated features:

- PAKE/strong session keys, HMAC-authenticated ordered frames and replay
  protection do not encrypt package/world/mod bytes. Use a trusted private LAN;
  do not expose receiver services to the internet. SDK loader upload is raw TCP
  and its process exit is not remote receiver success.
- The receiver is an explicit, short-lived service with fixed import roots and
  no-overwrite publication. It validates paths, inventories and constrained
  recipes, but does not run Java or fully validate Minecraft dependency linkage.
  Normal package validation and console game compatibility remain distinct.
- A missing COMMIT receipt can mean publication completed. The sender must not
  automatically delete or blindly retry that package. Crash recovery must not
  recursively purge shared transfer roots, saved worlds or unknown directories.
- Published import permissions follow Playden's shared writable-file policy;
  local processes with write access to the import root must be trusted. The
  fallback publication reservation and storage-specific durability need to be
  described accurately. Privacy cleanup covers known sensitive files, not every
  possible secret in user configs/mods/logs; inspect packages before sharing.
- No authenticated Minecraft multiplayer/Realms/account acquisition is offered.
  Unsupported loaders remain blocked. Preserve those limits instead of
  interpreting the confirmed configurations as support for all mods/versions.

## #20 and #22 sign-off for this release

- **#20 source work:** `0de6e96` validates the official LaunchWrapper dependency
  separately from the legacy client target, while retaining integrity checks,
  the Java 8 adapter and modern launch paths. Its 24 focused tests pass. The
  real Vanilla 1.0 package at `dist/physical-vanilla-1.0-20261125/Vanilla-1.0/`
  passes current package validation. Startup, resources, controller behavior and
  saving still require a physical PS5 test; see `docs/LEGACY-LAUNCH.md`.
- **#22 source work:** the mounted USB sender removes completed owned sessions
  and empty temporary parents, retaining interrupted checkpoints for validated
  retry. The LAN receiver removes its stage on handled outcomes and an empty
  temporary parent on internal storage or USB0. Concurrent sessions and unknown
  files remain protected; cleanup failure after publication is reported without
  revoking success. All 159 transfer/receiver tests pass, including nine
  subtests. Physical receiver and mounted USB filesystem acceptance remain.
  Record the final #22 commit and rerun its focused tests after any subsequent
  changes.

## Optional improvements after blockers are resolved

- Developer-ID/code signing and published checksum signatures where appropriate;
  hashes alone provide integrity rather than independent publisher identity.
- A reviewed dependency lock/hash strategy and narrowly scoped reproducible
  receiver build manifest, beyond environment package/version listings.
- Promote macOS to a required CI gate only after choosing stable macOS support;
  retain the explicit experimental label otherwise.
- A separate receiver capability revision/manifest can improve stale-ELF
  diagnostics; do not weaken handshake or package validation to accept it.
- Modernize historical guide links and automate more final bundle resource/CLI
  smoke checks once the intended release feature/platform matrix is fixed.

No unrelated features, dependency changes, permission changes or release-gate
updates were implemented by this audit. No PS5, Windows or macOS application was
started, and no new physical result is claimed.
