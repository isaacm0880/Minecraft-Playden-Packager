# Release acceptance

The preparation branch preserves the reviewed GUI features, with a correction for
Windows filesystem aliases in USB result/cleanup bookkeeping and updated frozen
smoke assertions for the current progress scale. The source policy records these
reviewed release fixes. Cleanup still rejects traversal, symlinks and junctions
before normalising a source path; confirmation and checksum gates remain mandatory.
Automated validation during preparation (Linux host, Python 3.14.8 / PySide6 6.11.3):

- Complete pytest suite: 434 passed, 1 optional upstream integration skipped,
  325 subtests passed.
- Additional GUI regression run using Qt minimal with Windows widget style:
  111 passed, 41 subtests passed. This does not simulate a native Windows system.
- mypy Linux, Windows and macOS targets: all passed for the application and
  release scripts (49 source files per target).
- GitHub Actions configuration: actionlint 1.7.7 passed.
- Public source content/privacy audit passed; reviewed application hash matched.
  CLI help exposes offline commands and no experimental authentication options.

No official release has been published. Check the candidate workflow run and
draft PR for the current artifact availability and exact public source commit.
Workflow builds and automated smoke tests do not replace platform acceptance.

Before public release, record results against the same candidate source commit:

- Verify the Windows x64 ZIP and Linux x86_64 AppImage from one exact source
  commit. Inspect provenance, bundled licences, content/privacy audits and actual
  SHA-256 checksums. These are the only required v0.1.0 binaries. Run each on a
  clean matching system (Linux glibc 2.36+). Check visible startup, icons, extraction,
  GUI scaling, file permissions and operating-system security prompts.
- Discover actual supported launcher installations and package Vanilla and
  Fabric, including installed dependencies, untested versions, Forge blocking,
  and NeoForge/Quilt blocking. Test mixed selections and repeated queues.
- Confirm all successful packages appear in completion results and both USB
  actions. Verify per-package progress, partial failure/retry, cancellation,
  insufficient-space rechecking and physical USB removal/error handling.
- Confirm privacy cleanup preserves worlds/configuration except known sensitive
  entries. Inspect packages before sharing. Verify offline-name persistence.
- Confirm default checksums, optional verification skipping and separately
  confirmed cleanup. Skipped/failed verification must retain local packages;
  original Minecraft instances must always remain intact.
- Add a package manually to the installed Playden version using the documented
  program/arguments. Record exact PS5, Playden, Minecraft, loader and mod versions.
  Successful packaging alone is not successful PS5 gameplay.

The public description/topics still require applying the prepared metadata.
The restricted preparation-branch push trigger can build candidates before a
merge; manual dispatch still requires the workflow on the default branch.
Neither trigger authorizes publication, merging or a visibility change. Known-pattern source audits do not detect every possible
encoded secret; review candidate contents as well as automated reports.

## Experimental macOS follow-up — not a v0.1.0 binary gate

macOS is source-only and not yet verified. Optional Apple Silicon/Intel build jobs
remain available for future testing, but their failure, cancellation or unavailability
does not block Windows/Linux verification. Before advertising a macOS binary,
complete native Cocoa startup, Gatekeeper/Finder, architecture, drive permissions,
physical USB and cleanup checks on both architectures. Do not infer acceptance
from Linux tests or macOS mypy targets.

## Retained v0.1.0 candidate

The selected Windows/Linux candidates were built from
`70daa32e92c2fe7cac47737c640ca19c2838bbd4`. Their original workflow wrappers and
release files must match the pinned hashes in `release/v0.1.0-candidate.json`.
Reverification creates a two-platform report and checksums without changing the
binaries. New documentation does not change their recorded build source.
Their embedded guide contains earlier four-platform planning text; distribute
the current installation guide and release notes alongside them. Those documents
supersede the old macOS download plan. A candidate verification pass does not sign
off the physical acceptance items above.
