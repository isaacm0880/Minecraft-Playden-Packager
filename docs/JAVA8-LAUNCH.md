# Java 8 launch adapter

Java 8 packages use the required Windows x64 Java 8 runtime. Java 9+ packages
keep their existing `java.exe @../../playden.args` recipe and argument bytes.
No runtime major is upgraded to avoid a launch problem. Forge remains
unsupported. The separate legacy Vanilla entry-point contract and physical-test
candidate are documented in [legacy launch support](LEGACY-LAUNCH.md).

## Invocation and validation

Java 8 has no Java-launcher argument-file expansion. The generated recipe still
selects `java/bin/java.exe`, with its default working directory `java/bin`, but
passes the original JVM options followed by:

```text
-cp ../../playden-java8.jar org.minecraftplayden.Java8Launch
```

The adapter JAR contains only our MIT-licensed Java 8 bootstrap, a manifest and
a length-prefixed UTF-8 launch plan. It contains no game or Fabric classes.
The manifest's `Class-Path` lists the ordered, resolved dependencies and client
as package-relative URLs. Spaces, Unicode, percent signs and hashes are URL
encoded; manifest lines are folded at 72 bytes with CRLF continuations.
The JVM's own manifest classpath mechanism loads the dependencies. Before
calling the original main method, the bootstrap restores `java.class.path` to
the complete ordered dependency list: Fabric Knot explicitly enumerates this
property. Vanilla's main or the installed Fabric Knot main runs in this same
JVM and system class loader. There is no subprocess, shell, Java agent, launcher
installation, or downloaded executable adapter.

The launch plan carries the original main class and game arguments, including
empty strings, spaces, Unicode, quotes and backslashes. JVM options stay on the
real Java command line, including heap, logging, native paths and installed
Fabric additions. `playden.args` remains an immutable, regenerated record of
the original complete launch arguments and is still used by Fabric preparation;
Java 8 does not read it at launch.

`playden.java8-command` records the exact bounded recipe command. Package
validation independently regenerates the adapter JAR, command and recipe from
the resolved metadata, verifies their SHA-256 inventory entries, checks the
runtime identity/PE target, and retains existing JAR/classpath/native checks.
Rehashing a modified adapter or command does not bypass this reconstruction.
The C receiver accepts a Java 8 command only when it matches the immutable
sidecar, uses the fixed adapter classpath/main and satisfies the quoting/length
limits. Both adapter files must appear in its verified inventory. Existing
executable, working-directory, recipe-field and controller-default restrictions
remain in place. USB and LAN copy these same bytes.

Java 8 JDK distributions store their runnable JRE under `jre/`. For these,
the packager copies/extracts that verified subtree into `java/`, along with the
original release identity. Existing JRE layouts remain supported. Local runtimes
are never modified; downloaded archives retain checksum and member-path checks.

## Playden and runtime limits

