# PS5 ftpsrv feasibility — 17 November 2026

Decision: retain Playden Receiver as the verified LAN transport. An optional FTP
method is not enabled in this change. FTP upload is feasible, but end-to-end speed
and a safe, interoperable verification/finalisation path are not established.
No PS5 address, ftpsrv version or console benchmark endpoint was supplied. No
console was scanned or contacted. There are **no measured PS5 FTP speed results**.

## Source review

Reviewed primary sources:

- [ps5-payload-dev/ftpsrv commands](https://github.com/ps5-payload-dev/ftpsrv/blob/92072f2694fdd804718cebfe0cc49fe7fe4aab82/cmd.c)
  and [dispatch/server](https://github.com/ps5-payload-dev/ftpsrv/blob/92072f2694fdd804718cebfe0cc49fe7fe4aab82/srv.c).
- [Its README](https://github.com/ps5-payload-dev/ftpsrv/blob/92072f2694fdd804718cebfe0cc49fe7fe4aab82/README.md):
  FTP port 2121, common FTP clients, PS5-specific commands and build instructions.
- [Older DNNDHH ftps5 payload README](https://github.com/DNNDHH/ftps5-payload/blob/7c809a3c038d7a531eceb194db98fac6d170e7ff/README.md):
  an earlier fork with different build/entry-point conventions and KILL/MTRW.
  This is not evidence that every fork shares current protocol or filesystem behavior.

| Concern | Finding in the pinned current implementation | Integration consequence |
| --- | --- | --- |
| File/directory creation | Binary STOR and MKD; paths resolved from CWD | Explicitly create parents and select TYPE I; no archive extraction assumed |
| Hidden Fabric cache | MKD/STOR do not exclude dot names; LIST/MLSD iterate directory entries | Drive upload from the validated local inventory, including `.fabric`, never a GUI-visible-only glob |
| Large files | STOR uses `off_t`, REST parses with `atol`, SIZE reports 64-bit size | Test multi-GiB files/offsets on the exact console build; no claim from Python or host-only tests |
| Interruptions | REST/STOR supports offsets; a closed data connection is not a package-completeness proof | Fresh isolated staging, or verify each resumed prefix; verify exact sizes/hashes afterward |
| Integrity | STOR's completion response has no expected package hash; command table has no package verification or hash command | A 226 response must never become packager success |
| Publication | RNFR/RNTO delegates to rename; no inventory validation, package lock or explicit no-replace publication contract | Never use FTP rename alone to publish into Playden import storage |
| Authentication | USER replies logged-in; dispatch has no PASS/AUTH/TLS handler | Ordinary credentials cannot secure this implementation; receiver pairing does not authenticate FTP data |
| Service lifetime | Custom KILL terminates the server, not a per-package verifier | Do not kill a user's existing FTP service or use MTRW/system remount operations |

These are source findings, not physical-console behavior guarantees. Ordinary
FTP also uses a new data connection for each file, so removing receiver framing
is not enough to establish a gain on a modded instance with many small files.
The receiver already streams DATA without per-frame acknowledgements. Hashing,
readback, storage flushes and per-file overhead remain necessary work; see
[receiver performance investigation](PS5_LAN_PERFORMANCE.md).

## Required design before implementation

A future optional FTP uploader could use the Python standard library, avoiding
another desktop dependency. It must stage outside import directories using a
unique transfer identifier on the destination filesystem. The locally validated
inventory must include all hidden directories and prepared-cache metadata.

The GUI would orchestrate a short-lived verifier payload through the existing
ELF loader, with no manually managed second service. An authenticated request
must bind the expected inventory hash, destination root/name and staging ID to
that transfer. The verifier must walk only controlled staging paths without
following symlinks, enforce limits, reject missing/extra/changed files, hash all
files on PS5, validate the package, flush writes, set required import permissions,
and publish without replacing a destination. It must return an authenticated,
explicit publication receipt and shut down on success, failure or a bounded
inactivity deadline. Interrupted or uncertain publication must retain the local
package and be reported honestly. An FTP server still able to mutate staging
during verification creates a race: exclusive staging ownership or an explicit
quiescence/isolation strategy must be demonstrated before publication.

The existing receiver binds verification to its own authenticated BEGIN/upload
session; it cannot safely adopt arbitrary FTP staging today. Adding that endpoint
would change the security/protocol surface and needs its own receiver tests.
Reusing its validation primitives is preferable to replacing them or relaxing
verification. No receiver source, protocol or ELF was changed for this review.

## Measurement gate

Use the exact ftpsrv fork/version, firmware, Playden version and destination
storage. Compare identical validated packages with (1) large files, (2) many small
files, and (3) prepared Fabric caches. Record link conditions, bytes/file count,
upload-only throughput, remote verification/flush/publication times and total
wall time. Run at least three interleaved trials per method after warm-up, with
no parallel transfers. Report median and range; distinguish cache effects.

Exercise disconnections mid-file, corrupted/missing/extra files, failed verifier
launch, full storage, duplicate names, hidden paths, multi-GiB files, timeout,
cancellation and lost receipts. Verify no incomplete import becomes visible,
existing packages remain untouched, and the verifier exits. Only enable FTP if
this demonstrates a useful end-to-end improvement while preserving these checks.
