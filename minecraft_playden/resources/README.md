# Official application icon

`icons/playdencreeper.png` is the original user-supplied 768×768 transparent PNG,
copied without changing its bytes. SHA-256:
`ee1033d02411db5eafd2e298f8d6b1d68c2c2093a7126690502cec8d2d499380`.

Generated PNGs and the Windows ICO contain 16, 24, 32, 48, 64, 128 and 256 px
variants. The ICO embeds those exact PNG payloads as seven 32-bit frames.
Regenerate with `python -m release.generate_icons`, then check with
`python -m release.generate_icons --check`. The original stays unchanged;
resampling keeps aspect ratio and pads with transparency when needed. There is
no cropping, recolouring or redesign. `icon-manifest.json` records all hashes.

Source, installed-wheel and frozen GUI startup use the shared branding helper.
PyInstaller includes the explicit resource files; Windows embeds the verified
multi-resolution ICO. AppDir uses the 256 px PNG for its root icon and .DirIcon,
plus standard hicolor theme entries and the desktop entry. The build audit
verifies all copies and, on Windows, the executable's actual PE icon resources.

macOS uses `playdencreeper.icns` with PNG-backed 16, 32, 64, 128, 256 and
512 px frames. Existing Windows ICO/PNG bytes remain unchanged. The 512 px image
is proportionally downsampled from the original, retaining alpha and colour space.
