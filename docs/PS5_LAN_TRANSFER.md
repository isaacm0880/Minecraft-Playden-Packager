# PS5 LAN package transfer — v0.4 development

The PC sends a validated launch package to an explicitly started custom receiver.
It does not deploy the payload, execute a game, modify Playden's managed games,
remove local outputs, or silently overwrite an existing import. Earlier receiver builds were tested on a physical PS5; the changes described
here still require the manual hardware checks below. The published v0.1.0 binaries do not
include it; run the development source on `feat/ps5-network-transfer`.

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

Run the next commands in a second terminal if the loader retains the first one
for payload output. Read the fresh **64-character pairing token** from the PS5
notification or receiver console output. Never paste it into a public report.
It changes on every payload start. The sender prompts for it without echo:

```bash
. .venv/bin/activate
read -r -p 'Existing built package directory: ' PACKAGE_DIR
python -m minecraft_playden validate "$PACKAGE_DIR"
python -m minecraft_playden transfer-ps5 "$PACKAGE_DIR" --host "$PS5_IP" \
  --destination internal
python -m minecraft_playden transfer-ps5 "$PACKAGE_DIR" --host "$PS5_IP" \
  --destination usb0
```

If using a second terminal, set `PS5_IP` there too. For scripted sends the token
can be supplied via `PLAYDEN_PS5_TOKEN`; it is not accepted as a command-line
argument and is never saved in QSettings. The GUI is available with
`python -m minecraft_playden gui`. Enter IP/token, select the destination and
click **Send to PS5 (LAN)**. Close/reopen to check that USB0 persists and the token
does not. The existing local USB-copy feature continues to work independently.

After transfer, inspect the exact package paths printed in the success receipts, then manually select the corresponding
folder in Playden's **Play from Import Folder** flow. Do not move files into or
modify Playden-managed game directories. Launch compatibility is separate from
file-transfer validation and remains unverified.

For an owner-approved interruption test, send a fresh name and cancel in the GUI
or interrupt the CLI before the publication step. Confirm that local files remain
unchanged and the final import name is absent. Re-send an existing name to verify
refusal; try USB0 while it is unavailable to verify the error and absence of an
internal fallback. Do not simulate storage exhaustion by filling the console;
automated tests cover both preflight and write-time insufficient-space errors.

## Pairing, integrity and recovery

The receiver expires after an hour between connections; stop/unload it through
the loader when finished. A fresh random key and per-connection nonces provide
mutual HMAC-SHA256 authentication, ordered authenticated frames and replay
protection. The token authorizes writes to the import roots: keep it private.
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

## Physical testing — Fish

These commands are manual. Stop/unload the old receiver through the loader first;
do not run two listeners on port 9025. Build with numeric diagnostics enabled:

```fish
cd /path/to/Minecraft-Playden-Packager
set -gx PS5_PAYLOAD_SDK /opt/ps5-payload-sdk/ps5-payload-sdk
make -B -C ps5/receiver PS5_PAYLOAD_SDK="$PS5_PAYLOAD_SDK" DIAGNOSTICS=1
read -P 'PS5 LAN IPv4 address: ' PS5_IP
read -P 'ELF loader port (usually 9021): ' PS5_LOADER_PORT
umask 077
"$PS5_PAYLOAD_SDK/bin/prospero-deploy" -h "$PS5_IP" -p "$PS5_LOADER_PORT" ps5/receiver/minecraft_playden_receiver.elf | tee /tmp/playden-receiver-private.log
```

Check `receiver_build=v0.4-import-access-1 stat_backend=freebsd-carry`. The full
log includes a pairing token; keep it private. In a second Fish terminal:

```fish
cd /path/to/Minecraft-Playden-Packager
read -P 'PS5 LAN IPv4 address: ' PS5_IP
read -P 'Existing validated package directory: ' PACKAGE_DIR
.venv/bin/python -m minecraft_playden validate "$PACKAGE_DIR"
.venv/bin/python -m minecraft_playden transfer-ps5 "$PACKAGE_DIR" --host "$PS5_IP" --destination internal --diagnostics
.venv/bin/python -m minecraft_playden transfer-ps5 "$PACKAGE_DIR" --host "$PS5_IP" --destination usb0 --diagnostics
```

Each send prompts privately for the fresh token. Inspect the exact named import
folders only after success. During a transfer, verify that the random private
stage appears under `/data/playden-packager-temp/` or
`/mnt/usb0/playden-packager-temp/`, not inside the import root. Cancel another
fresh-name transfer before COMMIT; its private stage and final import name should
be absent, local outputs unchanged, and unrelated staging directories preserved.
Retry an existing successful name and confirm refusal without changing its files.
Check USB0 unavailable behaviour, GUI destination persistence and local USB copy.
Do not fill the PS5 to simulate ENOSPC; automated fault tests cover storage errors.
If publication reports uncertain durability or a lost receipt, inspect that final
name before retrying; never automatically delete it.

To share diagnostic evidence without the startup pairing banner:

```fish
rg '^(receiver_build=|receiver_metrics |receiver_cleanup |receiver_socket )' /tmp/playden-receiver-private.log > /tmp/playden-receiver-metrics.txt
```

See [performance experiments and reproducible benchmarks](PS5_LAN_PERFORMANCE.md)
for controlled Wi-Fi/wired, internal/USB and buffer comparisons. The
[future shutdown proposal](PS5_RECEIVER_SHUTDOWN.md) is documentation only; no
shutdown command or automatic shutdown option is available in this receiver.


If verification rejects progress after upload, see the
[syscall ABI investigation and metrics lifecycle](PS5_LAN_PERFORMANCE.md#verification-failure-near-completion--syscall-abi-fix).
The current sender includes the rejected numeric counters in its error. Keep
mandatory verification enabled; a rejected frame never authorizes publication.
`receiver_metrics` is flushed before staging deletion; `receiver_cleanup` confirms
whether cleanup completed. Check `diagnostics=1` in the startup build line.


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
