# Legacy Vanilla entry points (#20)

Investigated 2026-11-25 from verified baseline `4584b3c`. This change addresses
Vanilla 1.0's packaging failure; it does not certify new PS5 compatibility, add
other loaders, rewrite clients or change the existing Java 8 bootstrap class.

## Confirmed cause

Mojang's official 1.0 and 1.5.2 metadata both declare
`net.minecraft.launchwrapper.Launch`, supplied by the declared
`net.minecraft:launchwrapper:1.5` library. Their client entry is
`net.minecraft.client.Minecraft`, not LaunchWrapper. The original client check
incorrectly required the metadata's outer entry point to be inside the client
JAR itself. Rejecting the authentic 1.0 client was therefore a location error,
not evidence of missing game bytecode or a reason to bypass official hashes.

Captured metadata URLs and SHA-1 identities are in
[the fixture provenance](https://github.com/isaacm0880/Minecraft-Playden-Packager/blob/feat/ps5-network-transfer/tests/fixtures/legacy/README.md). The inspected
installed 1.0 client matches Mojang's size of 2,362,837 bytes and SHA-1
`b679fea27f2284836202e9365e13a82552092e5d`; SHA-256 is
`136e3dd54454e96175badf50bee2cdebdab9e7d66fee4fd6d135f39ead99eb58`.
The official wrapper JAR is SHA-1
`5150b9c2951f0fde987ce9c33496e26add1de224`.

REA's archive inventory is retained locally as Evidence
`ev_74ffc7e582a16058670ae21eca0989793a9d03116b77e954065b761195e1386a`.
The REA MCP server was not registered in this session; the CLI inventory worked
without changing configuration or installing a disassembler. Focused JDK
`javap` inspection independently showed the client's public static
`main(String[])` and the wrapper's VanillaTweaker target. These are static
observations, not execution of Minecraft. No client/decompiled game code is
included in this repository.

## Validation and launch contract

The resolver recognizes the declared Vanilla LaunchWrapper contract rather than
a Minecraft-version allowlist or a scan for arbitrary alternative classes:

- The requested outer main stays exactly `net.minecraft.launchwrapper.Launch`.
- Exactly one applicable, hashed LaunchWrapper artifact must be declared and
  present in the resolved classpath. Its main and VanillaTweaker/injector classes
  must exist and be readable, with official artifact size/hash checks intact.
- The client must still pass its official size/SHA-1 checks and contain the
  expected `net.minecraft.client.Minecraft` target. A bounded class-file parser
  checks the exact public class identity, Java 8-compatible format and a public
  static `void main(String[])` with a method body. Duplicate/oversized/truncated
  target members are refused. The VanillaTweaker path additionally needs one
  unambiguous static `java.io.File` field for its game-directory injection.
- Package validation checks the client target and the separate outer entry point
  in their actual artifacts; rehashing a malformed package does not replace these
  independent contract checks. These checks are not a complete JVM verifier.
- The existing same-JVM Java 8 adapter now permits that validated outer main.
  Its owned source/class bytes and pinned digest remain unchanged. No reflective
  game-dir patch, replacement game launcher or third-party source was added.

Direct modern Vanilla and the supported Fabric Knot entry-point paths retain
existing behavior. No unknown class is guessed, and applet-only/obfuscated entry
points without this validated contract still fail explicitly. This is not a
promise of full support for every alpha, beta, old release or custom launcher.
Forge, NeoForge and Quilt remain outside launch support.

## Additional legacy requirements

The official positional command uses `${auth_player_name}` and `${auth_session}`.
The offline username is preserved as one argument and the session expands to
`0`; this adds no account/authentication flow. The official pre-1.6 index sets
`map_to_resources: true`, so assets are materialized beneath the game's
`resources/`. LaunchWrapper's `--assetsDir` points to that same mapped view,
including its window icons, instead of a nonexistent virtual view. The supplied
`--gameDir` lets the official VanillaTweaker inject the game's data directory.
There is no new working-directory or home-directory substitution.

These releases also depend on Windows Java 8, LWJGL 2/JInput natives, and the
wrapper's ASM-based transformation and AWT/ImageIO path. They may make obsolete
network requests or encounter legacy native/graphics/audio problems. Package and
static checks do not establish those behaviors under Playden. Do not replace the
runtime major or remove validation to conceal such failures.

## Generated physical-test candidate

A real installed, checksum-matching Vanilla 1.0 client was built to:

```text
dist/physical-vanilla-1.0-20261125/Vanilla-1.0/
```

The local package is about 201 MiB allocated. It uses the verified Windows x64
Java 8 runtime already present in the working Fabric 1.16 candidate; the source
client, runtime and existing packages were not modified. Missing public
metadata/dependencies/assets were fetched through the ordinary verified build
workflow. No client acquisition or console operation was performed.

The package passes full inventory, entry-point/dependency, native, mapped-asset,
Java 8 adapter, recipe and artwork validation. The actual upstream Playden
recipe/profile checker and PNG decoder accept it. Detailed build/validation logs,
JAR inventories and `javap` output remain local under
`dist/legacy-1.0-investigation-20261125/`, excluded from commits and releases.

For the only newly required Minecraft physical test:

1. Transfer this new 1.0 package to a fresh destination with existing USB/LAN
   validation. Use the receiver built from the corresponding source revision.
2. Import it and accept `playden/recipe.json`. Program remains `java/bin/java.exe`;
   manual Java 8 arguments must come from its `PLAYDEN.txt` or
   `playden.java8-command`, not `@../../playden.args`.
3. Check startup/menu, controller actions applicable to 1.0, a disposable world,
   sound/resources, saving and reloading. Confirm the world stays inside this
   package's `instances/default/minecraft/saves`, not another installation.
4. Retain complete Playden/JVM logs on failure; distinguish adapter, wrapper/ASM,
   legacy natives/AWT, game startup and saving. Record Playden/kit/firmware and
   storage. Do not promote 1.0 from Untested until physically confirmed.

Previously working Minecraft configurations do not need physical gameplay
retesting for this fix. Issue #20 also contains other historical investigations;
this package alone is not a reason to close it.
