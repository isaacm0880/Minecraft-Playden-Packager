# Credits and third-party notices

Minecraft Playden Packager code is MIT licensed; see [LICENSE](LICENSE).
Application artwork was supplied for this project. Original/derived icon hashes
and generation details are retained in `minecraft_playden/resources/`. The separate
default Minecraft package icon/background and Fabric, Forge, NeoForge and Quilt
icon variants were supplied by the maintainer and
are retained unchanged in `minecraft_playden/resources/artwork/`; their sizes and
hashes are documented in [artwork metadata](docs/ARTWORK-METADATA.md).

| Component/project | Role and notices |
| --- | --- |
| [Playden](https://github.com/leolobato/playden-ps5) | Target runtime; installed separately, not bundled |
| [Python](https://www.python.org/) | Application runtime; Python licence included in frozen builds |
| [Qt for Python / PySide6](https://doc.qt.io/qtforpython-6/licenses.html) and Shiboken | Desktop bindings/shared libraries; LGPL option and bundled component notices |
| [Pillow](https://python-pillow.org/) | PNG-only artwork validation for GUI and CLI; installed Pillow/PIL licence notices retained in frozen builds |
| [PyInstaller](https://pyinstaller.org/) | Freezer; distribution retains its licence and applicable bootloader exception |
| [AppImage](https://appimage.org/) | Linux distribution tooling/runtime; pinned build tools and notices retained |
| [Fabric](https://fabricmc.net/), [Prism Launcher](https://prismlauncher.org/), MultiMC | Installed-profile metadata/resolution compatibility; metadata fixture provenance retained |
| Mojang/Microsoft Minecraft and Eclipse Adoptium Java | User-owned game/client and downloaded runtime/dependency components keep their own licences; not included in app downloads |

Frozen distributions collect installed dependency licence/copyright files, Python's
licence, Qt notices and Linux native-component notices. Qt libraries remain dynamically
linked and replaceable; see `release/licenses/Qt-NOTICE.txt` and the included LGPL/GPL
texts. The release procedure requires checking the resulting notices per platform.
No blanket licence for redistributed Minecraft packages is granted by this project.

The test suite includes a tiny synthetic client JAR with dummy bytes, public metadata
snapshots and synthetic payloads generated during tests. It contains no playable
Minecraft client. Fixture provenance is described under `tests/fixtures/`.

## PS5 receiver SHA-256

The receiver includes Alain Mosnier’s SHA-256 implementation from
[amosnier/sha-2](https://github.com/amosnier/sha-2), pinned to commit
`565f65009bdd98267361b17d50cddd7c9beb3e6c`, under the 0BSD licence.
The unmodified source and licence are in `ps5/receiver/vendor/`.
The PS5 Payload SDK v0.45 is a pinned build dependency for the bundled receiver
ELF; end users do not need it for direct loader deployment. Its upstream source
is generally GPLv3-or-later except separately licensed files, and its startup
code may be linked into the ELF. The binary candidate includes an SDK notice and
GPLv3 text. Confirm the exact linked-code and corresponding-source obligations
before distributing that ELF; the app's MIT license does not remove them.


## Receiver Manager cryptography

The PS5 receiver also includes the MIT-licensed BearSSL 0.6 constant-time i31
integer arithmetic subset, public headers and conditional-copy routine. Original
licence and pinned file/archive hashes are retained in
`ps5/receiver/vendor/bearssl/`. This supports SRP-6a and RSA-OAEP; no BearSSL TLS
stack is shipped. Two local include adapters are identified in its manifest.

GUI startup encryption uses the external `cryptography` Python package (Apache-2.0
or BSD-3-Clause); frozen dependency licence collection retains its notices and
those of its bundled crypto backend. The PS5 Payload SDK is used only by the
receiver build, not installed on the user's desktop. Synthetic SRP vector
provenance is in `tests/fixtures/pairing/README.md`.

The Linux AppImage also embeds the type-2 runtime, which contains code from
musl, libfuse, squashfuse, zstd and zlib. Review its embedded runtime licence
and source notices in the final artifact before release.
