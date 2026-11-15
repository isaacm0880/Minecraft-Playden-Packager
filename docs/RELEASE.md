# Preparing v0.1.0

This repository builds its own candidates. Application source is the reviewed GUI
snapshot identified in `release/source-policy.json`; its complete application
hash must match. Public ancestry begins at the public repository bootstrap.
No checkout or history from another repository is required.

## Before building

Review the release-preparation branch, tests, documentation and third-party
notices. Apply the description, homepage and topics in
`release/repository.json` through repository settings. Leave visibility unchanged.
Do not merge, tag or publish without approval.

GitHub requires a manually dispatched workflow to exist on the default branch.
The initial default branch contains only the placeholder README, so candidate
build dispatch remains pending an approved merge of this preparation branch.
See [GitHub workflow dispatch documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch).

After approval and workflow activation, select the intended reviewed branch/ref
in **Desktop release candidate (no publishing)**. Supply its exact 40-character
commit as `source_sha`. The workflow refuses a different workflow/source commit,
a dirty checkout or an application differing from the reviewed source policy.
Future application updates require explicit review and a policy hash update.

## Candidate checks

All four native targets check out the same commit: Windows x64 portable ZIP,
Linux x86_64 AppImage, macOS ARM64 application ZIP and macOS x86_64 application ZIP.
Pinned toolchains, complete pytest runs, three-platform type checks, content
and credential audits, frozen application smoke tests and native GUI startup
checks remain mandatory. Linux uses a Debian Bookworm baseline (glibc 2.36).
macOS candidates use ad-hoc signatures; they are not notarized.

Download each workflow artifact and the final verification artifact. Inspect
`build-metadata.json`, `artifact.json`, `content-audit.json`, `release-pair.json`
and `SHA256SUMS`. Verification checks all four architectures, source manifests,
commit identity and actual artifact sizes/hashes. Keep these provenance records
with the candidate archives. SHA-256 records integrity, not a publisher signature.
Workflow artifacts are candidates, not a public release or evidence of PS5 support.

The optional VirusTotal workflow is manual and lookup-only by default. Review
its privacy implications before enabling any missing-file upload. It requires
an explicit candidate workflow run ID; no historical run is selected implicitly.

Complete [platform acceptance](ACCEPTANCE.md), record the tested source commit,
then update the release-notes draft with actual artifact names and results.
Publishing, tags, merging and visibility changes require separate approval.
The workflows do not create releases or publish binaries automatically.
