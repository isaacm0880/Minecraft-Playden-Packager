# PS5 LAN Receiver Manager — development branch

The GUI can start the matching receiver through the **already running** PS5 ELF
loader, pair automatically when encrypted startup credentials are forwarded, pair
with an externally started receiver using six digits, and request authenticated
shutdown. These controls act only when clicked. They do not install a loader,
restart the PS5, deploy unrelated payloads or manage Playden's own game folders.

## Prepare locally

Native desktop candidates include a source-bound receiver ELF and its integrity
manifest. **Start Receiver** deploys that verified ELF directly to an already
running PS5 ELF loader over TCP. No PS5 Payload SDK, `socat`, `plink.exe`, or
separate receiver download is needed for this path. **Export PS5 Receiver (.elf)**
is available under **Transfer advanced options** for manual deployment.
The SDK setting is hidden while the packaged GUI selects its verified bundled
ELF. Selecting a custom ELF exposes the developer SDK setting again.

Source checkouts can build their own receiver:

Install the GUI dependencies and build the receiver with your installed SDK:

```bash
python -m pip install -e '.[dev,gui]'
make -B -C ps5/receiver PS5_PAYLOAD_SDK=/path/to/ps5-payload-sdk
python -m minecraft_playden gui
```

Building contacts no console. The GUI does not download an ELF. Source checkouts
may select the SDK root and `ps5/receiver/minecraft_playden_receiver.elf` under
**Transfer advanced options**. A manually selected ELF without the verified
manifest retains the SDK deployer fallback, which needs `socat` on POSIX or
`plink.exe` on Windows. Windows batch-script paths containing spaces or shell
characters remain rejected for that fallback. A native Windows preview uploaded
the bundled ELF, authenticated the receiver and confirmed normal shutdown on a
physical PS5 on 2026-11-26; this is not a full release validation.

| Service | Default TCP port |
| --- | --- |
| Existing PS5 ELF loader | 9021 |
| Authenticated package transfer | 9025 |
| Receiver-specific management and pairing | 9026 |

Ports are editable and must be distinct. GUI startup patches only its ephemeral
RSA **public** key and receiver ports into a temporary ELF copy. The source ELF
is retained unchanged. No private key, pairing code or transfer credential is
written into either ELF. Keep all services on a trusted private LAN.
Before upload, Start Receiver probes both receiver ports with a five-second TCP
connect timeout. Only an explicit connection refusal establishes that a port is
unoccupied. A listening service, timeout or ambiguous network error blocks
deployment. The GUI reports the preflight, loader connection, upload, startup,
authentication and ready stages in its status and progress log.

## Build, start, send and import

1. Select a Minecraft instance and **Build Playden package**, then choose **Send
   to PS5 (LAN)…** in the completion dialog. The package is already selected.
   Alternatively use **Validate existing package…**.
2. Enter the PS5 IP in **Connect to PS5**. Ensure its ELF loader is
   already running, then click **Start Receiver**. Deployment, RSA generation and
   status checks run in workers; the interface remains responsive.
3. When the loader forwards payload stdout, the GUI decrypts the startup envelope
   in memory and verifies the receiver's session identity, version and HMAC
   response before showing **Running**. Its six-digit session code appears in the
   code field. **Start** never treats a loader stream ending as receiver success.
   A physical Windows preview confirmed this authenticated startup, but the PS5
   notification was not observed during the test. The receiver currently ignores
   the notification API's return value, so authenticated startup does not prove
   that the pairing code appeared on screen. Keep the GUI open to retain its
   in-memory credential if no code is visible.
4. If started externally, enter the six digits from the PS5 receiver notification
   and click **Pair**. **Refresh status** alone supplies only unverified discovery
   information and cannot enable Send or Stop. Codes expire after five minutes,
   are consumed on successful pairing and permit at most five attempts per
   receiver session. A locked/expired receiver must be restarted manually unless
   you still hold an authenticated controller session.
5. Check internal/USB0 destination and the editable instance name, then click
   **Send to PS5 (LAN)**. This reuses the existing validated-package workflow,
   buffered protocol, optional default-off file pipelining, progress, cancellation,
   verification and publication receipts. Local builds and **Copy to USB** remain
   separate operations; USB0 here is attached to the PS5.
6. On confirmed success, use the exact import path in Playden's **Play from Import
   Folder** flow. Publication uncertainty still requires inspection before retry.
