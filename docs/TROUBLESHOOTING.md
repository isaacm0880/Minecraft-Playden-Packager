# Troubleshooting

- **No installations:** finish installation in Minecraft Launcher, Prism or MultiMC,
  then Add source folder or Rescan. Select the instance/data folder, not a launcher executable.
- **Build disabled:** read the named blocked rows. Every selected instance must be
  eligible. Forge is Broken; NeoForge/Quilt launch packaging is not implemented.
- **Missing/mismatched client or Fabric dependency:** complete installation in the
  original launcher and rescan. The packager will not sign in or fetch a missing client.
- **Existing output/overlap:** choose a new output and keep cache/source/output separate.
- **Downloads fail:** check network and permissions. Public assets, metadata,
  dependencies and Windows Java can require downloads even though clients are local.
- **Low USB storage:** free space and Check again. Retry still validates packages,
  drive identity, capacity and destination safety. FAT32 cannot hold files of 4 GiB or larger.
- **Unplugged drive/partial copy:** reconnect the intended volume and retry failed
  copies. Verified staging may be reused; existing unrelated destination data is retained.
- **Only some outputs copied:** inspect every USB result and skip reason. Retry only
  incomplete items; fresh Copy plans all available latest-queue successes with new suffixes.
- **Cleanup not offered:** verification must be enabled and complete. Only unchanged
  outputs generated in this session qualify. Old/modified packages are retained.
- **Game fails on PS5 after validation:** validation proves package structure and
  integrity, not compatibility. Check the specific game/loader/mod/runtime combination.
- **GUI startup/platform issue:** use the matching architecture and supported desktop.
  Source users should use a virtual environment and the declared GUI dependency;
  do not install the excluded PySide6 version or disable OS security globally.

Report through [public issues](https://github.com/isaacm0880/Minecraft-Playden-Packager/issues/new/choose).
Include app/OS versions, launcher, game/loader versions, reproduction steps and the
exact error. Copy progress/error log is available. Review personal paths first;
never attach account files, credentials, game clients, worlds or populated packages.
