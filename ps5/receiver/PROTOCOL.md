# PDLAN/1 transfers and management v2

An explicitly started, single-transfer import receiver. Transfer TCP port 9025
and receiver-specific management TCP port 9026; private IPv4 only (loopback for
host tests). No remote execution, archive extraction, arbitrary destinations or
managed-game access. Public management discovery never authorizes a write or stop.

## Pairing and frames

Legacy receivers display a random 32-byte key as 64 hexadecimal characters; the
legacy CLI prompt/environment path remains compatible. Management-v2 receivers
never print that key in production. They issue independent high-entropy data
credentials through the PAKE or encrypted startup mechanisms below. Neither
credential nor six-digit code is stored in QSettings. Data framing is unchanged.

The receiver sends `PDLAN1\r\n` followed by a random 32-byte server nonce. The PC
sends its random 32-byte nonce and HMAC-SHA256(key, `client` || server || client).
The receiver replies with HMAC-SHA256(key, `server` || server || client). Both
derive HMAC-SHA256(key, `session` || server || client) as the session key.

Every subsequent frame is type:u8, length:u32, sequence:u64, payload:length,
HMAC-SHA256(session_key, direction || header || payload). Integers are unsigned
network byte order. Direction is ASCII `C` or `S`. Each direction has an independent
sequence starting at zero. Frames are at most 65536 payload bytes. Verify the
whole frame before interpreting it or writing data. Session nonces, directions
and sequence numbers prevent captured frames being replayed or reflected.

This authenticates both peers and all transferred bytes, but **does not encrypt
package data**. Use a trusted private LAN; never forward the receiver port. A
paired PC is authorized to upload launch packages, which contain executable
runtime files. Nothing is executed by the receiver.

## Transfer state machine

Only the client sends commands. The server replies with ACK (128) after each
command except DATA, or ERROR (129) containing a bounded UTF-8 error, then closes.

1. BEGIN (1): destination:u8 (0 internal, 1 USB0), name_length:u16,
   entry_count:u32, total_file_bytes:u64, name:UTF-8. ACK after storage preflight.
2. ENTRY (2): kind:u8 (0 directory, 1 regular file), path_length:u16,
   size:u64, sha256:32 bytes, path:UTF-8. Directory size/hash must be zero.
   Parents must already exist; directories precede their descendants. ACK.
3. For files only: DATA (3) frames, then END_FILE (4), empty payload. ACK only
   after declared size, SHA-256 and file flush succeed. Zero-length files still
   require END_FILE. No symlink or special-file entry type exists.
4. VERIFY (5), empty payload, after exactly the declared entries and total bytes.
   Receiver re-reads and hashes every staged file, rejects links and extra entries,
   and checks the recipe, required runtime/argument files and immutable inventory.
   VERIFY_PROGRESS (130) replies contain verified_bytes:u64, total_bytes:u64.
   ACK `verified` means staging passed; it does not publish. Cancellation remains
   available throughout this phase. After VERIFY only COMMIT is accepted.
5. COMMIT (6), empty payload. ACK contains the exact final absolute import path.
   It is the only publication success receipt.
6. Optional GET_METRICS (7), empty payload, only after that receipt, only when the
   BEGIN ACK advertised `ready;metrics=1`. ACK contains the binary schema below.
   The connection closes after this one response. Closing without requesting
   metrics retains the original PDLAN/1 behaviour. Diagnostic builds advertise
   support; other builds retain `ready`. Old clients ignore the BEGIN ACK body;
   new clients send no extension command to old receivers. No unsolicited stats
   frames are inserted before the original exact COMMIT receipt.

Limits: 100000 entries, 768 UTF-8 bytes per relative path, 120 bytes per package
name, 64 GiB per file, 256 GiB total; 16 KiB recipes and 16 MiB inventories.
Paths have at most 32 components and must have no empty/dot/traversal components, NUL/control bytes, backslashes,
Windows drive/ADS separators, reserved names or trailing dots/spaces. Case-folded
ASCII collisions and duplicate entries are rejected. Hidden package names are
rejected. Directory and file opens use descriptor-relative no-follow operations.

`internal` maps only to `/data/playden-import/`; `usb0` maps only to
`/mnt/usb0/playden/import/`. USB0 must already be a distinct mounted filesystem;
there is no fallback. Preflight includes allocation overhead and a 16 MiB reserve.
Actual write/flush errors are also checked; estimates cannot reserve disk space.

