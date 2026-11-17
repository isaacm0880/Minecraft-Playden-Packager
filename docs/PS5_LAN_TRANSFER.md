# PS5 LAN package transfer — v0.4 development

The PC sends a validated launch package to an explicitly started custom receiver.
CLI transfer does not deploy a payload. The GUI Receiver Manager deploys only
on an explicit Start click. Transfers do not execute games, modify Playden's
managed games, remove local outputs or overwrite an existing import. The owner has successfully transferred and launched packages on a physical PS5.
The current work focuses on GUI integration and bug fixes; the manual commands
below remain reference instructions, not a requirement to repeat hardware testing. The published v0.1.0 binaries do not
include it; run the development source on `feat/ps5-network-transfer`.

See [Receiver Manager, six-digit pairing and loader limitations](PS5_RECEIVER_MANAGER.md) for the current GUI workflow. The ELF loader must already be running.

| Selection | Published import root | Private staging root |
| --- | --- | --- |
| `internal` (new-user default) | `/data/playden-import/` | `/data/playden-packager-temp/` |
| `usb0` | `/mnt/usb0/playden/import/` | `/mnt/usb0/playden-packager-temp/` |

USB0 must be mounted; its absence is an error, never an internal-storage fallback.
The receiver cannot write arbitrary destinations. Only these import roots and
its own private staging directories are writable through the protocol.

## Build locally

