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
revision. Candidate artifacts stay attached to the workflow run, not a release. A new
preparation push cancels obsolete in-flight candidate jobs; previously uploaded
artifacts and local build attempts remain intact. Tests have time limits and
Python stack diagnostics so a stalled native GUI check cannot run indefinitely.

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

The required binary targets are Windows x64 portable ZIP and Linux x86_64
AppImage, from the same exact source commit. The `verify-pair` job depends only on
those two jobs and the source guard. macOS is experimental and source-only in
v0.1.0: both native Mac build jobs remain opt-in via `build_macos` on manual
dispatch. They retain their checks, use the same SHA and are non-blocking; optional
Mac candidates are not v0.1.0 release assets. Normal source CI also runs on
Windows/Linux with all three mypy targets; optional native Mac source tests are
available through `test_macos` on the Tests workflow.
Pinned toolchains, complete pytest runs, three-platform type checks, content
and credential audits, frozen application smoke tests and native GUI startup
checks remain mandatory. Linux uses a Debian Bookworm baseline (glibc 2.36).
Experimental self-built macOS candidates use ad-hoc signatures; they are not notarized.

Download each workflow artifact and the final verification artifact. Inspect
`build-metadata.json`, `artifact.json`, `content-audit.json`, `release-pair.json`
and `SHA256SUMS`. Verification checks both required architectures, source manifests,
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

## Reusing the selected v0.1.0 binaries

The approved build source for the retained candidates is
`70daa32e92c2fe7cac47737c640ca19c2838bbd4`; workflow run `43760353213` has successful
Windows and Linux jobs. Its macOS jobs and overall run status are not evidence for
or against the two already-completed native jobs. Record each job's actual result.
`release/v0.1.0-candidate.json` pins their GitHub artifact IDs, wrapper digests and
binary hashes/sizes. Download only those wrappers, check their actual SHA-256 and
sizes, and extract only the five expected regular files into new directories.
Never mix an artifact from a newer preparation run into this retained pair.

Run the current verifier with the recorded **binary source**, not the newer
preparation/documentation commit:

```sh
python -m release.verify_pair --windows REVIEW/windows --linux REVIEW/linux --source-sha 70daa32e92c2fe7cac47737c640ca19c2838bbd4 --output REVIEW/verified/release-pair.json
```

Create the empty `REVIEW/verified` directory first. The verifier checks actual
binary hashes/sizes, exact commit identity, matching source manifests/toolchains,
clean-source flags and successful content-audit/smoke evidence. It emits a report
and `SHA256SUMS` for **exactly two** binaries and refuses to overwrite existing
evidence. Optional `--macos-arm64` and `--macos-x86-64` must be supplied together
for separate future four-platform verification; missing/mismatched inputs fail.

Do not edit/repack the retained binaries to update their embedded older guide:
that would invalidate their hashes and provenance. Supply the current installation
guide and release notes alongside them, explicitly superseding the older Mac
download plan. Current release documentation and the recorded binary build source
are different revisions. Any replacement binary pair must pass all original gates
and be reviewed as a new exact-source cohort.

The optional VirusTotal workflow defaults to Windows/Linux and a verified pair
report from a **completed** new candidate run. It cannot prepare the retained
older run, which has no uploaded two-platform verification report; local pair
verification above is independent of VirusTotal. No VirusTotal upload is required
or authorized as part of this preparation task.
