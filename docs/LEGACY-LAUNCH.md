# Legacy Vanilla entry points (#20)

Investigated 2026-11-25 from verified baseline `4584b3c` and updated after the
first physical Vanilla 1.0 result. The entry-point change addressed packaging;
the physical result is Partial. No other loaders or client rewrites were added,
and the Java 8 bootstrap class remains unchanged.

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

## Original physical-test candidate

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

The existing candidate predates the automatic Java2D properties below. Its
successful physical run used manual JVM additions; its bytes were not changed.

## Physical PS5 result and scoped Java2D workaround

The maintainer reports package generation and LAN transfer passed. Adding both
`-Dsun.java2d.d3d=false` and `-Dsun.java2d.opengl=false` resolved the white
screen. The main menu loads but flashes significantly; world creation and
gameplay work without flashing. Controller input works and saving appears to
work. Audio is inaudible. World reload was not reported. Overall status is
**Partial**, not Works. The observed workaround is now emitted automatically
only for the verified Vanilla 1.0 LaunchWrapper metadata contract; neither
other legacy releases nor modern/Fabric launch paths receive it. It appears in
both generated `playden.args` and the actual Java 8 command. Previously built
packages still need the two manual properties or a fresh build; they are not
silently modified.

The menu flashing should be handed to the Playden developer as a potential
Playden/Wine Java2D presentation issue. The useful reproduction is the exact
Vanilla 1.0 package, Java 8 command with both properties, PS5 firmware/loader,
Playden/kit revisions, and a short menu versus in-world comparison. Gameplay's
absence of flashing narrows the symptom; it does not prove which upstream
component is responsible. No broad rendering workaround or launcher change is
proposed here.

## Sound asset investigation

The original package's official `pre-1.6` asset index has
`map_to_resources: true` and 749 entries. All 749 files are present under
`instances/default/minecraft/resources/` and match their indexed size and
SHA-1. Of these, 743 are in `sound/` (16), `sound3/` (417), `newsound/` (283),
`music/` (3), `newmusic/` (9) or `streaming/` (15). The exporter downloads the
content-addressed objects and materializes the indexed paths there; validation
checks both object and mapped copies. A synthetic sound fixture now exercises
that exact layout during packaging.

This matches PrismLauncher at `d2095469`:
[asset index handling](https://github.com/PrismLauncher/PrismLauncher/blob/d2095469e22ed260cc1b2dd9121cb6e4a1069b02/launcher/minecraft/AssetsUtils.cpp):
`map_to_resources` selects the instance's `resources` folder and reconstruction
copies each indexed object there. Its [launch task](https://github.com/PrismLauncher/PrismLauncher/blob/d2095469e22ed260cc1b2dd9121cb6e4a1069b02/launcher/minecraft/launch/ReconstructAssets.cpp)
passes the instance's `resourcesDir()`, which
[resolves beneath the game root](https://github.com/PrismLauncher/PrismLauncher/blob/d2095469e22ed260cc1b2dd9121cb6e4a1069b02/launcher/minecraft/MinecraftInstance.cpp).
The packager's resource placement therefore agrees with Prism's legacy path.

The maintainer's latest logs report successful OpenAL initialisation and a
`FileNotFoundException` from the obsolete `MinecraftResources` S3 endpoint.
Targeted static inspection of the verified 1.0 client (`ng.class`) shows its
resource thread first requests that endpoint, then catches an exception and
recursively registers files already present in its local `resources/` folder.
The S3 exception alone is therefore not proof that packaged sounds are missing.
The remaining audio cause is unresolved; possible next evidence is the exact
imported PS5 `resources/` tree after Playden's folder import, audio options and
any post-fallback sound registration or Wine audio errors in the full log.
There is no basis here to fetch assets from the obsolete endpoint or change
the verified asset layout.

Previously working Minecraft configurations do not need gameplay retesting.
Perfect Vanilla 1.0 compatibility is not required for the next stable packager
release. Issue #20 remains open for its other historical investigations and
these known partial-compatibility limits.
