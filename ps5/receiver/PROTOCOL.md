# PDLAN/1

An explicitly started, single-client import receiver. TCP port 9025; private IPv4
LAN interfaces only (loopback is supported for host tests). No discovery, remote
execution, archive extraction, arbitrary destinations or managed-game access.

## Pairing and frames

The receiver generates a fresh random 32-byte key at every start and displays its
64-character hexadecimal encoding locally. The PC never sends the key. It must
be entered in the GUI for each receiver session, or supplied to the CLI through
an environment variable or hidden prompt. It is never stored in QSettings.

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
for successful transfers, not via a new failure/recovery or shutdown command.

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