7. Click **Stop Receiver** when finished. It requests shutdown of this specific
   authenticated session and confirms both listeners closed. It never powers off
   the PS5 or kills another payload. Stop is disabled during local LAN transfers;
   an externally active transfer is refused by the receiver. Finish or explicitly
   cancel the transfer first, then stop. There is no force-stop override.
   Closure confirmation allows for delayed TCP refusals on both receiver ports.
   If either refusal cannot be confirmed, the status remains **Unknown** even
   when the authenticated stop request was acknowledged.
   When no session credential is available, the same Stop button explains the
   safe recovery options and sends no shutdown request. If the six-digit code
   is still valid, Pair first, then Stop. An expired or consumed code cannot
   recreate a lost high-entropy credential.

IP, SDK/ELF paths, ports and destination are non-sensitive remembered settings.
Codes, RSA private keys, session credentials and stop leases are never stored in
QSettings or logs. Closing the GUI disconnects its local loader stream; it does not
send shutdown. Keep the GUI open until stopping if you need to retain control:
after closing, an expired/used code cannot recover a forgotten credential.
Use the loader's own documented manual stop/unload procedure in that situation.
No separate shutdown ELF is bundled: a process name or listening port cannot
prove receiver identity or atomically establish that package publication is idle.

## Status meanings

| Status | Meaning |
| --- | --- |
| Unknown | Not paired, unverified discovery, lost contact, changed session or uncertain start/stop |
| Starting | ELF deployment and authenticated startup confirmation are pending |
| Running | This version and session authenticated successfully; it may be busy |
| Stopping | Authenticated stop and listener-closure confirmation are pending |
| Stopped | The receiver acknowledged stop and both of its listeners refused new connections |
| Error | Settings, version, pairing or deployment failed; read the adjacent message |

Paired sessions are probed every five seconds while the manager is idle. A failed
probe disables Stop and Send. A changed receiver session clears stale credentials
and requires pairing again. A live loader stream is not evidence the ELF is alive,
and a closed stream is not evidence it stopped. The manager never tears
down an earlier connected loader stream merely to retry Start, nor closes one
receiver's stream as a side effect of stopping another receiver. Returning to an
endpoint owned by this window can reuse its in-memory credential only after an
authenticated Refresh; closing the window forgets it.

## Loader limitations and trust

The SDK `prospero-deploy` sends an ELF over raw TCP and forwards loader output.
The built-in verified-bundle path reproduces that transport directly. Neither
path offers a receiver-specific unload API or proves remote payload
lifetime. Some loaders detach stdout: startup then remains **Unknown**, even if
the ELF is running. Use the PS5 notification and **Pair**, rather than repeatedly
redeploying. Start refuses occupied receiver ports before invoking the loader.
It also refuses to deploy when reachability is too uncertain to exclude an
existing receiver.

Encrypted startup credentials resist passive observation of the raw loader
stream. That stream itself is **not an authenticated code-loading channel**: an
active attacker able to replace the uploaded ELF can replace its behaviour or
bootstrap public key. The GUI cannot repair that loader limitation. Automatic
startup assumes a trusted loader and private LAN. Manual code pairing uses PAKE;
it does not send the code or expose an offline password-checking transcript.
Package data remains authenticated but unencrypted, as in PDLAN/1.

Receiver shutdown closes only its sockets, clears its credentials and returns
normally from its entry point after joining its control thread. Host tests prove
this behaviour on the host. Actual PS5 loader return/unload/resource-reclamation
semantics, and whether a particular loader ties ELF lifetime to its console
stream, require owner acceptance on that loader/firmware. No physical deployment
or benchmark was performed for this change.

Legacy PDLAN/1 receivers remain usable with the existing CLI's hidden 64-character
token prompt/environment option. They do not implement six-digit pairing or
management: the GUI cannot safely stop them. Stop/unload them manually before
starting this branch's receiver. New receivers retain PDLAN/1 authenticated data
framing but acquire their strong credentials through management v2.

The CLI can also pair with the new receiver without putting a code in argv:

```bash
python -m minecraft_playden transfer-ps5 /path/to/package --host 192.168.1.50 --pair
```

This prompts privately for the six digits, discovers the authenticated data port,
and performs the existing transfer. It does not deploy or stop anything.

## Validation and CachyOS checks

