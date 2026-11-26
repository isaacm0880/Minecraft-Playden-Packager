# Next Windows/Linux binary candidate — draft

This is a preparation note, not a published release. The application version
currently remains `0.4.0b2`; choose the final public version before publication.

## Downloads when native builds pass

- Windows x64: portable ZIP containing the complete application folder.
- Linux x86_64: AppImage built against Debian 12/glibc 2.36.
- macOS: source build only. Native Mac compatibility has not been physically verified.

Both official binaries will bundle the exact-source PS5 receiver ELF, integrity
manifest, artwork, and resource files. With an already running PS5 ELF loader,
automatic receiver deployment needs no host PS5 SDK, `socat`, or `plink.exe`.
The GUI also offers **Export PS5 Receiver (.elf)** under Advanced.

Fabric preparation requires an installed host-native full JDK 17+ with
`jdk.compiler`. The packager checks normal installs and standard PrismLauncher
managed-Java folders; it does not bundle or download a host JDK. The Windows
game runtime packaged for PS5 is separate.

## Physical evidence and limits

With receiver source `b05c77d`, automatic receiver deployment, encrypted
startup authentication, internal-storage LAN transfer, package publication,
temporary-directory cleanup and authenticated shutdown passed on a physical PS5.
USB0 and cancellation have not been physically retested. They are not marked
passed and do not block preparing this candidate.

Vanilla 1.0 is partially compatible: gameplay and controller input work with
its scoped Java2D options, but the main menu flashes and audio is inaudible.
World saving appeared to work; reload was not confirmed. The menu rendering
requires upstream Playden/Wine investigation. Previously confirmed Minecraft
versions need no new gameplay test for this candidate.

Before publication, inspect native Windows/Linux build artifacts, checksums,
frozen smoke results and bundled notices. Review GPLv3-or-later obligations for
SDK-linked receiver code and the AppImage runtime's third-party notices.
