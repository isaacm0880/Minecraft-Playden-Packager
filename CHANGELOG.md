# Changelog

## Unreleased

- The candidate workflow builds only a Windows x64 portable ZIP and Linux
  x86_64 AppImage from one approved source commit. macOS remains source-build
  only and physically unverified.
- Bundle the exact-source PS5 receiver ELF and integrity manifest. Direct loader
  deployment requires no host PS5 SDK, `socat` or `plink.exe`.
- Physical PS5 acceptance passed for automatic deployment, authentication,
  internal transfer/publication, temporary cleanup and authenticated shutdown
  with receiver source `b05c77d`. USB0 and cancellation have not been physically
  retested.
- Fabric preparation requires an installed host-native JDK 17+; standard
  PrismLauncher managed-Java locations are checked. Pillow is pinned in the
  desktop build environment.
- Vanilla 1.0 remains partially compatible because menu flashing and inaudible
  audio persist despite its scoped Java2D workaround.

- Omit ZGC from automatic JVM defaults while preserving explicit JVM additions.
  Keep supported metadata defaults and gate compact object headers by Java version.
- Verify the existing root `recipe.json` against Playden's new folder import
  implementation, expand package/transfer tests, and explain Use recipe / Skip
  with manual setup for older Playden builds. See [recipe details](docs/PLAYDEN-RECIPES.md).

## v0.4.0-beta.2 — 2026-11-17

Experimental source-only beta from `feat/ps5-network-transfer`. Python version:
`0.4.0b2`. No application builds or receiver ELF are included. v0.1.0 remains
stable. Changes since v0.2.0-beta.1:

- Metadata-driven Vanilla/Fabric packaging across supported Java/Minecraft
  generations, with exact Windows x64 runtime selection and inheritance handling.
  Verified local/cache runtimes are preferred; missing runtimes may download by
  default, with explicit offline/no-download controls.
- Native headless Fabric preparation, automatic when required in GUI builds,
  native JDK discovery and a separate-copy preparation action for existing packages.
  Vanilla 1.20.1 and natively prepared Fabric 1.20.1 / Loader 0.20.1 are confirmed
  reaching the PS5 main menu; this is not proof of world/mod compatibility.
- CLI `transfer-ps5 --start-receiver` reuses GUI SDK deployment and encrypted
  automatic pairing. Missing/stale source-tree receiver ELFs can be built locally
  with the installed SDK; no SDK or receiver binary is downloaded or bundled.
  Existing services are never silently restarted or replaced.
- Simpler GUI with collapsed advanced groups, current output-folder naming across
  LAN transfers, explicit CLI name overrides and clearer destination/conflict logs.
- Shared smoothed transfer speed/ETA, detailed transfer/verification statistics
  and completion confirmation returning to the main GUI while retaining packages.
- Correct first-show PS5 button sizing, divider-only hover tooltip and removal of
  duplicate Fabric preparation text. Compatibility labels are Works, Untested and
  Broken with retained evidence; mod tracking records known issues only.
- Authenticated internal/USB0 transfers retain private staging, inventory/hash
  validation, conflict refusal and explicit atomic publication receipts. Physical
  PS5 transfer success is owner-reported, not exhaustive firmware/loader acceptance.
- LAN performance remains under investigation. FTP feasibility is documented;
  FTP transfer is not enabled and no FTP speed improvement is claimed.

Receiver lifecycle, pairing and transfer foundations included in the previous beta:

## v0.2.0-beta.1 — 2026-11-17

Source-only milestone at `ca8539b`; its retained development snapshot reported
`0.4.0.dev0`. The following foundations were already available in that beta:

- PS5 Receiver Manager with explicit SDK deployment, authenticated session status,
  independent idle-only graceful shutdown and five-second liveness checks.
- Six-digit SRP-6a pairing with expiry, a five-attempt global budget and replay
  protection; RSA-OAEP encrypted automatic startup credentials. GUI session
  secrets stay in memory. Legacy CLI token transfers remain supported;
  `transfer-ps5 --pair` privately prompts for a new receiver's six-digit code.
- Lifecycle, PAKE reference-vector, encrypted bootstrap, busy-race, secret-handling
  and responsive GUI tests; loader stdout/trust/unload limitations documented.

- Paired PS5 LAN receiver payload and Python sender, with internal storage as
  the default destination and an explicit USB0 option without fallback.
- Authenticated messages, private staging, streamed hashes, receiver-side readback
  verification, constrained recipe/inventory checks and refusal of existing names.
- GUI send button, progress and cancellation; remembered IP/destination and
  memory-only session credentials. CLI `transfer-ps5` supports a fresh remote name.
- Host protocol/security integration tests and PS5 SDK build validation. The owner
  reports successful transfer and launch on a physical PS5; durability across
  unexpected power loss is not established.
- GUI build-completion LAN handoff, existing-package validation, editable recorded
  instance names (subsequently replaced by folder-name defaults), optional file pipelining, speed/ETA and explicit publication states.
- Fix #14: frozen Linux/AppImage folder opening uses the system desktop handler
  without bundled library paths, with asynchronous error reporting. Multi-build
  output selection opens the selected successful package and clears stale paths.
- Local USB transfer and local-output retention remain independent of LAN sends.

## v0.1.0 — 2026-11-16

First public release: Windows x64 portable ZIP and Linux x86_64 AppImage.
macOS is experimental and source-only; no prebuilt Mac application is provided.

- Desktop discovery for Minecraft Launcher, Prism Launcher and MultiMC.
- Local Vanilla/Fabric packaging with compatibility evidence, warnings and blocking.
- Sequential multi-instance builds, safe names, per-instance results and cancellation.
- Default-on privacy cleanup of staged copies; worlds and ordinary configs preserved.
- Queue-aware USB copying from both completion and main-window actions, verified by default.
- Distinct USB destinations, partial retries, cancellation and storage rechecking.
- Default-off, explicitly confirmed cleanup of eligible fully verified generated outputs.
- Idle startup progress and remembered offline GUI player name.
- Offline-only local-client CLI and package validation.
- Windows/Linux binary targets and optional experimental macOS builds with pinned tooling, provenance, content audits,
  frozen GUI smoke tests and SHA-256 checksums; native acceptance remains pending.
- Preparation-branch candidate builds use the exact public push commit without
  requiring a merge; combined checksum reports preserve existing evidence.
- Reviewed-source fingerprints use identical filename ordering across native hosts.
- Frozen startup smoke checks cover idle zero and the current GUI progress scale.
- Candidate archives include linked usage/compatibility guides and root licence/credits.
- Windows filesystem aliases retain verified USB results and safe cleanup eligibility.
- GUI regression fixtures use canonical transfer receipts and folder URLs; unexpected
  modal warnings fail tests promptly instead of stalling native CI.
- v0.1.0 requires Windows/Linux binaries only; macOS is experimental source-only
  and does not block binary verification. Retain the verified 70daa32 candidates.
