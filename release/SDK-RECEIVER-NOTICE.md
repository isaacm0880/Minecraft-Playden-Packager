# PS5 receiver SDK build notice

The bundled `minecraft_playden_receiver.elf` is built using
[ps5-payload-dev/sdk](https://github.com/ps5-payload-dev/sdk), release `v0.45`.
The exact SDK archive SHA-256 and receiver source revision are recorded in the
adjacent `receiver-manifest.json` and embedded `build-metadata.json`.

The SDK repository states that its files are GPLv3-or-later except where a file
states otherwise, including BSD-licensed FreeBSD headers. Its payload startup
and runtime code may be linked into the receiver ELF. The accompanying
`SDK-GPL-3.txt` contains GPLv3 text. Receiver application source is in
`ps5/receiver/` in the public source revision recorded by the manifest; the
SDK's corresponding source is at the pinned upstream `v0.45` tag.

The SDK is a build dependency. It is not bundled as an SDK and is not needed
on the user's desktop for direct deployment through an already running PS5
ELF loader. Final distribution still requires confirming which SDK components
were linked and how the corresponding source will be supplied.
