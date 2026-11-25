# Official legacy metadata fixtures

Captured 2026-11-25 from Mojang's version manifest. These contain metadata only;
no client JAR, dependencies, game bytecode, worlds or account data are included.
Synthetic regression payloads replace download checksums in memory.

| Minecraft | Metadata URL | Metadata SHA-1 | Client SHA-1 |
| --- | --- | --- | --- |
| 1.0 | https://piston-meta.mojang.com/v1/packages/75062586b830dd5160f13f1c9130eb365e01f1b9/1.0.json | `75062586b830dd5160f13f1c9130eb365e01f1b9` | `b679fea27f2284836202e9365e13a82552092e5d` |
| 1.5.2 | https://piston-meta.mojang.com/v1/packages/924a2dcd8bdc31f8e9d36229811c298b3537bbc7/1.5.2.json | `924a2dcd8bdc31f8e9d36229811c298b3537bbc7` | `465378c9dc2f779ae1d6e8046ebc46fb53a57968` |

Both declare `net.minecraft.launchwrapper.Launch` and the `net.minecraft:launchwrapper:1.5` dependency.
Their unmodified clients use `net.minecraft.client.Minecraft`; they have no LaunchWrapper class.
The pre-1.6 asset index declares `map_to_resources: true` and the legacy command uses positional
username/session followed by `--gameDir` and `--assetsDir`. Physical success is not inferred.
