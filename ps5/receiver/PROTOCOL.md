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
