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
