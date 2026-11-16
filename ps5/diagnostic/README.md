# PS5 diagnostic ELF (experimental)

This is the first **read-only** PS5-side component of Minecraft Playden Packager.
It reports process and OS details and probes a fixed list of potential storage
locations with `lstat`. A missing directory is informational, not a failure.
It does not install, transfer, delete, create, mount or change permissions.

## Build on Linux

Install the upstream [ps5-payload-sdk](https://github.com/ps5-payload-dev/sdk)
and its required Clang/LLD toolchain. Follow that SDK's installation
instructions; do not assume the host's ordinary GCC builds PS5 ELF files.

```sh
export PS5_PAYLOAD_SDK=/opt/ps5-payload-sdk
make -C ps5/diagnostic
file ps5/diagnostic/minecraft_playden_diagnostic.elf
```

## Host smoke test (not a PS5 build)

```sh
cc -std=c11 -D_DEFAULT_SOURCE -Wall -Wextra -Werror -O2 \
  ps5/diagnostic/main.c -o /tmp/minecraft-playden-diagnostic-host
/tmp/minecraft-playden-diagnostic-host
```

Expected: a version header, process/system information, path probe lines
and `diagnostic_complete=true`. Host output only verifies the diagnostic
logic; it does **not** prove SDK compatibility or PS5 execution.

## Deploy (only with permission and a compatible loader)

With an ELF loader already listening on your own PS5, set the correct
host/port for that loader:

```sh
make -C ps5/diagnostic test PS5_HOST=192.168.1.50 PS5_PORT=9021
```

Replace the example IP; never assume the default loader port is correct.
The loader must support the SDK's `PS5_DEPLOY` protocol. Capture its stdout
using your loader's logging facility. Some loaders may not expose stdout;
in that case absence of logs is not proof that the payload failed.

## Acceptance checklist

1. Host smoke test compiles and prints `diagnostic_complete=true`.
2. SDK build succeeds and `file` identifies an x86-64 ELF.
3. The payload runs on the intended PS5 loader without crashing.
4. Output reports path status; absent directories are allowed.
5. No files or permissions are changed.

This stage does **not** validate Minecraft packages or implement direct
transfer, and it has not yet been tested on physical PS5 hardware.
