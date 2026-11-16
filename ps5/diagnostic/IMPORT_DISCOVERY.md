# Read-only PS5 import discovery

The diagnostic ELF now scans only these two **Playden import** roots:

- Internal: `/data/playden/import/<package>`
- USB: `/mnt/usb0/playden/import/<package>`

It lists immediate child directories and checks for regular files at
`recipe.json`, `playden.args`, and `java/bin/java.exe`.
`structure-present` means these three files exist, **not** that their
contents are valid, that the game will launch, or that Playden has imported it.
Missing roots and incomplete packages are reported without failing the payload.
Symlinked package directories are not traversed.

**Hard boundary:** This diagnostic never writes to Playden-managed game
locations. In particular, **internal managed storage** `/data/playden-games/<game>` and
**USB managed storage** `/mnt/usb0/playden/games/<game>` are never output
targets. Future packager copy operations must write **only** to the designated
import roots. From an import location, **Playden** performs the import and
lets the user choose internal or USB managed storage. The packager must not
perform that managed-storage copy itself.

## Test

Run from the repository root:

```sh
bash ps5/diagnostic/test_host.sh
```

This creates temporary host fixtures for complete, incomplete, USB and
symlinked packages, and checks the diagnostic output. No PS5 is needed.

For the real console, with the PS5 Payload SDK installed:

```sh
make -C ps5/diagnostic clean all
make -C ps5/diagnostic test PS5_HOST=YOUR_PS5_IP PS5_PORT=9021
```

Confirm that the reported import roots match the console's mounted paths.
The previous milestone's read-only environment diagnostics remain intact.