After authentication, the receiver creates a random `.pdlan-*` directory under
`/data/playden-packager-temp/` (internal) or
`/mnt/usb0/playden-packager-temp/` (USB0), outside both import roots. It pins the
staging and import directory descriptors, requires them to be distinct and on
the same filesystem, and revalidates their identity before publication. It removes only its own stage on normal errors/disconnects.
On systems lacking exclusive directory rename, publication first reserves an
empty final directory with exclusive mkdir and verifies its identity before
renaming the fully validated stage over that reservation. Existing names are
always refused, including empty directories; overwriting is not implemented.
Local processes with write access to the import root must be trusted.

Disconnect/cancellation before COMMIT discards staging and never publishes. Once
COMMIT is sent, publication can complete even if the receipt is lost. The client
reports that uncertainty and retains every local file. No automatic overwrite,
retry, resume or local deletion occurs. A payload/process crash may leave private
`.pdlan-*` staging directories (or an empty reserved final directory), which must
be inspected and removed manually while the receiver is stopped. An incomplete
stage has no published package path. Idle I/O times out; unauthenticated peers
cannot create files. Tokens rotate when the payload restarts.


Receiver replies coalesce the unchanged header, payload and HMAC into one socket
write (partial writes are still handled). DATA remains streaming without
per-frame acknowledgements. The 64 KiB maximum, sequence checks, pairing, SHA-256,
per-file flushes and readback verification are unchanged. Experimental smaller
DATA frames and socket buffer requests do not alter the wire protocol.

Shutdown commands and automatic shutdown are **not implemented** in PDLAN/1.
See the [future design proposal](../../docs/PS5_RECEIVER_SHUTDOWN.md).


## Publication permissions

Received entries start as 0700 directories / 0600 files behind random private
0700 staging ancestors. After all hashes, namespace, recipe and inventory checks
pass, the receiver opens each declared entry relative to held descriptors with
no-follow flags, refuses links/hard-linked files, and prepares 0777 directories /
0666 regular files, including the package root and nested contents. Metadata is
flushed and failures abort publication. Permission preparation sends ordinary
VERIFY_PROGRESS heartbeats and must succeed before ACK `verified`.

This matches Playden's imported-game access policy for different app/runtime
users, including games rewriting files beside their program. No executable bits
are added to regular Windows data files. Staging ancestors remain private; only
the verified package is renamed into the import namespace. Before COMMIT rename,
the import root is prepared as 0777. USB0's `playden` container gains read/search
bits as needed; the mount itself and Playden-managed directories are untouched.
An existing exact mode is retained without requiring chmod ownership. Mounts
that expose extra file x bits may retain them after a successful fchmod; missing
required bits or chmod/stat errors fail closed. POSIX DAC privacy depends on the
filesystem honouring private directory modes; FAT-like mount masks need physical
verification. No ownership, group, mount-mask or recursive shared-root changes
are performed.

## Optional metrics schema 1

All extension messages use the existing session HMAC, direction and sequence
checks. GET_METRICS is refused before COMMIT and cannot read arbitrary files or
another connection's counters. The response has exactly **153 bytes**:

- schema:u8 = 1;
- seven u64 counters, in order: published, bytes, data_frames, files, fsyncs,
  send_calls, recv_calls;
- twelve u64 durations in **microseconds**, in order: wall, socket_read,
  socket_write, frame_auth, file_write, file_hash, fsync, verify, verify_read,
  verify_hash, permissions, commit.

All integers are unsigned network byte order. The fixed schema contains no
receiver-provided keys, text, secrets, filenames or paths and needs no stdout or
floating-point string formatting. The snapshot is taken before sending its own
reply and before cleanup; timings may overlap. The client checks the exact
length/schema and matching published/byte/file counters. It converts durations
to named `_s` values, keeps them in NetworkResult.receiver_metrics and reports
numeric diagnostics/progress. Failed, unauthenticated, unknown or missing metrics
are discarded; an already authenticated publication receipt remains successful.
Optional metrics waits are bounded separately. Metrics are currently available
for successful transfers; management status and shutdown do not expose profiling counters.

## Optional detailed profile (GET_PROFILE = 8)

