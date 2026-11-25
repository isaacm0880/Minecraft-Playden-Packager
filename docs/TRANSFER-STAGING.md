# Transfer staging ownership and cleanup

Issue #22 concerns packager-owned staging, not Playden's managed-game state.
The final import folders and their packages are never cleanup targets.

| Owner | Temporary location | Lifetime and cleanup |
| --- | --- | --- |
| Shared exporter | `.playden-<random>/package` beside the requested output | A private `TemporaryDirectory`; removed after atomic publication, cancellation or failure. |
| File-only instance import | `.playden-import-<random>/package` beside the output | Same private staging/atomic-publication lifetime. |
| Fabric preparation | OS temp `playden-remap-<random>`; `.playden-remap-<random>/package` beside a prepared-copy output | Private context-managed worker/copy directories; removed when their operation exits. |
| Download cache | Exclusive random download/seed files in the configured cache | The owning fetch removes failed temporary files; verified content-addressed cache objects are deliberately retained. |
| Mounted USB copy | `playden/.packager-transfers/<content-id>-<nonce>/` | `state.json`, a private `payload/`, `.session-lock`, exclusive `.part` files and temporary checkpoint files. Interrupted payloads/checkpoints remain for validated retry. Completed sessions and an empty `.packager-transfers` parent are removed after verified publication and flushing. |
| LAN receiver, internal | `/data/playden-packager-temp/.pdlan-<random>/package` | The connection holds directory descriptors; its stage is discarded on disconnect/cancellation/failure, or removed after publication. The empty temporary parent is then removed. |
| LAN receiver, USB0 | `/mnt/usb0/playden-packager-temp/.pdlan-<random>/package` | Same ownership/cleanup; publication remains on the same filesystem under `/mnt/usb0/playden/import/`. |
| Playden | Managed-copy journal `.playden-copy/plan.json`, `.playden-files`, `.playden-install.json`, `.playden-download`, upload/restore temporary names | Playden controls their lifecycle and recovery. The packager does not remove or alter them. |

The Playden names above were inspected in upstream develop
[`c324021adda124641e7f3fa1f8252a4f46fde569`](https://github.com/leolobato/playden-ps5/tree/c324021adda124641e7f3fa1f8252a4f46fde569/src/stores/local),
especially `copy_plan.{h,cpp}`. They are not an exhaustive inventory of every
Playden runtime subsystem or a permission to clean its directories.

## Completed mounted USB copies

A matching resumable session is adopted only after acquiring its nonblocking
cross-process `.session-lock` and re-reading its checkpoint. The lock is held
through copying, publication and cleanup (`flock` on POSIX; a one-byte `msvcrt`
lock on Windows). A busy session is left untouched; another operation uses its
own private stage and atomic no-overwrite publication still permits only one
winner. A handled failure/cancellation closes the lock but retains its file and
checkpoint for retry; process exit also releases the operating-system lock.
An unsupported lock operation fails closed rather than silently sharing mutable
staging. Host tests cover a separate process owning a same-content session while
another transfer finishes, preserving the owner's checkpoint and payload.

After final publication, the completed checkpoint is written and the volume is
flushed before cleanup. Cleanup validates the selected drive, session-directory
identity and exact completed checkpoint. It unlinks only that `state.json` and
its own `.session-lock`, then uses `rmdir` on the session and `.packager-transfers`.
The completed checkpoint is removed before releasing the session lease, so no
new resumer can adopt that completed state. A nonempty directory
is retained: unknown files, other checkpoints and concurrent-session payloads
are never traversed or deleted. The parent `playden/` and `playden/import/` stay.
A failed checkpoint write removes only its own exclusively created temporary
file. If a new session is still empty, its empty directories can be removed;
otherwise retry data is preserved.

Cancellation, copy/hash failures, disconnects and post-rename flush failures
continue to retain resumable USB state. An existing matching session is validated
again on retry; the final package is never overwritten. This is intentional
recovery data, not an abandoned-folder sweep. Removing retry data automatically
would regress the verified resumable-copy behavior.

Cleanup failure after durable publication is logged as `transfer-cleanup` while
the successful transfer result remains successful. Missing mounts, replacement
session directories, changed checkpoints and permission failures leave uncertain
state in place. The GUI and CLI share this transfer engine.

## Receiver sessions and shared parents

The receiver already removed its own descriptor-bound `.pdlan-*` stage. It now
also removes an empty `playden-packager-temp` parent after that cleanup. The
parent is opened through its known base path and must match the held inode.
Only `unlinkat(..., AT_REMOVEDIR)` is used on the shared parent; it never undergoes
recursive deletion. Another transfer stage or unrelated file makes removal
refuse safely. A moved/replaced parent is retained and reported as incomplete
cleanup. A small bounded reopen retry handles another receiver removing an empty
parent between opening it and creating the next private stage.

No transfer depends on the stage once publication has moved its package into the
final import folder. Publication acknowledgement remains unchanged and precedes
cleanup. Receiver stdout logs `receiver_cleanup`, including completion, whether
the parent was removed and an error code. Cleanup errors never revoke the
already-sent publication receipt or become a new protocol failure.

A receiver process killed without running cleanup may leave an old private stage.
The next process does not scan/remove `.pdlan-*` by age or name: it cannot establish
that another process or operator no longer needs that data. Such leftovers need
explicit ownership/recovery review. Normal handled cancellation, disconnect and
failure remove the current connection's stage.

## Validation scope

Host regressions use fixture packages and the real C receiver on loopback, not
PS5 game execution. They cover internal/USB success, failed verification,
publication failure, lack of space, disconnect, simultaneous receiver sessions,
unknown data, replaced parents, cleanup-error receipts and retryable USB state.
Physical acceptance for #22 is a small transfer-and-folder-lifecycle check with
the rebuilt receiver on internal storage and USB0, plus mounted USB lock/cleanup
acceptance on the supported host platforms/filesystems; previously working Minecraft
versions do not need gameplay retesting.
