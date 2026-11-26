# Receiver shutdown — implemented in management v2

The explicit authenticated shutdown described by the earlier proposal is now
implemented. See [Receiver Manager](PS5_RECEIVER_MANAGER.md) and the
[wire contract](../ps5/receiver/PROTOCOL.md).

Shutdown requires the exact receiver session's high-entropy control credential,
an authenticated idle status and its two-second, per-connection stop lease.
An independent bounded control listener checks busy state at acceptance and again
under the lifecycle lock when stopping. It refuses active transfers, prevents
new transfers once stopping, closes its own listeners, clears credentials and
returns normally. It does not call PS5 power APIs or kill unrelated payloads.

The GUI disables Stop during local transfers. Users finish or explicitly cancel
them before stopping; there is no interrupt/force-stop mode. A remote active
transfer is refused. A lost acknowledgement or unconfirmed socket closure is
Unknown, never confirmed Stopped. Closing the SDK deployment process is not used
as a remote stop mechanism.

Automatic exit-after-success remains unimplemented. Actual PS5 loader unloading
and resource reclamation after normal return require hardware acceptance; host
exit and listener-closure tests cannot establish firmware/loader behaviour.

## Lost credentials and older receivers

The Stop button explains the recovery options if this application no longer
holds the session credential. Pairing with the PS5's six-digit code allows the
same authenticated shutdown while the code remains valid and unused. After it
expires or is consumed, there is no safe unauthenticated Stop operation in an
already deployed receiver.

The upstream PS5 ELF loader runs payloads in separate processes and can look
up process names, but a name or an open port does not authenticate a receiver.
An external shutdown ELF also cannot use this receiver's lifecycle lock to
atomically exclude a transfer, verification or publication. For that reason,
the application does not bundle a process-killing ELF or attempt to terminate
an older orphaned receiver. Use the loader's documented manual stop or unload
procedure when pairing is no longer possible. A future authenticated recovery
protocol would require receiver-side support and would not apply retroactively.

Upstream references: [ELF loader](https://github.com/ps5-payload-dev/elfldr),
[loader process code](https://github.com/ps5-payload-dev/elfldr/blob/master/elfldr.c),
[PS5 SDK process API](https://github.com/ps5-payload-dev/sdk/blob/master/include/ps5/kernel.h).