Use an installed [PS5 Payload SDK](https://github.com/ps5-payload-dev/sdk).
These build commands contact no console:

```bash
export PS5_PAYLOAD_SDK=/path/to/ps5-payload-sdk
make -C ps5/receiver
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev,gui]'
python -m pytest -q
```

The ELF output is `ps5/receiver/minecraft_playden_receiver.elf`. It is not
committed or published. `make` has no deployment target. Building the SDK payload
and running host tests does not establish firmware/loader compatibility.

## Physical testing — manual commands only

Stop automation here. The following commands are for the owner to execute when
ready. A compatible ELF loader must already be running on the jailbroken PS5.
Use the loader's configured port (the SDK deployer defaults to 9021). The receiver
itself listens on TCP **9025**, on the first available private IPv4 interface.

```bash
read -r -p 'PS5 LAN IPv4 address: ' PS5_IP
PS5_LOADER_PORT=9021
"$PS5_PAYLOAD_SDK/bin/prospero-deploy" -h "$PS5_IP" -p "$PS5_LOADER_PORT" \
  ps5/receiver/minecraft_playden_receiver.elf
```

Run transfer from another terminal if the loader retains the first one for
payload output. Read the fresh **six-digit code** from the PS5 notification.
The CLI can pair without echoing the code or putting it in argv:

```bash
. .venv/bin/activate
read -r -p 'Existing built package directory: ' PACKAGE_DIR
python -m minecraft_playden validate "$PACKAGE_DIR"
python -m minecraft_playden transfer-ps5 "$PACKAGE_DIR" --host "$PS5_IP" --pair \
  --destination internal
```

For repeated sends or switching internal/USB0 in one receiver session, use the
GUI, which keeps the strong credential in memory. The one-shot CLI consumes the
code and forgets its derived credential on exit; it cannot reuse that code.
An authenticated GUI that started the receiver remains able to manage it.
Legacy PDLAN/1 receivers still use the CLI's hidden 64-character token prompt or
`PLAYDEN_PS5_TOKEN`. The current GUI has no manual long-token field.

After transfer, inspect the exact package paths printed in the success receipts, then manually select the corresponding
folder in Playden's **Play from Import Folder** flow. Do not move files into or
modify Playden-managed game directories. Launch compatibility is separate from
file-transfer validation; owner-reported successful launches do not guarantee every instance will work.

For an owner-approved interruption test, send a fresh name and cancel in the GUI
or interrupt the CLI before the publication step. Confirm that local files remain
unchanged and the final import name is absent. Re-send an existing name to verify
refusal; try USB0 while it is unavailable to verify the error and absence of an
internal fallback. Do not simulate storage exhaustion by filling the console;
automated tests cover both preflight and write-time insufficient-space errors.

## Pairing, integrity and recovery

The receiver expires one hour after startup, after any active connection finishes.
Use authenticated Stop when finished; use the loader manually for legacy or
unpaired sessions that cannot authenticate. Fresh strong credentials and nonces provide
mutual HMAC-SHA256 authentication, ordered authenticated frames and replay
protection. The in-memory credential authorizes writes to the import roots. The six-digit code is never used as the data HMAC key.
This protocol does **not encrypt** packages, worlds or mods. Use a trusted private
LAN and do not expose TCP 9025 outside it.

The receiver uses no-follow, descriptor-relative file operations; checks path
bounds, entry counts and free-space overhead; re-reads every file for SHA-256;
and validates the constrained recipe and immutable inventory before publication.
The PC also runs the complete exporter validator. The receiver does not run Java
or independently resolve Minecraft dependencies. Full launch validation belongs
to the PC and physical Playden testing.

Cancellation/disconnect before COMMIT discards only the session's private stage.
After COMMIT, publication may finish even if the network connection disappears.
If no success receipt arrives, inspect the exact import path before retrying.
The client never automatically retries or deletes either copy. Existing names
are always refused; choose a fresh `--name` rather than deleting old imports.

Each transfer uses a random `.pdlan-*` subdirectory in its destination’s staging
root, outside the import tree. The receiver pins both roots, requires distinct
directories on the same filesystem, and rechecks their identity before an atomic
rename of the verified package. Cross-filesystem publication fails closed. Normal
cancellation and failures remove only the current session’s stage. Shared staging
roots and unknown directories are never recursively cleared.

A crash/payload kill may leave `.pdlan-*` stages in the staging root or an empty
reserved final name in the import root.
While the receiver is stopped, inspect those paths manually before removing
anything. There is no automatic deletion of abandoned or unrelated directories.
On platforms without exclusive rename, the receiver reserves a new empty final
directory and verifies that reservation before the atomic rename. Local
processes with write access to the import root must be trusted. File `fsync`
must succeed; unsupported directory `fsync` is tolerated. Actual USB durability,
mount detection, notifications and Playden access permissions need physical tests.

Historical v0.1.0 release manifests, checksums and source approval remain intact.
The existing release gate intentionally rejects these new development sources
until a separate release review. No v0.4 binary release is produced here.


## CLI progress

On an interactive terminal, `transfer-ps5` updates one progress bar at most ten
times per second, with bytes, percentage, smoothed current speed, average speed,
elapsed time and ETA. Validation, pairing, verification and publication have
separate stage messages. Unknown speeds/ETAs display `--`; rates reset when a new
stage starts. Ctrl-C and errors finish the line before the diagnostic appears.
When stdout or stderr is redirected, or `NO_COLOR` is set, the CLI writes plain
stage records and at most one intermediate record every 30 seconds to stderr;
the final receipt remains on stdout. GUI callbacks retain the same byte fields.

## Optional physical acceptance

The owner reported successful transfers and launches with earlier receivers.
For this manager update, validation uses host receivers and fake SDK tools; no
physical deployment or performance benchmark was performed. Follow the optional
[Receiver Manager acceptance steps](PS5_RECEIVER_MANAGER.md#validation-and-cachyos-checks)
when ready. The manager's authenticated idle-only Stop is implemented; automatic
exit-after-success remains unimplemented. See
[shutdown behaviour](PS5_RECEIVER_SHUTDOWN.md).

Historical diagnostic investigations remain in
[PS5_LAN_PERFORMANCE.md](PS5_LAN_PERFORMANCE.md). They describe earlier receiver
versions and token banners; they are not current GUI setup instructions or a
requirement for new benchmarking. Mandatory verification remains enabled.

## Instance names and import access

`--name` is optional. Both GUI and CLI now read the recorded instance display
name from the hashed `metadata/instance-name.json` (schema 1, string `name`) in
new builds. GUI builds preserve the selected installation's name; CLI builds
resolve the local installation name when available, otherwise the chosen output
folder name. Transfer reads this metadata without changing local files. Renaming
an export folder keeps the recorded default. Older packages without the metadata
use their package folder name, matching the existing standalone GUI default.
The recipe's generic Minecraft version title is not used as an instance name.

The same 120-byte portable single-folder validation applies to defaults and
`--name`. Invalid recorded names are refused; choose an explicit valid override.
An existing remote name is always refused, including an empty directory. A
custom `--name My-New-Instance` selects a different name without changing the
local package or enabling overwrite. No dates or LAN test prefixes are generated.

Playden's imported-game repair policy uses 0777 directories and 0666 files so
its different runtime user can update saves, configs and files beside a program.
The receiver now prepares that policy before atomic publication, including all
nested contents and the import root, while keeping enclosing staging directories
0700. It avoids adding executable bits to regular files and does not chown,
change mount settings, follow symlinks or change managed game directories.
Permission errors abort and clean staging. Re-check internal and USB0 discovery,
launch, saving, config changes and filesystem-reported modes manually on PS5;
only recursive 0777 was confirmed by the prior physical report, so the narrower
regular-file 0666 policy still needs that check. Some USB filesystems expose a
mount-wide mode rather than independent private/per-file modes.

With a diagnostic receiver build and `transfer-ps5 ... --diagnostics`, the sender
now prints a `receiver_metrics` JSON line received over the existing paired LAN
connection. The GUI logs a receiver verification/permissions/flush summary and
retains the full numeric record in its transfer result. This does not rely on
ELF-loader log forwarding. Old receivers still transfer normally without this
optional record. Metrics loss after a valid receipt is reported as optional
diagnostics unavailability, not as a failed package transfer. See the
[authenticated wire extension](https://github.com/isaacm0880/Minecraft-Playden-Packager/blob/feat/ps5-network-transfer/ps5/receiver/PROTOCOL.md#optional-metrics-schema-1)
and [performance investigation](PS5_LAN_PERFORMANCE.md).


The current diagnostic receiver also offers an optional detailed profile after
its original metrics reply. New senders request it automatically; older peers
retain the original metrics/receipt behaviour. Look for `profile_version: 2` and
`phase_*_s` fields in the CLI's authenticated `receiver_metrics` JSON. The receiver
now buffers bounded incoming bytes by default; frame authentication and package
validation still precede writes/publication. `--pipeline-files` is an optional
CLI experiment with at most two file ACKs pending; it is off by default and does
not change GUI sending. See the performance guide for controlled manual trials.

## GUI build and send workflow

1. Select a local instance and **Build Playden package**, then choose **Send to
   PS5 (LAN)…** in the completion dialog. No folder selection is repeated.
   **Validate existing package…** loads a previously built package instead.
2. In **PS5 LAN Receiver Manager**, choose the PS5 IP and configure SDK/ELF paths
   if needed. Ensure the PS5 ELF loader is running, then click **Start Receiver**.
   It uses encrypted automatic pairing when loader stdout is available. An
   externally started receiver instead uses its six-digit PS5 code and **Pair**.
3. Select internal/USB0 and check the editable **PS5 instance name**. The recorded
   instance name survives a renamed local folder. A multi-build queue sends all
   successful launch packages with their own names; selecting a result changes
   which local output folder is opened. **Copy to USB** separately writes to a
   drive attached to this computer.
4. Click **Send to PS5 (LAN)**. Validation, sending, receiver verification and
   publication are separate stages. Speed and ETA describe sending. The progress
   bar completes only after a confirmed publication receipt. Once COMMIT is sent,
   cancellation cannot undo publication; inspect uncertain import paths before
   retrying. Local outputs remain.
5. Open the confirmed path through Playden's **Play from Import Folder**, then
   use **Stop Receiver** when idle. Stop never interrupts an active transfer.
   Closing the app only closes its local SDK connection, not remote shutdown.

Non-sensitive settings are remembered. Session secrets and codes are not.
Buffered receiver reads remain the default; **Advanced: pipeline files
(--pipeline-files)** is optional and initially disabled. See the
[manager guide](PS5_RECEIVER_MANAGER.md) for states, code expiry, security,
manual recovery and the limits of the SDK loader's raw TCP transport.

### CachyOS / KDE regression check

Run the development GUI using `python -m minecraft_playden gui` after installing
`.[dev,gui]`. Published v0.1.0 binaries do not contain these fixes. The historical
v0.1 release source-approval gate deliberately blocks a new AppImage from this
unreleased branch; it has not been changed. AppImage folder-launch environment
and helper failures have regression coverage, but native acceptance of a new
AppImage awaits a separately approved desktop build.

- Build one package into a path containing spaces. Use **Open Output Folder**
  in both the completion dialog and main window; Dolphin should open the exact
  package folder. The next build must remain available with a fresh output path.
- Build two selected instances. The queue dialog opens the common output parent;
  selecting a successful row and using the main window button opens that package.
  Selecting a failed row must disable the button instead of opening a stale path.
- Validate an existing package, then check that opening and sending refer to it.
  Repeat a build, validation and cancelled transfer without restarting the app.
- Check a source launch and AppImage launch under your usual KDE session. Frozen
  Linux builds use an asynchronous system `xdg-open` with bundled Python/Qt library
  paths removed. Missing handlers or nonzero exits must show a message containing
  the folder path and recovery instructions, and write the same error to the log.
  Ensure `dolphin` and `xdg-utils` are installed and the desktop's default folder
  handler is configured if opening fails. Desktop acceptance is not established
  by an offscreen test; check the actual file manager window.
- Check the manager starts Unknown with Stop disabled. Enter IP/code and pair, or
  explicitly click Start with an already running ELF loader. Check internal/USB0,
  an edited name, disabled Stop while sending, cancellation, verification,
  publication and the final import path. Stop when idle. Close and reopen:
  IP/destination remain, code and credentials are blank. No benchmark or physical
  transfer is needed to test local build/folder controls.