Schema 1 and its `ready;metrics=1` advertisement remain byte-for-byte compatible.
After receiving schema 1, a new client may request GET_PROFILE (8, empty payload).
A new receiver accepts at most one such request and replies with an authenticated
ACK containing schema 2. Old clients close after schema 1; old receivers close
there too. A new client retains valid schema-1 metrics if the optional profile
query encounters EOF, an unsupported reply, invalid counters or failed MAC.
Neither telemetry failure revokes the already verified publication receipt.
GET_PROFILE is not accepted before COMMIT or as a substitute for GET_METRICS.
No shutdown command is added.

Schema 2 is exactly **489 bytes**: version:u8=2, followed by 61 unsigned network
byte-order u64 values. The first 19 words are the schema-1 fields in unchanged
order. Next are 17 integers:

`frames, entry_frames, end_frames, reply_frames, entry_acks, end_acks,
recv_wire_bytes, recv_short_calls, frame_recv_calls, rx_cache_hits,
parent_walk_calls, dir_open_calls, clock_calls, write_calls, clock_probe_ns,
clock_probe_valid, buffered_reads`.

The last 25 values are microsecond durations:

`frame_read, frame_parse, frame_socket_read, frame_receive_auth, reply,
entry_parse, entry_create, parent_walk, file_close, hash_finalize, hash_walk,
namespace, recipe, inventory, phase_pairing, phase_setup, phase_frames,
phase_entries, phase_data, phase_end_files, phase_verify, phase_commit,
phase_metrics, unattributed, clock_overhead_estimate`.

