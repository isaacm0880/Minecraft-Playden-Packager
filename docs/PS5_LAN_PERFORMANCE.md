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
filtered receiver metrics separately (remote JSONL has sender timings only).

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
