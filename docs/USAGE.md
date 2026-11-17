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
separate. Public metadata, assets and dependencies may require internet access;
matching installed files and verified cached downloads are preferred. Missing
local Minecraft clients are never downloaded.

The development source selects the exact Java major required by Minecraft/Fabric
metadata and requires a Windows x64 runtime, even when building on Linux/macOS.
Windows Java runtime downloads are off by default. To permit one, enable **Allow
Windows Java runtime download if missing** for the build queue. Otherwise provide
a matching Java home at `CACHE/runtimes/java-MAJOR-windows-x64`; known local launcher
runtime locations are also checked. Java 8 remains blocked because the preserved
Playden launch command uses Java launcher argument files, introduced in Java 9.
See [compatibility](COMPATIBILITY.md) for the tested metadata matrix and limitations.

For CLI builds, `--java-runtime PATH` selects an existing Windows Java home and
`--allow-runtime-download` permits a missing runtime download. Add `--offline` to
forbid HTTP, including public metadata/dependencies. A first build may need the
public manifest and missing Windows files; subsequent builds can reuse verified
installed/cached files. `python -m minecraft_playden versions` explicitly refreshes
the version catalog; `python -m minecraft_playden --offline versions` lists the
cached catalog. Source packaging tests are not PS5 runtime tests.

**Prepare Fabric for Playden** defaults on for Fabric instances whose metadata
requires remapping. It prepares Fabric's game files before transfer to avoid
first-launch remapping issues on Playden. The worker resolves the actual mappings:
Vanilla and Fabric without remapping work bypass preparation. You may uncheck the
option for troubleshooting; GUI transfer warns about unprepared Fabric packages.

The **Host preparation JDK** field is separate from the Windows Java runtime
packaged for PS5. Leave it blank for automatic native JDK discovery, or use
**Choose Java…** to select a JDK 17+ Java executable with the compiler module.
Linux checks JAVA_HOME, PATH and common `/usr/lib/jvm` installations; Windows also
checks installed and packaged JDKs; macOS checks standard JDK bundles and Homebrew
locations. Java validation and preparation run in the background. Missing or
incompatible Java is reported as an error; no incomplete package is published.
No Wine, PS5 SDK or extra Java download is used for preparation.

For an existing package, click **Validate existing package…**. The status explains
whether preparation is required or an existing matching cache is already valid.
Click **Prepare Fabric…**, choose a parent directory, and let the worker create a
separate prepared package. The original is retained. On success, use Copy to USB
or Send to PS5 (LAN) as usual. Hidden `.fabric` cache files and preparation metadata
are retained; no receiver protocol change is involved.

Minecraft 1.20.1 Vanilla and 1.20.1 + Fabric Loader 0.20.1 with native pre-remapping
are confirmed reaching the PS5 main menu. This does not establish world loading
or compatibility with every mod. Optional manual CLI preparation remains available;
see [compatibility](COMPATIBILITY.md) for its scope and limits.

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

## PS5 LAN transfer (v0.4 development)

Start the custom receiver manually, then enter the PS5 IPv4 address and its
pairing token. Select **Internal storage** or **USB0 storage**, then click
**Send to PS5 (LAN)**. The destination and valid IP address persist per user;
the token is kept only in memory. The default destination is internal storage.
Every available package in the latest successful build queue is sent in order.
An error stops the remaining queue; previously confirmed packages stay on PS5.
Local files are always retained. The existing **Copy to USB** action is separate.

The receiver refuses existing package names. Use the CLI `--name` option to send
another copy under a fresh name. Pairing authenticates transfers but does not
encrypt them: use a trusted private LAN. Cancellation is available until the
publication commit point. A lost completion receipt requires inspecting the
PS5 import folder before retrying.

See [receiver setup and physical testing](PS5_LAN_TRANSFER.md).

## Playden import

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
