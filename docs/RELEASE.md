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

## Starting candidate builds without merging

A push to `release/v0.1.0-prep` runs **Desktop release candidate (no publishing)**.
Only that branch has the automatic candidate trigger. The source job records the
exact pushed public commit; every native job checks out that SHA. It does not
check out another branch, build a pull-request merge commit or select an earlier
revision. Candidate artifacts stay attached to the workflow run, not a release.

GitHub requires a manually dispatched workflow to exist on the default branch.
Manual dispatch therefore remains unavailable while `main` contains only the
placeholder README; the restricted push trigger avoids that dependency without
merging. See [GitHub workflow dispatch documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch).

After an approved merge activates manual dispatch, select the intended reviewed
branch/ref and supply its exact 40-character commit as `source_sha`. Empty,
malformed or different manual input is rejected, never silently substituted.
Both triggers require a clean checkout and an application matching the reviewed
source policy. Future application updates require explicit review and a policy
hash update. GitHub Actions must be enabled and have available runner capacity.

If Actions is unavailable, a candidate can be built from the same clean commit
on a native host using its pinned Python and `release/requirements.txt`:

```sh
python -m pip install -r release/requirements.txt
python -m pip install --no-deps --no-build-isolation -e .
python -m release.build_desktop --source-sha EXACT_PUBLIC_COMMIT --output ../new-candidate-output
```

Use a new output directory every time. Linux additionally requires the exact
container image from `release/toolchain.json` and `release/linux-system-deps.sh`;
Windows/macOS need matching native hosts. Do not cross-compile an archive and
claim it passed a native smoke test. Preserve failed attempts and existing archives.
After a Linux container build, run the frozen executable under X11 with
`xvfb-run -a env QT_QPA_PLATFORM=xcb PATH_TO_FROZEN_EXECUTABLE --smoke-test`.
macOS also requires `python -m release.smoke PATH_TO_FROZEN_EXECUTABLE --native-cocoa`.

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