Verified against Playden develop
[`3f973f4eb215cee894efa37b08a3f5bf9df4daaa`](https://github.com/leolobato/playden-ps5/commit/3f973f4eb215cee894efa37b08a3f5bf9df4daaa):

- `src/runtime/prospero_profile.h` sets 32 argument words, 512 bytes normally,
  or 4096 with the runtime kit capability `launch-arguments-4096`.
- `SplitProsperoArguments` in `src/runtime/prospero_profile.cpp` groups double
  quotes and removes them; it has no escape syntax. All emitted JVM words are
  quoted. Literal quotes or controls inside JVM options are refused explicitly.
  Game values do not use that syntax: the JAR plan preserves them exactly.
- `src/runtime/game_settings.cpp` builds effective arguments and renders the
  profile using the kit's actual capabilities. The upstream checker exercises
  this path and verifies that every emitted word round-trips its splitter.
- The tested real Vanilla 1.16, Fabric 1.16 and Fabric 1.14.4 commands are 436
  bytes, within even the 512-byte limit. Custom JVM options may need the 4096-byte
  capability; over 4096 bytes or 32 words is refused, never truncated. Plan size
  limits are 10000 game arguments and 1 MiB per UTF-8 string.

Controller defaults remain independent of the adapter. Their layout still needs
Playden's `controller_layout` support and a kit with `input-map`. Older receiver
ELFs must be rebuilt/restarted to recognize Java 8 recipes; protocol and pairing
formats have not changed. Do not alter the package's relative launch directory.

The entry-point scope is standard `net.minecraft.client.main.Main`, the two
supported Fabric Knot client classes, and the validated official Vanilla
LaunchWrapper contract described in [legacy launch support](LEGACY-LAUNCH.md).
Vanilla 1.0 now has a validated physical-test candidate, not a confirmed PS5
rating. This does not introduce Forge support. System properties, custom JVM
flags and mods can have their own runtime constraints.

## Technical reference and provenance

[ProsperoMC](https://github.com/Ayoubec/ProsperoMC), investigated for
[issue #64](https://github.com/isaacm0880/Minecraft-Playden-Packager/issues/64),
was read only as technical reference at commit
`5cdcb996421565f7b528a5245fa31d18df3922de`:

- `launcher/src/org/prosperomc/Main.java`, launch preparation and `execute`,
  uses direct JVM invocation and a same-JVM reflective game handoff. Its own
  command-line budget is not Playden's recipe budget and was not adopted.
- `launcher/src/org/prosperomc/JavaRuntime.java` selects the requested major.
- Its launcher is GPL-3.0-or-later. None of its source was copied or translated,
  and neither it nor its artifacts are a dependency of this implementation.

The adapter is independently implemented using standard JDK facilities:
[Java 8 launcher](https://docs.oracle.com/javase/8/docs/technotes/tools/windows/java.html)
and [JAR manifest specification](https://docs.oracle.com/javase/8/docs/technotes/guides/jar/jar.html).
Fabric's source evidence is
[`Knot.java`](https://github.com/FabricMC/fabric-loader/blob/0.20.1/src/main/java/net/fabricmc/loader/impl/launch/knot/Knot.java).

The owned bootstrap source is `minecraft_playden/resources/java8/Java8Launch.java`.
Its shipped class has class-file major 52 and SHA-256
`174127a697b1b2b67ce7ed9baf907e817cb47f011103e063a181adadc66610ac`.
It was built with OpenJDK javac 21.0.13.0.3 (cross-compilation, not a game runtime):

```sh
mkdir -p /tmp/playden-java8-classes
javac --release 8 -encoding UTF-8 -g:none -d /tmp/playden-java8-classes \
  minecraft_playden/resources/java8/Java8Launch.java
sha256sum /tmp/playden-java8-classes/org/minecraftplayden/Java8Launch.class
```

The resource is bundled in source/wheel/frozen installations, is checksum-checked
before use, and is the sole exact-hash exception for owned Java bytecode in the
source/distribution audits. Unknown game JARs/classes remain refused. Ordinary
packaging needs no host compiler. Rebuilding/changing the source requires checking
its compiled digest and updating the explicit pins together.

## Verification and physical testing

Automated regressions cover the three target configurations in Prism, MultiMC
and official layouts; GUI/shared-engine and CLI packaging; retained Java 8 identity;
controller enabled/disabled; offline rebuilding; strict inventory/tamper checks;
and USB plus the actual C receiver on loopback. Owned probe code (never Minecraft)
also runs through the adapter on native Java 8 and Windows Java 8 under Wine,
checking classpath expansion, Unicode paths, arguments and JVM properties.
Set `PLAYDEN_JAVA8`, `PLAYDEN_JAVA8_WINDOWS` and optionally `PLAYDEN_JAVA8_JAVAC`
for those opt-in probes. Set `PLAYDEN_RECIPE_CHECK` to the checker built as described
in [the recipe guide](PLAYDEN-RECIPES.md) for actual upstream parser/profile checks.

Real installed client/dependency builds were generated and validated for Vanilla
1.16, Fabric 1.16 / Loader 0.20.1 and Fabric 1.14.4 / Loader 0.20.1 with Windows
Java 8. For Fabric 1.16 an isolated manifest selected the existing installed 1.16
client and loader cache; source installations were not altered. Those initial builds established package/bootstrap behavior only. The subsequent
2026-11-25 physical reports confirm Vanilla 1.16 and prepared Fabric 1.16/1.14.4
working on PS5, including gameplay, world saving/reloading and controller mappings
for the two Fabric packages. Automatic preparation, recipe detection and artwork
also work. See [current physical evidence](COMPATIBILITY.md#physical-acceptance-update--2026-11-25).

For a new configuration, the targeted physical procedure is (the above confirmed
versions do not need retesting):

1. Build each target to a new folder with its installed client/loader. Use a
   verified Windows x64 Java 8 home through `--java-runtime` or runtime acquisition;
   a Linux/macOS host JDK is not the game runtime. Required Fabric cache
   preparation runs automatically; use `--remap-java /path/to/java` to select its
   separate host JDK 17+. `--no-prepare-fabric` is for troubleshooting only.
2. Run `python -m minecraft_playden validate /path/to/package` before transfer.
   Check `metadata/runtime.json` says major 8 and `java/release` says 1.8. The
   recipe's command must match `playden.java8-command`, not `@playden.args`.
3. Copy through the verified USB workflow, or use LAN with the rebuilt receiver.
   Import the new folder in Playden and accept its recipe. The executable remains
   `java/bin/java.exe`; its default working directory is `java/bin`. For manual
   setup copy the complete command from `PLAYDEN.txt`/`playden.java8-command`.
4. Check each target's main menu, then a disposable single-player world, saving
   and reload. For Fabric, record loader/mod versions and whether preparation
   was used. Exercise the selected controller layout or controller mod.
5. Record firmware, Playden build, kit, destination and logs. Distinguish JVM
   startup, adapter/classpath, Fabric remapping, native loading and gameplay
   failures. A validated copy or successful owned probe alone proves none of
   these physical game milestones. Keep issue #20 open pending the new legacy candidate's physical acceptance
   and its other tracked investigations; the older successful Java 8 results do
   not certify Vanilla 1.0.
