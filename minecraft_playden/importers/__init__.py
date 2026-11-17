"""Read-only launcher adapters. Never inspect launcher account databases."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import json
from .. import profiles
from ..modloaders import ModLoader, COMPONENT_LOADERS, profile_loaders, packaging_limitation


@dataclass(frozen=True)
class LocalInstallation:
    version: str
    client: Path
    adapter: str
    loaders: tuple[ModLoader, ...] = ()
    packaging_problem: str = ''
    base_problem: str = ''


class ImportAdapter(Protocol):
    name: str

    def detect(self, instance: Path) -> bool: ...

    def resolve(self, instance: Path, version: str | None) -> LocalInstallation: ...


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('Expected an object')
        return data
    except (OSError, ValueError):
        raise ValueError(f'Cannot read installation metadata: {path.name}') from None


def version_id(value: object) -> str:
    # Version identifiers are used only to identify Mojang metadata and safe filenames.
    if not isinstance(value, str) or not value or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-' for c in value) or value in ('.', '..'):
        raise ValueError('Invalid or missing Minecraft version in local installation')
    return value


def require_client(path: Path) -> Path:
    if not path.is_file():
        raise ValueError(f'Local Minecraft client JAR missing: {path}. No replacement will be downloaded.')
    return path


def select_version(detected: object, requested: str | None) -> str:
    found = version_id(detected)
    if requested is not None and requested != found:
        raise ValueError(f'Requested version {requested} does not match local version {found}')
    return found


class PortableAdapter:
    name = 'portable'

    def detect(self, instance: Path) -> bool:
        return (instance / 'metadata/version.json').is_file()

    def resolve(self, instance: Path, version: str | None) -> LocalInstallation:
        if (instance / 'metadata/fabric.json').exists():
            raise ValueError('Packaged Fabric reimport is unsupported; select the original launcher instance or use import-instance')
        metadata = read_json(instance / 'metadata/version.json')
        found = select_version(metadata.get('id'), version)
        loaders = profile_loaders(metadata)
        limitation = packaging_limitation(loaders)
        if limitation:
            raise ValueError(limitation)
        profile = profiles.DEFAULT_PROFILE
        if (instance / 'package.json').is_file():
            profile = read_json(instance / 'package.json').get('launch_profile', profile)
        client = profiles.client_path(metadata, profile)
        return LocalInstallation(found, require_client(instance / client), self.name)


class OfficialAdapter:
    name = 'official'

    def detect(self, instance: Path) -> bool:
        return (instance / 'versions').is_dir() or (instance / f'{instance.name}.json').is_file()

    def resolve(self, instance: Path, version: str | None) -> LocalInstallation:
        # Accept .minecraft, or a specific versions/<id> directory. Never read launcher_profiles.json.
        if (instance / 'versions').is_dir():
            if version is None:
                versions = [p for p in (instance / 'versions').iterdir() if p.is_dir() and (p / f'{p.name}.json').is_file()]
                if len(versions) != 1:
                    raise ValueError('Local installation has multiple or no versions; specify --version')
                directory = versions[0]
            else:
                directory = instance / 'versions' / version_id(version)
        else:
            directory = instance
        inspected = self.inspect(directory)
        if inspected.packaging_problem:
            raise ValueError(inspected.packaging_problem)
        if version is not None and version not in (directory.name, inspected.version):
            raise ValueError(f'Requested version {version} does not match local version {inspected.version}')
        require_client(inspected.client)
        return inspected

    def inspect(self, directory: Path) -> LocalInstallation:
        current = directory
        seen: set[str] = set()
        loaders: dict[str, ModLoader] = {}
        inherited = False
        for _ in range(32):
            key = str(current.resolve())
            if key in seen:
                raise ValueError('Cyclic local version inheritance; cannot resolve base Minecraft')
            seen.add(key)
            metadata = read_json(current / f'{current.name}.json')
            for loader in profile_loaders(metadata):
                if loader.name not in loaders or loaders[loader.name].version == 'unknown':
                    loaders[loader.name] = loader
            parent = metadata.get('inheritsFrom')
            if parent is None:
                found = version_id(metadata.get('id'))
                break
            inherited = True
            current = directory.parent / version_id(parent)
        else:
            raise ValueError('Local version inheritance is too deep; cannot resolve base Minecraft')
        loader_tuple = tuple(loaders.values())
        limitation = packaging_limitation(loader_tuple)
        if (inherited or metadata.get('mainClass') not in (None, 'net.minecraft.client.main.Main')) and not loader_tuple:
            limitation = 'Custom launch profile detected. Its main class, libraries and arguments are not preserved by the vanilla exporter; select a separate vanilla installation.'
        base_problem = ''
        java = metadata.get('javaVersion', {})
        if not isinstance(java, dict):
            raise ValueError('Invalid Java runtime metadata for base Minecraft')
        major = java.get('majorVersion')
        if isinstance(major, int) and major < 9:
            from ..metadata import JAVA8_BLOCKER
            base_problem = JAVA8_BLOCKER if major == 8 else f'Base Minecraft {found} requires unsupported Java {major}.'
        if loader_tuple and not inherited:
            base_problem = 'Base Minecraft version cannot be established from this standalone loader profile; an installed vanilla parent is required.'
            found = 'Unknown'
        if len(loader_tuple) == 1 and loader_tuple[0].name == 'Fabric' and inherited:
            from ..fabric import inspect as inspect_fabric
            try:
                fabric = inspect_fabric(directory)
                fabric.check_local_requirements()
                loader_tuple = (ModLoader('Fabric', fabric.loader),)
                limitation = ''
            except (OSError, ValueError, KeyError, TypeError) as exc:
                limitation = f'Modded packaging: Fabric main class/libraries cannot be resolved: {exc}'
        return LocalInstallation(found, current / f'{current.name}.jar', self.name, loader_tuple, limitation, base_problem)


class MultiMCAdapter:
    name = 'multimc'

    def detect(self, instance: Path) -> bool:
        return (instance / 'mmc-pack.json').is_file()

    def details(self, instance: Path, version: str | None) -> tuple[str, tuple[ModLoader, ...], str]:
        components = read_json(instance / 'mmc-pack.json').get('components', [])
        if not isinstance(components, list) or any(not isinstance(c, dict) for c in components):
            raise ValueError('Invalid MultiMC/Prism component list')
        if any(not isinstance(c.get('uid'), str) for c in components):
            raise ValueError('Invalid MultiMC/Prism component identifier')
        minecraft = [c for c in components if c.get('uid') == 'net.minecraft']
        if len(minecraft) != 1:
            raise ValueError('Expected one Minecraft component in mmc-pack.json')
        found = select_version(minecraft[0].get('version'), version)
        loaders = tuple(ModLoader(COMPONENT_LOADERS[c['uid']], str(c.get('version') or 'unknown'))
                        for c in components if c.get('uid') in COMPONENT_LOADERS)
        patch_paths = sorted((instance / 'patches').glob('*.json'))
        if patch_paths:
            by_name = {loader.name: loader for loader in loaders}
            for patch in patch_paths:
                for loader in profile_loaders(read_json(patch)):
                    if loader.name not in by_name or by_name[loader.name].version == 'unknown':
                        by_name[loader.name] = loader
            loaders = tuple(by_name.values())
        allowed = {'net.minecraft', 'org.lwjgl', 'org.lwjgl3', 'net.minecraft.java'} | set(COMPONENT_LOADERS)
        if any(loader.name == 'Fabric' for loader in loaders):
            allowed.add('net.fabricmc.intermediary')
        if any(loader.name == 'Quilt' for loader in loaders):
            allowed.update(('org.quiltmc.intermediary', 'net.fabricmc.intermediary'))
        unknown = [str(c.get('uid')) for c in components if c.get('uid') not in allowed]
        limitation = packaging_limitation(loaders)
        if patch_paths:
            limitation += (' ' if limitation else '') + 'Custom component patches are present; their libraries, main class and arguments cannot be preserved by the vanilla exporter.'
        if unknown:
            limitation += (' ' if limitation else '') + 'Custom components cannot be preserved: ' + ', '.join(unknown)
        return found, loaders, limitation

    def minecraft_version(self, instance: Path, version: str | None) -> str:
        return self.details(instance, version)[0]

    def inspect(self, instance: Path, version: str | None, libraries: Path | None = None, launcher_root: Path | None = None) -> LocalInstallation:
        found, loaders, limitation = self.details(instance, version)
        if libraries is None:
            if instance.parent.name != 'instances':
                raise ValueError('Detached Prism/MultiMC instance needs --client-jar and --version; shared libraries cannot be located')
            libraries = instance.parent.parent / 'libraries'
        client = libraries / 'com/mojang/minecraft' / found / f'minecraft-{found}-client.jar'
        if len(loaders) == 1 and loaders[0].name == 'Fabric':
            from ..fabric import inspect as inspect_fabric
            try:
                inspect_fabric(instance, version, libraries, launcher_root).check_local_requirements()
                limitation = ''
            except (OSError, ValueError, KeyError, TypeError) as exc:
                limitation = f'Modded packaging: Fabric main class/libraries cannot be resolved: {exc}'
        return LocalInstallation(found, client, self.name, loaders, limitation)

    def resolve(self, instance: Path, version: str | None, libraries: Path | None = None, launcher_root: Path | None = None) -> LocalInstallation:
        inspected = self.inspect(instance, version, libraries, launcher_root)
        if inspected.packaging_problem:
            raise ValueError(inspected.packaging_problem)
        require_client(inspected.client)
        return inspected


# Explicit registry permits new adapters without coupling them to the exporter.
ADAPTERS: dict[str, ImportAdapter] = {
    'portable': PortableAdapter(), 'official': OfficialAdapter(),
    'multimc': MultiMCAdapter(), 'prism': MultiMCAdapter(),
}


def resolve_local(instance: Path | None, version: str | None, client_jar: Path | None = None,
                  adapter: str = 'auto') -> LocalInstallation:
    if client_jar is not None:
        if version is None:
            raise ValueError('--client-jar requires --version')
        return LocalInstallation(version_id(version), require_client(client_jar), 'jar')
    if instance is None or not instance.is_dir():
        raise ValueError('Local mode requires an existing --instance directory or --client-jar with --version')
    if adapter != 'auto':
        if adapter not in ADAPTERS:
            raise ValueError(f'Unknown local adapter: {adapter}')
        return ADAPTERS[adapter].resolve(instance, version)
    for name in ('portable', 'multimc', 'official'):
        selected = ADAPTERS[name]
        if selected.detect(instance):
            return selected.resolve(instance, version)
    raise ValueError('Unrecognized local layout; supply --client-jar and --version explicitly')
