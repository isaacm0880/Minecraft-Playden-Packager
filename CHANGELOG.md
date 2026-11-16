# Changelog

## v0.4.0.dev0 — unreleased

- Paired PS5 LAN receiver payload and Python sender, with internal storage as
  the default destination and an explicit USB0 option without fallback.
- Authenticated messages, private staging, streamed hashes, receiver-side readback
  verification, constrained recipe/inventory checks and refusal of existing names.
- GUI send button, progress and cancellation; remembered IP/destination and
  memory-only pairing token. CLI `transfer-ps5` supports a fresh remote name.
- Host protocol/security integration tests and PS5 SDK build validation. Physical
  PS5 transfer, filesystem durability and Playden launch testing remain pending.
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