Automated tests use loopback C receivers and fake SDK scripts, not a PS5. They
cover GUI responsiveness and repeats, startup envelopes, PAKE reference vectors,
leading-zero codes, invalid public values, expiry, attempt exhaustion, replays,
control/data key separation, authenticated identity, active-transfer races,
short-lived stop leases, unavailable SDK/loader output, unexpected termination,
legacy transfers, retained packages and secret-free settings/logs.

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q --ignore=tests/test_lan_benchmark.py
python -m mypy --platform linux minecraft_playden release
python -m mypy --platform win32 minecraft_playden release
python -m mypy --platform darwin minecraft_playden release
PLAYDEN_RECEIVER_SANITIZE=1 QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_receiver_manager.py tests/test_gui_receiver.py tests/test_network_receiver.py tests/test_network_transfer.py
```

On CachyOS/KDE, run the source GUI, build one package into a path with spaces, and
check output opening from both the completion dialog and main window. Build two
instances and check selecting each successful result opens its own folder. Check
existing-package validation and the manager's initial Unknown state and disabled
Stop without pairing. Verify settings survive reopening while the code is blank.

When choosing to test a real receiver, explicitly start the PS5 loader yourself
and click Start. Check encrypted automatic pairing if stdout is forwarded, or
manual six-digit pairing otherwise; send, verify the import path, stop, then
start again. Cancel a transfer before requesting Stop. Disconnect the LAN to
check Unknown and disabled Stop/Send. These are optional owner acceptance steps;
there is no benchmark requirement. The existing release source-approval gate
is distinct from the source approval and native distribution gates.

The full management wire format and cryptographic construction are documented in
[the receiver protocol](../ps5/receiver/PROTOCOL.md). This is regression coverage,
not an independent cryptographic audit of the new protocol composition.

## Automatic CLI startup

`transfer-ps5 --start-receiver` uses the same `Deployment` implementation as the
GUI, including per-launch RSA encryption of bootstrap credentials and subsequent
authenticated receiver status. It never enters a code prompt or falls back to
plaintext credentials. An already-running compatible ELF loader that forwards
payload stdout is required; the packager does not start the console's loader.

```bash
.venv/bin/python -m minecraft_playden transfer-ps5 \
  "$HOME/Minecraft-Playden-Packages/1.20.1 Fabric Modded-2" \
  --host 192.168.1.50 --destination internal \
  --name "CLI Progress Test" --start-receiver
```

SDK/ELF paths and ports come from the GUI's existing per-user receiver settings.
When no SDK path is set, `PS5_PAYLOAD_SDK`, a `prospero-deploy` installation on
PATH, and standard `/opt/ps5-payload-sdk` layouts are considered. The default ELF
is the bundled verified ELF in a frozen app, or
`ps5/receiver/minecraft_playden_receiver.elf` in a source checkout. CLI
`--sdk PATH`, `--receiver-elf PATH`, `--loader-port PORT`, `--port PORT` and
`--control-port PORT` override startup configuration without changing preferences.

A missing or stale receiver in a source directory with its Makefile is rebuilt
with the installed SDK and local `make`. The build uses temporary output and
replaces the local ELF only after format/startup-slot checks succeed. Failed or
cancelled builds retain a previous ELF. A custom prebuilt ELF without sources
can be used directly; a missing custom ELF needs a valid source location or an
explicit local build. No dependencies, SDK or ELF are downloaded at runtime.
Verified ELF bundles deploy directly; only the unmanifested SDK fallback needs
`socat` on POSIX or `plink.exe` on Windows and retains the existing safe-path
restrictions. Encrypted startup needs the installed
`cryptography` dependency (included in `.[gui]`).

Listening receiver ports prevent another deployment. If the process has an
existing compatible transfer key in the selected `--token-env` environment
variable, it attempts management authentication before reuse; a wrong key,
unsupported receiver, busy receiver or uncertain connection never permits a
replacement. A fresh CLI cannot recover a GUI's memory-only owner credentials.
Use its authenticated GUI **Stop Receiver** action and retry, or explicitly use
`--pair` with an externally launched receiver's still-valid six-digit code.
`--pair` and `--start-receiver` are mutually exclusive. Pairing is never automatic
from a guessed or scraped six-digit code.

Startup stages and transfer/verification progress share the existing renderer.
Keys, codes, private keys and captured loader output are not printed or saved.
Completion and Ctrl-C close/reap the local loader stream and temporary files; they
do not send a remote STOP. The receiver can remain running, including after a
startup failure if deployment occurred. Inspect its state before retrying; never
assume a closed loader stream proves remote shutdown. A lost publication receipt
still requires inspection of the PS5 import path before retrying a transfer.
