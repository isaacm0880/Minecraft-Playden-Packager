# LAN transfer performance investigation (#17)

The reported 0.8–1.4 MiB/s Wi-Fi/Wi-Fi PS5 rate is not yet explained by a physical
timing trace. Host loopback results below do not measure the PS5, its Wi-Fi radio,
firmware, storage or SDK crypto cost. No console was contacted or deployed to.

## Findings and changes

- DATA already streams without per-frame ACKs. A directory requires one round
  trip; a file requires ENTRY and END_FILE round trips, plus a durable file flush.
  With many small files, throughput can be limited by latency and flushes even
  when bulk link throughput is high. A 4 KiB file with two 5 ms round trips has
  an upper bound of about 0.39 MiB/s before disk and hashing work. Two Wi-Fi peers
  may also share airtime; neither effect is established as this PS5's cause.
- Both peers already use TCP_NODELAY. Previously each receiver reply used separate
  header, body and HMAC sends. Replies now use one contiguous bounded packet and
  one send loop. This reduces tiny sends without changing framing or authentication.
  A partial send still loops correctly. New receiver counters show 716 socket
  sends for the small fixture versus the old implementation's structural 2,144
  (two handshake sends plus three sends per reply); no per-file ACK was removed.
- Sender chunk progress/formatting is now bounded to ten callbacks per second,
  plus stage boundaries and final byte totals. The GUI uses the same byte fields.
  Current throughput uses recent samples rather than the old lifetime average.
- Default DATA payload remains 64 KiB. A 4 KiB experiment was slower for bulk data.
  Raising the limit would require coordinated protocol changes and more receiver
  memory; no evidence yet justifies it. Defaults retain OS socket buffers.
- Optional sender `--socket-buffer BYTES` and receiver `SOCKET_BUFFER=BYTES` request
  send/receive buffers **before connect/listen**, with effective sizes and failures
  in diagnostics. Limits are 4 KiB–4 MiB for runtime requests. Unsupported/clamped
  requests do not disable safety checks. OS buffer accounting/autotuning differs;
  Linux may report twice the requested size. Initial sender values are recorded
  before connect; the OS may grow them later. A 512 KiB experiment did not help
  loopback. It remains an explicit hardware experiment rather than a default.
- Receiver hashes every DATA frame for HMAC, hashes file bytes while writing and
  hashes them again during readback. Hashing dominates the RAM-backed bulk test.
  Sender pre-validation, full manifest hashing and upload-time hashing protect
  against invalid or changing local sources. These checks remain in place.
- File writes remain synchronous and bounded to one frame; every file is flushed
  before its END_FILE ACK, with final directory flushes around publication. No
  buffering/pipeline change weakens failure detection, cancellation or integrity.
  A writer pipeline could overlap network, hashing and disk but introduces buffer
  ownership and cancellation complexity. Measure hardware first. Do not remove
  per-file fsync or readback just to increase the displayed number.

## Upstream review and attribution

