# Future receiver shutdown — proposal only

No shutdown opcode, sender command, automatic shutdown option or new listener
lifecycle is implemented by this change. The current receiver continues to serve
connections after successful publication, with its existing idle expiry and
loader/signal-based stop behaviour. This proposal concerns stopping the receiver
payload, **not powering off or rebooting the PS5**.

## Optional automatic exit

A future receiver launch option `--exit-after-success` could default to false.
Only a successfully validated, atomically published package qualifies. Persisted
GUI preference, if later added, must be explicit and must describe stopping the
receiver. Validation failure, disconnect, cancellation, failed publication or a
lost acknowledgement must never trigger a new deployment or power operation.

After publication, mark the package committed, attempt to send the receipt with a
bounded timeout, then close that connection and exit if the option was selected.
A lost receipt cannot undo publication or keep an exit request alive forever;
the sender retains the current “inspect the import folder” uncertainty message.
Queue transfers need a distinct “exit after successful queue” negotiation or a
final explicit command, rather than exiting after the first package.

## Explicit authenticated exit

Use a versioned capability exchange/new protocol version before adding commands;
old PDLAN/1 receivers reject unknown commands. Never reinterpret a current frame
or expose an unauthenticated shutdown socket/HTTP endpoint. A proposed empty
`SHUTDOWN_RECEIVER` command is valid only after the existing mutual pairing and
HMAC sequence checks, in an idle control session (or after a COMMIT receipt).
The sender must require an explicit user action and explain that a fresh payload
start and token are needed for the next transfer. An illustrative future command
would be `receiver-stop --host ADDRESS`; it does **not** exist today.

The response acknowledges stopping; then stop accepting new connections, close
the listener, close the authenticated socket, clear session keys and the pairing
key with a non-elidable wipe, release all inventory/file handles and return
normally from the payload entry point. Avoid process-wide kill operations,
`exit` from worker threads or PS5 power APIs. Confirm SDK/ELF-loader ownership of
return/unload and resource reclamation on each target firmware before delivery.

## Active transfers and races

Keep the lifecycle owner in the listener/main thread, with explicit
`idle -> receiving -> verifying -> committing -> idle/stopping` states.
A shutdown command during receiving, verification or commit returns BUSY and does
not abort or publish anything. Cancellation remains the authenticated transfer
session's own operation; a later shutdown can run after cleanup. Commit is an
irreversible point: neither cancellation nor shutdown removes a published package.

The current listener serves one client at a time. Do not queue an authenticated
shutdown behind an active transfer and unexpectedly execute it later: a future
control implementation must check state at request acceptance, use a short
session/request lifetime, or require the sender to retry once idle. Any concurrent
control listener would need bounded connections, the same authentication and one
serialized lifecycle owner. Never introduce a second unauthenticated port.

Cleanup removes only the active session's pinned random stage. It must preserve
shared temporary roots, abandoned/unknown stages and published packages. On
shutdown during an OS/loader stop signal, normal cleanup should run when possible;
crashes and forced unload retain the documented manual recovery procedure.

Before implementation, test default keep-running, opted-in successful exit,
failed/cancelled transfers staying available, bad-token/replayed requests,
BUSY during all active phases, lost receipts, simultaneous connects, queued GUI
transfers, cleanup isolation and no-overwrite. Verify actual payload return,
listener closure and redeployment manually on PS5; host tests cannot establish
loader or firmware semantics.
