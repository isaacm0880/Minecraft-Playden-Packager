# Release acceptance

The preparation branch preserves the reviewed GUI features, with a correction for
Windows filesystem aliases in USB result/cleanup bookkeeping and updated frozen
smoke assertions for the current progress scale. The source policy records these
reviewed release fixes. Cleanup still rejects traversal, symlinks and junctions
before normalising a source path; confirmation and checksum gates remain mandatory.
Automated validation during preparation (Linux host, Python 3.14.8 / PySide6 6.11.3):

- Complete pytest suite: 400 passed, 1 optional upstream integration skipped,
  323 subtests passed.
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

- Build all four targets through the candidate workflow; inspect provenance,
  bundled licences, content audits and SHA-256 checksums. Run each archive on a
  clean matching system: Windows x64, Linux x86_64 (glibc 2.36 or newer), macOS
  Apple Silicon and macOS Intel. Check application startup, icons, permissions,
  extraction, GUI scaling and operating-system security prompts.
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
