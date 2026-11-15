# Credits and third-party notices

Minecraft Playden Packager code is MIT licensed; see [LICENSE](LICENSE).
Application artwork was supplied for this project. Original/derived icon hashes
and generation details are retained in `minecraft_playden/resources/`.

| Component/project | Role and notices |
| --- | --- |
| [Playden](https://github.com/leolobato/playden-ps5) | Target runtime; installed separately, not bundled |
| [Python](https://www.python.org/) | Application runtime; Python licence included in frozen builds |
| [Qt for Python / PySide6](https://doc.qt.io/qtforpython-6/licenses.html) and Shiboken | Desktop bindings/shared libraries; LGPL option and bundled component notices |
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
