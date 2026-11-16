# Receiver stat/errno compatibility investigation

## Failure and source evidence

The original preflight combined successful lookup and every non-`ENOENT` error
into the same “Package name already exists” message. That message did not prove
that either reported directory existed.

The installed SDK declares syscall 493 (`fstatat`) and
`AT_SYMLINK_NOFOLLOW = 0x200`. Its `libc.a` supplies `fstatat` from `syscalls.o`;
the previous receiver linked that implementation. The upstream
[SDK v0.45 source](https://github.com/ps5-payload-dev/sdk/blob/v0.45/libc/syscalls.c)
uses raw syscall stubs without POSIX errno translation. FreeBSD syscall failures
set carry and return a positive errno in `rax`; the stub does not turn that into
`-1` or set libc `errno`. A missing name can therefore return positive `ENOENT`
while the old code consults stale errno from another filesystem operation.

This establishes an SDK ABI mismatch and explains the observed symptom, but no
physical errno trace was available from the old payload. The console's exact
firmware/filesystem behavior still requires the manual re-run below.

## Changes and safety

- `sdk_fs.h` provides receiver-local FreeBSD syscall wrappers that read carry and
  translate errors to POSIX `-1/errno`. This also covers the direct `openat`,
  `mkdirat`, `unlinkat`, `renameat`, `dup` and `fstatfs` calls used by the receiver.
  Positive descriptors must remain valid on success; a positive errno must never
  be mistaken for an opened file descriptor or a successful mutation.
- All receiver `fstatat` sites use `stat_nofollow_at`, including identity checks,
  tree verification and private-stage cleanup. Failed calls never expose an
  uninitialized `struct stat` to those callers.
- Only successful stat means “exists”; only a recognized `ENOENT` means “absent.”
  Permissions, I/O, bad descriptors, unsupported syscalls/flags and malformed
  returns stop the transfer. They are not converted to an available destination.
- After pairing, a read-only `fstatat(root, ".", ..., AT_SYMLINK_NOFOLLOW)` probe
  must agree with `fstat(root)` on directory identity before querying the name.
- Console output records the operation, flags, raw return, CPU carry, raw libc
  errno and normalized errno with `strerror`. Host POSIX calls show `carry=-1`
  because libc does not expose that flag.
- A read-only absolute `lstat` comparison is logged for the requested name.
  It is diagnostic only. There is no absolute-path, changed-CWD, flag-dropping or
  follow-symlink fallback that could override an unexpected `fstatat` error.
- Exclusive directory reservation, `O_EXCL`, `O_NOFOLLOW`, pinned descriptors and
  staged integrity checks remain in place. Existing directories, ordinary files,
  FIFOs and symlinks—including dangling symlinks—are refused.

The installed SDK build and POSIX host tests can verify compilation, error
translation and no-follow behavior on the host. They cannot certify PS5 firmware
support for syscall 493 or its flags. The on-console read-only probe and lookup
diagnostics distinguish that remaining compatibility question from a real name
collision. No automatic fallback is safe solely because another lookup reports
`ENOENT` while the descriptor-relative check failed unexpectedly.

## Manual redeployment and capture

First stop/unload the previous receiver through the loader. Do not delete the
reported package names or relax permissions merely because of the old message.
The receiver's port is 9025; the ELF loader's usual port is 9021.

From the repository root, using an installed SDK and a compatible running loader:

```bash
export PS5_PAYLOAD_SDK=/path/to/ps5-payload-sdk
make -C ps5/receiver clean
make -C ps5/receiver
sha256sum ps5/receiver/minecraft_playden_receiver.elf
read -r -p 'PS5 LAN IPv4 address: ' PS5_IP
"$PS5_PAYLOAD_SDK/bin/prospero-deploy" -h "$PS5_IP" -p 9021 \
  ps5/receiver/minecraft_playden_receiver.elf | tee /tmp/playden-receiver-stat.log
```

The new console output must include:

```text
receiver_build=v0.4-import-access-1 stat_backend=freebsd-carry
```

In a second terminal, run the sender with a fresh name and the new pairing token:

```bash
read -r -p 'PS5 LAN IPv4 address: ' PS5_IP
read -r -p 'Existing built package directory: ' PACKAGE_DIR
.venv/bin/python -m minecraft_playden transfer-ps5 "$PACKAGE_DIR" \
  --host "$PS5_IP" --destination internal --name LAN-Stat-Probe
```

For a genuinely missing name on a compatible PS5, expect the target's stat line
to show `raw_result=2 carry=1 raw_errno=0 errno=2 (No such file or directory)`.
The pinned-directory dot probe should show success (`raw_result=0 carry=0`).
These are expected values, not measurements from the physical console.

If the lookup still fails, capture the `receiver_build`, `receiver_stat` and
`receiver_error` lines and the sender's full error. For example:

```bash
rg '^receiver_(build|stat|error)' /tmp/playden-receiver-stat.log
```

The startup line contains a pairing token; do not share that line or an
unfiltered log. `EINVAL` or `ENOSYS` from the dot probe indicates a syscall/flag
compatibility problem; an actual target lookup error now includes its own errno
instead of claiming a collision. No console deployment is run automatically.