Reviewed `owendswang/ps5-web-file-manager` at commit
[`734bda6a75c0594bf4221d337862e55973c7d267`](https://github.com/owendswang/ps5-web-file-manager/tree/734bda6a75c0594bf4221d337862e55973c7d267):

- [transfer.h](https://github.com/owendswang/ps5-web-file-manager/blob/734bda6a75c0594bf4221d337862e55973c7d267/src/transfer.h)
  defines 512 KiB socket, 8 MiB read and 2 MiB HTTP buffers, with a 256 MiB pipeline threshold.
- [websrv.c](https://github.com/owendswang/ps5-web-file-manager/blob/734bda6a75c0594bf4221d337862e55973c7d267/src/websrv.c)
  sets the listener receive buffer before listen, accepted send buffers and NODELAY,
  and uses libmicrohttpd polling/TURBO with an 8 MiB connection memory limit.
- [upload.c](https://github.com/owendswang/ps5-web-file-manager/blob/734bda6a75c0594bf4221d337862e55973c7d267/src/upload.c)
  batches 1 MiB upload writes, optionally uses a writer thread, flushes files and
  publishes by rename. [transfer.c](https://github.com/owendswang/ps5-web-file-manager/blob/734bda6a75c0594bf4221d337862e55973c7d267/src/transfer.c)
  reports requested/effective socket options and has a bounded reader pipeline.
- The [README credits](https://github.com/owendswang/ps5-web-file-manager/blob/734bda6a75c0594bf4221d337862e55973c7d267/README.md)
  identify `seregonwar/zftpd` as a socket tuning/throughput reference.
  [v1.5 release notes](https://github.com/owendswang/ps5-web-file-manager/releases/tag/v1.5)
  describe faster HTTP uploads/downloads and reduced speeds for small files;
  [v1.10](https://github.com/owendswang/ps5-web-file-manager/releases/tag/v1.10)
  describes further transfer improvements. Those are upstream observations under
  different conditions, not a comparable PDLAN benchmark or guaranteed throughput.

Only concepts were used. No GPL source, assets, threads, HTTP server or new
third-party dependencies were copied into this project. Our existing pinned
0BSD SHA-256 implementation is unchanged. Larger buffers alone cannot reproduce
an upstream result across different protocols, hardware, firmware and safety work.

## Opt-in diagnostics

Sender: `transfer-ps5 ... --diagnostics` emits a single numeric `sender_metrics`
JSON object on stderr, including on errors. Counters include bytes/files/frames,
socket calls, send/receive call wall time, frame authentication time, validation,
prehash, source reads/upload hashes, pairing, transfer, receiver verification,
commit and total wall time. Diagnostics are disabled by default.

Receiver: build with `make -B ... DIAGNOSTICS=1`. The payload prints one
`receiver_metrics` record per authenticated connection on success, failure or
disconnect, before cleanup, explicitly flushed. Metrics include publication state,
bytes/files/DATA frames,
send/receive calls, socket read/write times, file write/hash time, fsync count/time,
verification/read/hash time and commit time. A separate `receiver_cleanup` record
reports cleanup duration, completion and errno. `receiver_socket` records show
requested/effective buffers and errno. There is no per-frame logging. The normal
startup banner contains the pairing token; **do not share raw receiver logs**.
Share only metrics/socket/build lines after checking them. Timing records contain
no tokens, keys, package names, paths or file contents.

Timings are elapsed wall time, not CPU profiling. Socket send time includes kernel
calls that may block; it is not pure network time. Socket reads also include
waiting for the other peer. Sender verification time includes receiver work and
reply latency; receiver verification includes its read/hash sub-timings. Validation
includes prehash. These nested/overlapping fields must not be summed. Clock calls
add overhead in diagnostic builds, especially for very small frames. OS scheduling,
cache state, antivirus and CPU power policy can affect results.

## Reproduce without a PS5

Use the development checkout with `.[dev]` installed and a POSIX C compiler. The
helper performs a real offline fixture export using shared test helpers; it
requires the repository's `tests/` directory. The synthetic Java/Minecraft files
are transfer fixtures, **not playable packages**. Never launch them in Playden.

Fish-compatible commands, from the repository root:

```fish
.venv/bin/python tools/lan_benchmark.py prepare /tmp/playden-lan-dataset
.venv/bin/python tools/lan_benchmark.py loopback /tmp/playden-lan-dataset --output /tmp/lan-default.jsonl
.venv/bin/python tools/lan_benchmark.py loopback /tmp/playden-lan-dataset --output /tmp/lan-buffer.jsonl --socket-buffer 524288
.venv/bin/python tools/lan_benchmark.py loopback /tmp/playden-lan-dataset --output /tmp/lan-4k.jsonl --chunk-size 4096
mkdir -p .cache/lan-benchmark
.venv/bin/python tools/lan_benchmark.py loopback /tmp/playden-lan-dataset --output /tmp/lan-disk.jsonl --scratch-dir .cache/lan-benchmark
```

`prepare` refuses an existing directory. Reuse the exact prepared directory for
all trials; each result records a digest of sorted relative names and content
hashes. Default datasets add one 32 MiB file or 300 × 4 KiB files to the same valid
fixture. Optional `--large-mib`/`--small-count` adjust preparation size within
bounds. Default three repeats use new receiver processes and fresh random package
names. Results append to JSONL; choose a new output file per experiment. Host
runs delete only their owned temporary sandbox after stopping the child receiver.
`--scratch-dir` chooses an existing host filesystem directory; no arbitrary
scratch-root deletion occurs. OS temporary storage may be RAM-backed, so use a
real mounted disk for storage measurements. No cache dropping, sysctl changes,
traffic shaping, payload deployment or console contact occurs in loopback mode.

For a comparison receiver, compile the old checkout with the same host compiler:
`cc -std=c11 -DHOST_TEST -O2 -Wall -Wextra -Werror main.c vendor/sha-256.c -o receiver-host`
from its `ps5/receiver/` directory. Use the current benchmark/sender with
`--receiver-binary /absolute/path/receiver-host --legacy-receiver`. The old receiver
lacks timing records; this comparison holds the new sender and dataset constant.

## Recorded host results, 2026-11-16

Linux 7.3.1, Ryzen 5 5600X, GCC 16.2.1 `-O2`, Python 3.11.16, TCP loopback;
source and initial receiver sandbox on `/tmp` tmpfs, warm caches, default OS TCP
settings unless specified. Sequential configurations, three repeats each, no
parallel test suite. The old receiver is `2c29c46c506ade3596f271abb95d26a457162d3e`;
all comparisons use the new sender and identical dataset content. New receiver
metrics enabled; old receiver cannot provide them. These short trials indicate
regressions/costs, not statistically established throughput improvements.

| Configuration | Large median MiB/s | Small median MiB/s |
| --- | ---: | ---: |
| Old receiver, new sender | 82.64 | 8.11 |
| Updated receiver, OS buffers, 64 KiB | 82.77 | 9.25 |
| Updated, requested 512 KiB buffers | 83.05 | 9.03 |
| Updated, 4 KiB DATA frames | 73.83 | 9.34 |

Large dataset: 33,568,041 bytes, 30 files. Small: 1,242,409 bytes, 329 files.
The original sender+receiver baseline separately measured about 81 MiB/s and
9.3 MiB/s; it was a preliminary run, not the controlled table comparison.
The updated small-file result spans 9.11–9.30 MiB/s versus 7.72–9.11 for the old
receiver. Large-file changes are within a few percent; no bulk improvement is
claimed. Reply batching's deterministic benefit is two fewer socket calls per
reply; any Wi-Fi benefit still needs physical testing.

A representative updated large trial spends about 0.11 s each authenticating
frames, hashing writes and hashing readback, within about 0.36 s receiver wall
time. File writes take about 0.008 s and readback reads about 0.004 s. Tmpfs fsync is
negligible and cannot establish disk flush performance. The small fixture has
329 file flushes plus directory flushes and many metadata round trips.

The 4 KiB trials range from 38.54–77.26 MiB/s: one scheduling outlier reinforces
why three runs cannot establish small percentage gains.

A second run uses a SATA-backed Btrfs receiver sandbox and the same tmpfs source:
updated receiver medians are **74.22 MiB/s large**, **2.35 MiB/s small**. Here real
file flushes matter; the small fixture's receiver fsync time is about 0.36 s.
These are host storage results, not a measured PS5 explanation. The unmodified old
receiver failed its verification walk on Btrfs, so no old disk throughput is
reported. Investigation found `fdopendir(dup(root))` reusing directory enumeration
state: it could see no entries after files were added. Each verification/cleanup
walk now opens `.` relative to the pinned directory with no-follow flags, creating
an independent stream. Repeated scans and cleanup are regression-tested, and
directory read errors now fail the walk rather than silently appearing as EOF.
No absolute-path fallback or symlink check was relaxed.

Raw numeric trials, dataset fingerprints and disk-backed records are
in [the JSONL record](benchmarks/lan-loopback-2026-11-16.jsonl). Re-run on the same
machine/filesystem and increase repetitions before drawing narrow percentage
claims. Receiver cryptographic acceleration or pipelining needs a separate,
measured security review if hardware traces identify it as the bottleneck.

## Physical measurements — owner-run only

Build/deploy manually using [the Fish commands](PS5_LAN_TRANSFER.md#physical-testing--fish).
Use the same prepared dataset and fresh imports, and do not launch fixture files:

```fish
read -P 'PS5 LAN IPv4 address: ' PS5_IP
.venv/bin/python tools/lan_benchmark.py remote /tmp/playden-lan-dataset --host "$PS5_IP" --destination internal --output /tmp/lan-wifi-internal.jsonl
.venv/bin/python tools/lan_benchmark.py remote /tmp/playden-lan-dataset --host "$PS5_IP" --destination usb0 --output /tmp/lan-wifi-usb.jsonl
```

The sender prompts for the fresh pairing token without echo. Remote benchmarking
publishes new `LAN-Bench-large-*`/`LAN-Bench-small-*` packages; it does not remove
remote packages, resume, overwrite, launch files or deploy/unload a payload. Keep
storage use in mind when repeating; inspect imports manually afterward. Capture
filtered receiver metrics separately (remote JSONL also includes authenticated receiver metrics when supported).

Record PS5 model/firmware/loader, internal or USB filesystem/device, PC OS/CPU,
router/AP, band/channel, signal strength, shared airtime/other traffic, negotiated
link rates, wired/wireless for **both** peers, dataset fingerprint and repetitions.
Use the original receiver binary and updated receiver with the exact same dataset
for before/after, stopping/unloading each manually through the loader. Never run
two receivers at once. Compare Wi-Fi/Wi-Fi, then Ethernet/Ethernet if available,
then the optional 512 KiB receiver build and sender `--socket-buffer 524288`.
Buffer build variables require `make -B` and manual redeployment with a fresh token.
Keep frame size fixed while comparing buffers; use smaller frames as a separate
experiment. Repeat with real, validated packages separately from synthetic data.

If an already installed/trusted file manager offers HTTP upload, optionally time
its upload of the **same large file** and the same many-small-file dataset to an
owner-chosen temporary directory outside Playden-managed paths. Use that service's
existing UI/authentication and manual cleanup; do not install or expose an
unauthenticated service for the comparison. Record whether its timer includes
fsync/readback/hash work: HTTP completion is not necessarily equivalent to PDLAN's
verified publication. No HTTP request is run automatically by our benchmark.

Interpret high receiver file-write/fsync times as storage evidence; high hash
times as CPU/crypto evidence; long sender socket sends with receiver read waits
and low disk/hash times as a reason to inspect Wi-Fi/TCP/window behaviour. High
small-file time with fast bulk transfer points to per-file latency/flush overhead.
Do not infer a single cause from one cumulative throughput number. The remaining
PS5 bottleneck and any physical throughput gain require these manual traces.


## Verification failure near completion — syscall ABI fix

A physical wired transfer reported 937,944,806 bytes / 5,678 files uploaded, then
`Invalid receiver verification progress` near 99% verification. The previous
sender error discarded the rejected values, so the exact physical frame's two
integers cannot be recovered from that message. No console was deployed to or
inspected during this investigation.

The defect is in `sdk_fs.h`'s inline assembly contract. The
[reference FreeBSD fast syscall return](https://github.com/freebsd/freebsd-src/blob/releng/9.0/sys/amd64/amd64/exception.S#L397)
restores C-preserved registers and gives bonus preservation to the first two
arguments. RDX holds a second return value; R8/R9/R10 are not restored by that
fast path. Our helper declared only RAX, carry, RCX and R11 modified, allowing
optimized code to keep live values in unpreserved registers across `syscall`.
The fix marks argument registers read/write and declares R8/R9 clobbered, while
retaining carry-based errno translation. It does not rely on bonus preservation.

Inspection of the previous SDK-built ELF found this concrete sequence in
`count_tree`: the fifth argument (expected total) arrives in R8, `openat` executes
inline, then R8 is saved for later use. Every 128 namespace entries the saved
value is encoded twice into a heartbeat. An unpreserved R8 therefore changes
both `verified` and `expected_total` **before** their HMAC is computed: message
authentication still passes, but the sender correctly rejects the inconsistent
total. This walk starts after all file readback hashes pass, explaining the late
failure after the last displayed hash update near 99%. On Linux, normal POSIX
calls preserve live C values through compiler-generated call boundaries, so
ordinary host socket tests did not exercise this PS5-only assembly contract.

An optimized host assembly gate now deliberately clobbers the ABI's volatile
registers. With the original constraints, the live-total assertion fails or a
live pointer crashes; with the fix both GCC and Clang preserve the exact reported
total, values above 4 GiB and the 256 GiB protocol limit. The gate invokes no
kernel syscall. SDK ELF inspection confirms the total is now saved before the
syscall rather than trusting its previous register value afterward. Physical
register contents and firmware behaviour still require an owner-run re-test.

Both readback and namespace heartbeats now share one encoder: two unsigned
big-endian 64-bit integers (`!QQ`), exactly 16 bytes. Readback counts bytes once
per file and guards addition against the declared total. The namespace pass
reports total/total only after every file hash passed. The sender checks payload
size, equal expected total, bounded verified bytes and monotonicity, including
repeated equal heartbeats. Rejections include numeric `verified`,
`receiver_total`, `sender_total` and `previous_verified` values. No token, key or
local filename is included. No COMMIT follows a rejected frame.

Regression coverage includes 5,678 files and dozens of both kinds of heartbeat,
malformed payloads, wrong totals, excess bytes, backwards progress and preservation
of local files/no publication after rejection. A separate offline fixture transfer
with **exactly 937,944,806 bytes / 5,678 files** completed on host loopback with
983 valid progress frames, a final count of 937,944,806, verified publication and
successful staging cleanup. That is a correctness reproduction at the reported
scale, not a measurement or physical PS5 compatibility claim.

### Missing metrics after disconnect

Previously `receiver_metrics` was emitted only after closing files, recursively
removing all staging contents and releasing inventory. Deleting 5,678 files can
delay that line; the same syscall register defect also exposes cleanup's live
values to corruption. A crash, forced unload or blocked filesystem call before
the report would prevent it entirely. Without the old receiver/loader trace,
the console's exact termination or blocking point cannot be established.

Stdout was already unbuffered via `setbuf(stdout, NULL)` before listening. Normal
SIGINT/SIGTERM set the stop flag; SIGPIPE is ignored. Metrics are opt-in, so the
startup build line now explicitly includes `diagnostics=0/1 stdout=unbuffered`.
Build with `make -B ... DIAGNOSTICS=1`; reusing an older ELF cannot enable them.
Socket sends additionally suppress SIGPIPE per call where supported, and reject
impossible oversized I/O return counts instead of underflowing the remaining
length. Existing socket timeouts still bound network waits; they cannot bound
kernel filesystem deletion/close latency.

The transfer metrics record now runs **before filesystem cleanup**, with an
explicit flush. Its `wall_s` excludes cleanup; a second, flushed
`receiver_cleanup wall_s=... complete=... errno=...` record follows cleanup.
`published=0` still means no publication, not that staging cleanup has completed.
A deterministic host gate blocks deletion after the metrics record: the test
reads the record through a pipe while the receiver is alive and the stage still
exists, releases cleanup, observes the completion record, and pairs again with
the same receiver. No receiver termination is needed to flush metrics. Existing
recorded benchmark JSONL predates this timing boundary and remains unchanged.

For a manual re-test, expect build `v0.4-verify-abi-1` with `diagnostics=1` and
capture only `receiver_build`, `receiver_metrics`, `receiver_cleanup` and
`receiver_error` lines, plus any sender numeric counter error. Keep the pairing
banner private. A closed or backpressured loader/stdout pipe can still lose or
block output even when unbuffered; a process killed before reaching the report
cannot emit it. Inspect loader exit/crash status separately. This change adds no
PS5 log files, automatic shutdown, deployment or new write destinations.


## Import permissions and metrics delivery update

The physical owner subsequently confirmed a successful transfer and apparent
Minecraft launch after recursively applying 0777 permissions. The previous
0700/0600 package modes were private to the payload user. Review of Playden's
`src/stores/local/drop_folder_access.h` shows its imported-game policy opens
folders to 0777 and files to 0666 while retaining pre-existing executable bits.
The receiver now prepares that policy without adding file executable bits,
through descriptors inside its private stage. The `permissions_s` timer measures
this preparation separately. `fsyncs`/`fsync_s` now also include flushing changed
file permission metadata; permission time overlaps that flush time. Older raw
benchmark records remain unchanged and predate this policy.

The owner still did not receive receiver metrics in the deployment log despite
successful transfers with diagnostics enabled. Successful host pipe tests establish
that our stdout is unbuffered and explicitly flushed, but cannot establish how
the physical ELF loader retains/forwards the payload's descriptors. The SDK's
[deployer](https://github.com/ps5-payload-dev/sdk/blob/v0.45/host/bin/prospero-deploy)
forwards its deployment socket through socat; that is a different connection from
PDLAN's authenticated transfer socket. No physical stdout trace or loader exit
status is available to prove a specific forwarding failure. No speculative log
files or automatic redeployment were added.

Diagnostic receivers now advertise optional metrics in the BEGIN ACK. A new
sender requests a fixed-size, authenticated binary snapshot **after** validating
the exact COMMIT receipt. This bypasses deployment-log forwarding and printf
formatting entirely. The record has no paths, names, tokens or dynamic keys.
It includes transfer counters and twelve timings (including permissions), encoded
as unsigned microseconds. The CLI prints it as a separate `receiver_metrics`
JSON line with `--diagnostics`; sender diagnostics also include prefixed receiver
values. GUI progress logs a short numeric summary; NetworkResult retains the
record. The benchmark prefers that authenticated snapshot for both local and
remote runs, falling back to stdout only for older host receivers.

Old clients receive the same COMMIT path and close normally. New clients never
send the optional command to a receiver that does not advertise it. Unknown
versions/lengths, mismatched counters, failed authentication or connection loss
make only the optional record unavailable: a valid publication receipt remains
successful. The extension neither enables remote shutdown nor changes pairing,
file validation, ports or no-overwrite rules. Physical delivery of the binary
snapshot and Playden access with regular-file mode 0666 require manual verification
with receiver build `v0.4-import-access-1`.

## Wired 378-second transfer: accounting and bounded receive buffering

The owner supplied a successful wired transfer of 937,944,806 bytes / 5,678 files
in 378 seconds, with receiver wall 375.92 s, sender socket read 334.17 s,
receiver socket read 12.22 s, file writes 65.10 s, fsync 11.89 s / 11,884 calls,
permissions 12.85 s, verification 12.46 s, 115,152 receives, 12,891 sends and
18,745 DATA frames. Its end-to-end binary-unit average is 2.37 MiB/s.

### What this confirms, and what it does not

- Sender socket reads wait for responses. That includes receiver parsing, HMAC,
  filesystem work, flushes and scheduling before each ACK, as well as the network.
  334 seconds in that timer is not a latency measurement. The receiver spent only
  12.22 seconds inside the instrumented recv calls.
- File writes consumed 65.10 seconds, a substantial confirmed cost. Fsync consumed
  11.89 seconds; removing all flushes could not explain hundreds of seconds, and
  would compromise durability. Permissions includes its metadata fsyncs;
  verification includes its read/hash/tree work. Summing those overlapping fields
  and subtracting from wall time is not an accounting ledger.
- Existing schema-1 `frame_auth_s`, `file_hash_s` and `socket_write_s` were not in
  the supplied excerpt. Their full values are needed before assigning the gap.
  They have not been assumed zero. Previously unmeasured path opens/closes,
  allocation/path validation, file creation, final hash/close, dispatch and timer
  overhead can also contribute. CPU versus scheduler/IO stalls remains a physical
  profiling question: these counters measure elapsed time, not CPU samples.

New builds provide an **exclusive coarse phase ledger** covering the whole
receiver interval: pairing, setup, incoming frames, ENTRY handling, DATA handling,
END_FILE handling, verification/permissions, commit/receipt and metrics queries.
Their sum plus `unattributed_s` matches the snapshot wall time to microsecond
rounding. Setup includes initial BEGIN and filesystem/stat logging. Dispatch and
instrumentation are charged to the surrounding phase rather than disappearing.
A zero residual means time has an enclosing phase; it does not identify CPU work
or imply that fine sub-timers explain every instruction in that phase.

Fine elapsed timers further identify:

| Fields | Scope; overlaps the coarse phases |
| --- | --- |
| `frame_read_s` | Whole authenticated frame read, including copies and timer bookkeeping; includes BEGIN/metrics frames in their respective phases |
| `frame_socket_read_s`, `frame_receive_auth_s` | Actual recv time and receive-side HMAC, separated from reply HMAC |
| `frame_parse_s` | Envelope length/sequence parsing; ENTRY payload parsing is separate |
| `reply_s` | Complete reply assembly/HMAC/send, including ACKs and verification heartbeats |
| `entry_parse_s`, `entry_create_s` | ENTRY checks/lookup/allocation and file/directory creation/parent close |
| `parent_walk_s` | Descriptor-relative parent traversal across upload, verification and permissions |
| `file_close_s`, `hash_finalize_s` | Upload-time file close and final SHA-256 operation |
| `hash_walk_s`, `namespace_s`, `recipe_s`, `inventory_s` | Readback walk, namespace enumeration and structured validation |

`entry_acks` and `end_acks` count semantic acknowledgements. `frames`,
`entry_frames`, `end_frames`, `reply_frames`, `write_calls`, `parent_walk_calls`
and `dir_open_calls` distinguish protocol and filesystem operation counts.
`clock_calls`, `clock_probe_ns` and `clock_probe_valid` expose timer use and a
256-sample calibration of the complete timer path, including errno preservation.
`clock_overhead_estimate_s` multiplies those quantities. It is an estimate affected
by scheduling/cache state, **not an extra phase to sum or subtract as proven cost**.
The probe runs before connections; an unavailable probe is explicitly flagged.
A diagnostics-disabled comparison is provided to assess observer overhead.

### Why receives outnumber DATA frames

DATA is only one frame type. Every file also has ENTRY and END_FILE frames, plus
directory entries and control commands. The original reader performed separate
exact reads of header (13 bytes), nonempty payload and HMAC (32 bytes); TCP short
reads required further recv calls. A short stream read does not prove packet loss.

For the original schema-1 exchange, with D directory entries, the supplied DATA
and file counts imply a minimum of **84,635 + 3D** receives (including one pairing
read), even if every exact read completes in one call. The excess is short reads
or retry attempts. The shipped permission pass makes `fsyncs = 2*files + D + 7`
for internal, or `+8` for USB0. If that build and destination apply, the supplied
flush count implies D=521 (internal), giving about 86,198 minimum reads; roughly
29,000 more calls then come from partial/retried reads. This is a conditional
source consistency check, not a recovered packet trace. Schema 2 measures D from
`entry_frames - files` and reports `frame_recv_calls`, `recv_wire_bytes` and
`recv_short_calls` directly. Compare receives to **all** frames, not only DATA.

The default reader now uses a **bounded 65,581-byte heap buffer**, sufficient for
one maximum authenticated frame. recv requests available bytes into this buffer;
it does not wait for the buffer to fill. Header, payload and tag can consume the
same refill, including coalesced following frames. Each frame's sequence and full
HMAC still pass before any command is acted on or any byte written. Claimed frame
lengths never control allocation. Fragmentation, truncation, bad tags/sequences
and oversize headers are regression-tested. Memory is released on cleanup.
`rx_cache_hits` counts logical read requests that start with already buffered
bytes; it is not an exact count of avoided syscalls. Short reads in buffered mode
use a larger requested size, so their ratio is not directly comparable to the
old exact-part reader. Socket buffer defaults and 64 KiB DATA limits are unchanged.

### Bounded file pipelining and deferred alternatives

CLI `--pipeline-files` is an **opt-in experiment**. The sender queues one regular
file's ENTRY/DATA/END_FILE before draining its two original ACKs, then admits the
next entry. Directories retain their original ACK gate. At most two small replies
are outstanding: this is not an unbounded multi-file window. Receiver state,
per-frame HMAC, hash comparison, per-file fsync, source identity checks, readback,
permissions and COMMIT publication are unchanged. Cancellation still closes an
uncommitted transfer and cleans its stage; failures never authorize COMMIT.
It uses the existing wire commands and works with sequential receivers.
Sender `entry_wait_s`/`end_wait_s` help separate response-wait stages; they still
include server work and are not pure RTT timers. The GUI retains sequential
sending; all clients benefit from the receiver buffer.

No descriptor cache, worker threads, global sysctl tuning, flush removal or larger
DATA protocol limit was introduced. Repeated parent walks are now measured before
attempting a cache that would need descriptor-identity and no-symlink revalidation.
Data is already written in up-to-64-KiB calls without per-DATA ACKs. A larger
file-write batch could reduce syscall overhead, but the 65-second write timer
alone cannot establish whether cost is per call, per byte or storage scheduling.
`write_calls` and phase timings are available for that next hardware experiment.
A write pipeline would also need bounded authenticated buffers and mandatory
END_FILE flush/error handling; it is deferred until the physical profile supports
it. Working permissions and per-file durability were preserved.

### Host before/after results

2026-11-16, Linux 7.3.1, Ryzen 5 5600X, Python 3.11.16, GCC 16.2.1 `-O2`, TCP
loopback. One prepared dataset was reused and fingerprinted: large adds 32 MiB
(31 total files); small adds 5,648 x 4 KiB (5,678 total files). Source was on tmpfs.
Configurations ran sequentially without parallel test suites, five repetitions
for tmpfs receiver storage and three for SATA-backed Btrfs. Results are medians;
these short host runs are not PS5 throughput claims.

| Receiver / sender configuration | Large wall s / MiB/s | Small wall s / MiB/s | Small recv calls |
| --- | ---: | ---: | ---: |
| Original a36ccc0, tmpfs | 0.3924 / 81.57 | 2.1002 / 10.51 | 45,578 |
| New profile, exact-part reader, tmpfs | 0.3920 / 81.67 | 2.0808 / 10.61 | 45,580 |
| New default buffered reader, tmpfs | 0.3942 / 81.21 | 2.0695 / 10.67 | 17,035 |
| Buffered + opt-in file pipeline, tmpfs | 0.3931 / 81.43 | 1.7568 / 12.57 | 17,033 |
| Original a36ccc0, Btrfs | 0.5119 / 62.54 | 14.1329 / 1.56 | 45,578 |
| New profile, exact-part reader, Btrfs | 0.5107 / 62.68 | 14.0656 / 1.57 | 45,580 |
| New default buffered reader, Btrfs | 0.5101 / 62.76 | 14.0905 / 1.57 | 17,073 |
| Buffered + opt-in file pipeline, Btrfs | 0.5124 / 62.48 | 13.8175 / 1.60 | 17,056 |

Buffering cut receive calls about 63% for many small files and about 66% for bulk.
Bulk throughput did not improve on this host. The opt-in pipeline reduced the
buffered tmpfs small-file median about 15%; the disk-backed improvement was about
2% and is too small to generalize. Btrfs runs show how flush/storage costs can
dominate the same protocol. Default buffered diagnostics-off medians were 0.3851 s
large and 1.9646 s small, versus 0.3942/2.0695 with diagnostics. Observer cost and
run variance matter; those deltas do not establish PS5 timer overhead. Schema-2
queries add one post-receipt exchange; original schema-1 peers still work.

Raw repetitions, phase/sub-timings, counters and dataset fingerprints are in
[the profiling JSONL record](benchmarks/lan-profile-loopback-2026-11-16.jsonl).
No PS5 physical before/after run was performed by automation. The dominant cause
of the supplied 375.92-second receiver interval remains unconfirmed until the
extended profile (and omitted original fields) is collected on that hardware.

### Reproducing the profile comparisons (Fish)

Run from the repository with its development virtualenv installed. Choose a fresh
dataset directory; preparation refuses to replace an existing one. These host
commands compile the real C receiver and use owned temporary roots on loopback:

```fish
.venv/bin/python tools/lan_benchmark.py prepare /tmp/playden-profile-fixtures --small-count 5648
.venv/bin/python tools/lan_benchmark.py loopback /tmp/playden-profile-fixtures --repeat 5 --unbuffered-frames --output /tmp/playden-exact.jsonl
.venv/bin/python tools/lan_benchmark.py loopback /tmp/playden-profile-fixtures --repeat 5 --output /tmp/playden-buffered.jsonl
.venv/bin/python tools/lan_benchmark.py loopback /tmp/playden-profile-fixtures --repeat 5 --pipeline-files --output /tmp/playden-pipeline.jsonl
.venv/bin/python tools/lan_benchmark.py loopback /tmp/playden-profile-fixtures --repeat 5 --no-receiver-diagnostics --output /tmp/playden-no-diagnostics.jsonl
```

Use `--scratch-dir /path/to/existing/disk-directory` for a disk-backed receiver
comparison; its owned temporary directory is removed after each command. Keep
the source dataset, destination filesystem, chunk/socket settings and repeat
counts fixed, run configurations sequentially, and compare medians plus spread.
Use `a36ccc0` in a separate checkout for the original baseline. An exact-part run
on the new build isolates buffering while retaining the same expanded timers.

For **manual PS5 testing only**, follow the
[Fish build/deployment instructions](PS5_LAN_TRANSFER.md#physical-testing--fish).
For a matched reader comparison, build the same source with these flags in turn
and explicitly stop/unload the old listener before each manual redeployment:

```fish
set -gx PS5_PAYLOAD_SDK /opt/ps5-payload-sdk/ps5-payload-sdk
make -B -C ps5/receiver PS5_PAYLOAD_SDK="$PS5_PAYLOAD_SDK" DIAGNOSTICS=1 BUFFERED_READS=0
# Manually deploy and collect the exact-part baseline before the next build.
make -B -C ps5/receiver PS5_PAYLOAD_SDK="$PS5_PAYLOAD_SDK" DIAGNOSTICS=1 BUFFERED_READS=1
```

Confirm `receiver_build=v0.4-profile-1` and `receiver_buffered_reads` in the
authenticated sender metrics. In a second Fish terminal, this explicitly remote
benchmark prompts for a pairing token and publishes uniquely named test packages:

```fish
read -P 'PS5 LAN IPv4 address: ' PS5_IP
.venv/bin/python tools/lan_benchmark.py remote /tmp/playden-profile-fixtures --host "$PS5_IP" --destination internal --repeat 3 --output /tmp/playden-ps5-buffered.jsonl
.venv/bin/python tools/lan_benchmark.py remote /tmp/playden-profile-fixtures --host "$PS5_IP" --destination internal --repeat 3 --pipeline-files --output /tmp/playden-ps5-pipeline.jsonl
```

Repeat the sequential command with the exact-part receiver first and a different
output filename. Repeat on USB0 only when intentionally testing that storage.
Allow space for every fresh package; this tool never deletes published imports.
For the original real package, use `transfer-ps5 "$PACKAGE_DIR" --host "$PS5_IP"
--diagnostics` and optional `--pipeline-files`, choosing a fresh `--name` override
for each intentional duplicate benchmark. Normal GUI/CLI instance naming is
unchanged. Collect the full numeric `sender_metrics` JSON, not just selected
fields. The authenticated `receiver_phase_*` values first localise the gap; then
compare nested timers and operation counts inside the dominant phase. A valid
clock probe may round to zero on a coarse clock; that does not prove zero observer
cost. Keep cancellation, collision, permissions and Playden detection checks in
the manual acceptance pass; do not disable verification or flushes for timing.
