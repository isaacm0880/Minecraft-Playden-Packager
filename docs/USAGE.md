# Using the packager

Finish installing a supported Vanilla/Fabric instance through Minecraft Launcher,
Prism Launcher or MultiMC. Close Minecraft before building; live world data is not
a filesystem snapshot. The packager never runs your launcher or signs in.

## Discover and build

Select discovered rows, or Add source folder for a custom/portable location. Inspect
source paths, version, loader and status. Ctrl/Cmd/Shift allows multiple selection.
Every selected instance must be eligible before Build enables. Deselect Broken or
Cannot package rows; untested rows may build after the compatibility warning.

Outputs default to sanitised instance names, with collision suffixes. A single
build uses the displayed output path; a multi-build creates named directories
under its parent. Existing outputs are not replaced. Keep source, cache and output
separate. Downloaded public metadata, assets, dependencies and Windows Java may
require internet access; missing local Minecraft clients are never downloaded.

Build runs instances sequentially. Each keeps its own progress, result and error;
a failure during an accepted queue does not stop later items. Cancel stops current
work safely and marks pending items not started. Successful outputs remain. The
completion summary lists every item and outcome count. Open Output Folder opens
the shared parent for a queue, or the individual directory for a single build.

Progress starts at 0% after the startup scan. Stage counters/indeterminate activity
are not total-time estimates. 100% is reserved for a completed successful operation.
The GUI remembers the offline player name in local user settings; the CLI does not
read it. Empty/invalid preferences fall back to Player; spaces and Unicode work.

## Privacy

GUI Privacy cleanup is enabled by default and operates on staging copies before
hashing and validation. It removes known credential/session/identity fields and
private files. Worlds, mods and ordinary settings are preserved. Configs containing
known sensitive fields may be removed. Original instances are never cleaned.

This is a known-pattern filter: binary/archived fields, unfamiliar names and
unlabelled secrets may remain. Review packages and logs before sharing. The chosen
offline display name is included in generated launch files. Do not share clients,
account data, worlds or populated packages without permission.

## Copy the queue to USB

Both Copy to USB actions copy every successful, available output from the latest
build queue. Selecting one result row does not narrow this set. A standalone
Validate existing package action creates a single-package copy context.

Choose the mounted external drive, check its label/root and review every destination
under `playden/import/`. Existing/case-colliding/queued names receive numeric suffixes.
If another process creates a destination after planning, publication refuses to
overwrite it. Advanced manual drive selection requires its own confirmation.

Per-package stages, bytes, verification and aggregate results are shown separately.
A copy failure continues to later items. Cancel preserves completed copies and
stops pending ones. Retry uses only failed/cancelled/not-started requests and their
original planned destinations; a fresh Copy action allocates fresh names for all
still-available successes. Skipped builds/missing outputs are reported with reasons.
Low space offers Check again; drive identity and package checks still run on retry.

Checksum verification is enabled by default. Skipping the extra post-copy pass is
faster but marks copies unverified. Source/destination package validation, write
flushing and safe publication remain mandatory. Safely eject before unplugging.

## Optional local cleanup

Remove local generated package after verified USB copy is off by default each
session. It requires a successful fully checksum-verified individual copy and
confirmation showing the exact local and retained USB paths. Local/USB content,
identity, ownership and unchanged-tree checks run again before deletion.

Only unchanged outputs generated in this session qualify. Existing validated
packages, skipped verification, failures, partial/cancelled copies, changed files,
links and unsafe paths cannot authorise cleanup. Other outputs, original source
instances and USB destinations are never deletion targets. A cleanup failure is
reported separately; inspect local files and retain the verified USB copy. Checking
can be cancelled; deletion finishes once its commit point is crossed.

## Playden

Use your installed Playden version's supported local-game/folder flow for the copied
package, then configure manually:

```text
Playden configuration:
  Program: java/bin/java.exe
  Launch arguments: @../../playden.args

Note: Online play limited to offline mode servers, authentication is not yet implemented
```

Internal recipes remain part of validation, not a promised Playden UI feature.
[Tested configurations and limits](COMPATIBILITY.md) · [Troubleshooting](TROUBLESHOOTING.md)
