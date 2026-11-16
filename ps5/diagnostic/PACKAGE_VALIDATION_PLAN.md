# PS5 package validation milestone

The v0.2 discovery payload has been verified on physical PS5 hardware.

## Required validation

- Only enumerate packages under `/data/playden-import` and `/mnt/usb0/playden/import`.
- Distinguish unrelated directories from Minecraft package candidates.
- Parse `recipe.json` as JSON, with a 16 KiB maximum, and check the exact packager recipe contract: `recipe_version=1`, `game.store=imported`, `architecture=pe64`, `graphics=opengl`, `executable=java/bin/java.exe`, and launch arguments `@../../playden.args`.
- Check that `playden.args` exists and is nonempty and that the Java executable is a regular file.
- Emit actionable per-package errors. Do not treat file presence alone as a valid recipe.
- Reject symlinked package roots and avoid following symlinks outside a package.
- Test valid, invalid, oversized, truncated, missing and unrelated package fixtures before deploying.

## Non-negotiable storage boundary

The payload remains **read-only**. Future transfer operations may write only to:
- `/data/playden-import/<package>`
- `/mnt/usb0/playden/import/<package>`

Never copy directly to:
- `/data/playden-games` (managed internal game files)
- `/mnt/usb0/playden/games` (managed USB game files)
- `/data/playden/games` (Wine prefixes)

Playden alone imports packages and selects their managed storage destination.
