"""Detect loader identities without treating a loader as a Minecraft version."""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModLoader:
    name: str
    version: str = 'unknown'

    @property
    def label(self) -> str:
        return f'{self.name} {self.version}'


COMPONENT_LOADERS = {
    'net.fabricmc.fabric-loader': 'Fabric',
    'net.minecraftforge': 'Forge',
    'net.neoforged': 'NeoForge',
    'net.neoforged.neoforge': 'NeoForge',
    'org.quiltmc.quilt-loader': 'Quilt',
}
LIBRARY_LOADERS = {
    'net.fabricmc:fabric-loader': 'Fabric',
    'net.minecraftforge:forge': 'Forge',
    'net.neoforged:neoforge': 'NeoForge',
    'org.quiltmc:quilt-loader': 'Quilt',
    'org.quiltmc:quilt-loader-bootstrap': 'Quilt',
}


def profile_loaders(metadata: dict[str, Any]) -> tuple[ModLoader, ...]:
    detected: dict[str, ModLoader] = {}
    uid = metadata.get('uid')
    if isinstance(uid, str) and uid in COMPONENT_LOADERS:
        detected[COMPONENT_LOADERS[uid]] = ModLoader(COMPONENT_LOADERS[uid], str(metadata.get('version') or 'unknown'))
    libraries = metadata.get('libraries', [])
    additions = metadata.get('+libraries', [])
    if not isinstance(libraries, list) or not isinstance(additions, list):
        raise ValueError('Invalid loader library metadata')
    for library in libraries + additions:
        if isinstance(library, dict) and isinstance(library.get('name'), str):
            parts = library['name'].split(':')
            name = LIBRARY_LOADERS.get(':'.join(parts[:2]))
            if name:
                detected[name] = ModLoader(name, parts[2] if len(parts) > 2 else 'unknown')
    # Installer generations vary: modern Forge/NeoForge may identify the loader
    # via launch arguments rather than a library named after it.
    serialized = str(metadata.get('arguments', {})) + str(metadata.get('minecraftArguments', ''))
    main = str(metadata.get('mainClass', '')).lower()
    identity = str(metadata.get('id', '')).lower()
    hints = [('NeoForge', 'neoforge' in main or 'neoforge' in identity or 'fml.neoForgeVersion' in serialized),
             ('Fabric', 'fabricmc' in main or 'fabric-loader' in identity),
             ('Quilt', 'quiltmc' in main or 'quilt-loader' in identity),
             ('Forge', 'minecraftforge' in main or ('forge' in identity and 'neoforge' not in identity) or 'fml.forgeVersion' in serialized)]
    for name, matches in hints:
        if matches and name not in detected:
            detected[name] = ModLoader(name)
    if not detected and ('modlauncher' in main or 'bootstraplauncher' in main or 'launchwrapper' in main):
        detected['Unidentified loader'] = ModLoader('Unidentified loader')
    return tuple(detected.values())


def packaging_limitation(loaders: tuple[ModLoader, ...]) -> str:
    if not loaders:
        return ''
    labels = ', '.join(loader.label for loader in loaders)
    return (f'{labels} detected. Modded packaging for this loader is not implemented or verified. '
            'Its main class and Windows dependencies have not been resolved, so launch-package generation is blocked. '
            'Use '
            'import-instance in the CLI to preserve mods, custom files and worlds without a launch recipe. '
            'File import does not verify that this loader can launch on Playden.')