The `_s` names in Python use seconds after decoding. The nine exclusive phases
plus unattributed must match snapshot wall time within rounding; other timers
overlap them. Counters and publication totals are validated before acceptance.
The snapshot excludes its own serialization/send and cleanup. It includes the
schema-1 reply and the incoming profile query. No dynamic field names, paths,
pairing material or payload contents are transmitted. See the
[profiling investigation](../../docs/PS5_LAN_PERFORMANCE.md#wired-378-second-transfer-accounting-and-bounded-receive-buffering)
for definitions and before/after results.


## Management v2 (`PDMGR2`)

This is an independently serviced, bounded control listener, normally TCP 9026.
It cannot interpret package writes. It never shares transfer diagnostics state.
The receiver creates a random 32-byte session ID and random 32-byte bootstrap
credential, and uniformly samples a six-digit ASCII code with rejection sampling
from OS `arc4random_buf`. The code appears in the PS5 notification, not stdout.
An explicit HOST_TEST-only legacy banner/private test pipe is compiled out of the
PS5 build. Production stdout contains only public startup information and, when
requested, an encrypted bootstrap envelope.

A management connection receives `PDMGR2\r\n` (8 bytes) and the session ID
(32 bytes). The client selects exactly one ASCII operation:

- `I`: public discovery. Server sends a u16 JSON length (at most 768), then JSON
  containing protocol=2, exact build version, session ID, data/control ports, busy
  flag and empty stop lease. This is unverified and must not enable Send or Stop.
- `P`: six-digit PAKE pairing, below. At most five admitted attempts per receiver
  lifetime, including disconnects/malformed inputs, and only within five minutes
  measured by a monotonic clock. A successful code is consumed. Exhaustion/expiry
  requires an already authenticated controller or a manual receiver restart;
  clients never automatically redeploy. This global budget can be exhausted by
  a hostile peer (availability is not guaranteed), but cannot be reset over LAN.
- `A`: authenticated control. Derive `control_key = HMAC-SHA256(data_credential,
  "control-v2" || session_id)` first. Run the original mutually authenticated
  PDLAN/1 nonce handshake with this separate key, then use its ordered, directional
  authenticated frames. Master and PAKE-issued data credentials are accepted;
  the six-digit code is never accepted as a data or control HMAC key.

An authenticated control session must send empty STATUS (9) first. The signed ACK
contains the same validated status JSON. Unknown protocol/version, wrong session
ID or malformed fields fail closed. When idle, status also contains a random
16-byte `stop_lease`, hex encoded; busy responses contain no lease.

To stop, immediately send STOP (10), payload `session_id:32 || stop_lease:16` in
that same authenticated connection. The lease expires two seconds after status
was sent. The lifecycle mutex checks the accept-time busy snapshot, current busy
state and stopping state before setting stopping. A transfer that begins after
status makes Stop fail with authenticated BUSY, and is not interrupted. New
transfers are refused after stopping is set. No command is queued behind a data
transfer because the control listener has its own thread. Unauthenticated/slow
control peers have bounded reads and cannot create staging or stop a transfer.

ACK `stopping` confirms the request, then the receiver closes its control listener;
the main owner closes the data listener, joins control and clears both credentials
with a volatile wipe before returning normally. Clients only report Stopped after
ACK **and** both listener ports refuse connections. Lost receipts, timeout,
replacement services or unconfirmed closure mean Unknown. They never trigger an
OS kill, a PS5 power operation or automatic retry. Loader unload semantics remain
outside this protocol. Clock failures reject authentication/leases and startup
rather than disabling expiry.

### Fixed SRP-6a suite

The six digits are a PAKE password, not a transfer key. Use RFC 5054 Appendix A's
2048-bit safe-prime group, g=2, SHA-256, username ASCII `playden`, and 256-bit
random private ephemeral exponents. All public group values are exactly 256-byte
big-endian integers; salts are exactly 16 random bytes. This suite uses
`k = H(PAD(N) || PAD(g))`, `x = H(salt || H("playden:" || code))`,
`u = H(PAD(A) || PAD(B))`, and the SRP-6a premaster secret S. Reject A or B zero
modulo N, out-of-range public inputs and zero u. The C server uses pinned BearSSL
constant-time i31 modular arithmetic with fixed-width private exponents.

1. After `P`, server sends one byte: 1 if admitted, 0 if expired/used/exhausted.
   The attempt is charged before receiving any attacker public input.
2. Client sends A (256). Server computes verifier v and fresh B, then sends salt
   (16) and B (256). No verifier or password hash is transmitted.
3. Both derive `K = SHA256(PAD(S))` and transcript
   `T = SHA256(session_id || salt || A || B)`.
4. Client sends `HMAC-SHA256(K, "client-pair-v2" || T)` (32).
5. Only after this proof and a second expiry check does the server install
   `data_credential = HMAC-SHA256(K, "credential-v2" || T)` and consume the code.
   It sends `HMAC-SHA256(K, "server-pair-v2" || T)` (32), then closes the PAKE
   connection. The client must validate that proof and an authenticated STATUS
   for this session before enabling transfer/control.

Fresh salt, A, B and transcript-bound directional proofs prevent replay and do
not expose an offline code-checking oracle. An active guessing party gets only
one password guess per charged attempt; the five-attempt, five-minute budget is
receiver-global. A lost PAKE server proof can be resolved only by successfully
using the already derived high-entropy credential to authenticate this exact
session. A failed proof never enables transfer or control. Successful PAKE grants
one additional in-memory credential; it does not replace the startup owner's key.

### Encrypted automatic startup

The GUI generates a new RSA-3072 key pair (e=65537) in its worker. Only its public
modulus (384 bytes) and data/control ports (u16 each) are placed after the unique
`PDLAN-BOOTSTRAP-RSA-3072-V2:` slot in a temporary ELF copy. The private RSA key is
never serialized. An unpatched external ELF has no bootstrap public key.

The receiver encrypts exactly 82 bytes:
`"PDBOOT2\0":8 || session_id:32 || random_data_credential:32 || code:6 ||
data_port:u16 || control_port:u16` using RSAES-OAEP, SHA-256, MGF1-SHA256 and label
ASCII `Playden receiver bootstrap v2`. Its stdout line is
`PDLAN2_BOOTSTRAP ` plus the 384-byte ciphertext in lowercase hexadecimal.
No cleartext code, credential or private key is printed. The GUI's reader bounds
line sizes, discards other output without logging it, decrypts in memory, checks
ports and format, then authenticates STATUS with the credential and session ID.
RSA key freshness prevents startup-envelope replay across GUI starts.

The raw SDK loader channel is not authenticated code loading. Encryption protects
startup secrets against passive capture; it cannot defend a console against an
active attacker who can replace the ELF being loaded. Such loader trust and
stdout/return/unload limitations are explicitly reported by the GUI/docs. A
missing stdout envelope never counts as successful startup or remote shutdown.

References: [RFC 5054](https://www.rfc-editor.org/rfc/rfc5054),
[BearSSL big integers](https://bearssl.org/bigint.html),
[RSA-OAEP](https://www.rfc-editor.org/rfc/rfc8017).
Independent synthetic SRP premaster-secret vectors are in
`tests/fixtures/pairing/`, in addition to C/Python integration and replay tests.
