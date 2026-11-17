# PS5 import receiver

Build with `make PS5_PAYLOAD_SDK=/path/to/ps5-payload-sdk`. The result is
`minecraft_playden_receiver.elf`; building never deploys it.

See [setup, recovery and manual physical test commands](../../docs/PS5_LAN_TRANSFER.md)
and the [wire contract](PROTOCOL.md). The [GUI Receiver Manager](../../docs/PS5_RECEIVER_MANAGER.md) can deploy only on an explicit Start click and stop only an authenticated idle receiver. The payload's only write destinations
are the import roots `/data/playden-import/`, `/mnt/usb0/playden/import/` and
their separate staging roots `/data/playden-packager-temp/`,
`/mnt/usb0/playden-packager-temp/`. Existing package names
are refused. A six-digit, short-lived PAKE code is generated each session and shown in the PS5 notification. The strong transfer credential is never printed.

Host integration compiles these same sources with `HOST_TEST`. Host binaries
require `--test-root` and redirect both destinations into a temporary sandbox;
they cannot default to physical import paths. Test-only quota/fault options are
compiled out of the PS5 payload.

```bash
python -m pytest -q tests/test_network_receiver.py tests/test_network_transfer.py
PLAYDEN_RECEIVER_SANITIZE=1 python -m pytest -q tests/test_network_receiver.py tests/test_network_transfer.py
```

SHA-256 and the BearSSL constant-time integer subset are pinned and credited in `vendor/README.md`. Management and PAKE tests are in `tests/test_receiver_manager.py` and `tests/test_gui_receiver.py`.

For “already exists” errors where no destination is visible, see the
[stat/errno compatibility investigation and manual redeployment guide](STAT_COMPATIBILITY.md).
The rebuilt payload identifies itself as `v0.4-manager-2` with public data/control ports in console output.


Use `make -B DIAGNOSTICS=1` for numeric per-connection timings. Optional
`SOCKET_BUFFER=524288` requests 512 KiB before listening; the default `0` retains
OS buffers. `-B` is required when changing these build variables. Host binaries
also accept `--diagnostics` and `--socket-buffer BYTES`. See the
[benchmark investigation](../../docs/PS5_LAN_PERFORMANCE.md) before changing defaults.
