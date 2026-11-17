"""Bounded, read-only local installation discovery; no Qt or network dependency."""
import configparser
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .compatibility import Compatibility, FORGE_BROKEN, assess
from .modloaders import ModLoader, profile_loaders
from .importers import MultiMCAdapter, OfficialAdapter, read_json, resolve_local, version_id


@dataclass(frozen=True)
class Installation:
    name: str
    launcher: str
    version: str
    instance: Path
    client: Path | None
    problem: str = ''
    launcher_root: Path | None = None
    libraries_root: Path | None = None
    loaders: tuple[ModLoader, ...] = ()
    packaging_problem: str = ''
    base_problem: str = ''
    needs_fabric_preparation: bool | None = None

    @property
    def loader_label(self) -> str:
        return ', '.join(loader.label for loader in self.loaders) or ('Custom profile' if self.packaging_problem else 'Vanilla')

    @property
    def build_problem(self) -> str:
        reasons = [self.problem, self.base_problem, self.packaging_problem]
        if self.version == 'Unknown' and not self.base_problem:
            reasons.append('Base Minecraft version could not be resolved. Rescan or choose another installation.')
        if any(loader.name == 'Forge' for loader in self.loaders):
            reasons.insert(0, FORGE_BROKEN)
        elif self.loaders and not (len(self.loaders) == 1 and self.loaders[0].name == 'Fabric') and not self.packaging_problem:
            reasons.append('Launch-package generation for this loader is not implemented.')
        if self.client is None or not self.client.is_file():
            if not self.problem:
                reasons.append('Local Minecraft client JAR is missing; no replacement will be downloaded.')
        return '\n'.join(reason for reason in reasons if reason)

    @property
    def compatibility(self) -> Compatibility:
        return assess(self.version, self.loaders, self.build_problem)

    @property
    def buildable(self) -> bool:
        return not self.build_problem

    @property
    def available(self) -> bool:
        return not self.problem and self.client is not None and self.client.is_file()


@dataclass(frozen=True)
class DiscoveryResult:
    installations: tuple[Installation, ...]
    warnings: tuple[str, ...] = ()


def default_roots(home: Path | None = None, platform: str | None = None,
                  environ: Mapping[str, str] | None = None) -> list[tuple[str, Path]]:
    home = home if home is not None else Path.home()
    platform = platform if platform is not None else sys.platform
    env = os.environ if environ is None else environ
    if platform == 'win32':
        roaming = Path(env.get('APPDATA') or home / 'AppData/Roaming')
        local = Path(env.get('LOCALAPPDATA') or home / 'AppData/Local')
        return [('Official launcher', roaming / '.minecraft'),
                ('Prism Launcher', roaming / 'PrismLauncher'),
                ('Prism Launcher', local / 'PrismLauncher'),
                ('MultiMC', roaming / 'MultiMC'), ('MultiMC', local / 'MultiMC'),
                # Portable launchers have no single installation directory.
                # Probe a bounded set; arbitrary locations use Add source folder.
                *[(launcher, base / folder)
                  for launcher, folder in (('Prism Launcher', 'PrismLauncher'), ('MultiMC', 'MultiMC'))
                  for base in (home, home / 'Desktop', home / 'Downloads', home / 'Documents', local / 'Programs')]]
    if platform == 'darwin':
        support = home / 'Library/Application Support'
        return [('Official launcher', support / 'minecraft'),
                ('Prism Launcher', support / 'PrismLauncher'),
                ('MultiMC', support / 'MultiMC'),
                *[(launcher, base / folder)
                  for launcher, folder in (('Prism Launcher', 'PrismLauncher'), ('MultiMC', 'MultiMC'))
                  for base in (home, home / 'Applications', home / 'Downloads', Path('/Applications'))],
                *[('MultiMC', base / suffix)
                  for base in (home, home / 'Applications', home / 'Downloads', Path('/Applications'))
                  for suffix in ('MultiMC.app/Data', 'MultiMC.app/Contents/MacOS')]]
    data = Path(env.get('XDG_DATA_HOME', str(home / '.local/share')))
    return [('Official launcher', home / '.minecraft'),
            ('Prism Launcher', data / 'PrismLauncher'),
            ('MultiMC', data / 'multimc'), ('MultiMC', data / 'MultiMC'),
            ('MultiMC', home / '.multimc'), ('MultiMC', home / 'MultiMC'),
            ('Prism Launcher', home / 'PrismLauncher'),
            ('Prism Launcher (Flatpak)', home / '.var/app/org.prismlauncher.PrismLauncher/data/PrismLauncher'),
            ('MultiMC (Flatpak)', home / '.var/app/org.multimc.MultiMC/data/multimc'),
            ('MultiMC (Flatpak)', home / '.var/app/org.multimc.MultiMC/data/MultiMC')]


def _settings(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    with path.open(encoding='utf-8-sig') as stream:
        parser.read_file(stream)
    return dict(parser['General']) if parser.has_section('General') else {}


def _canonical(path: Path) -> str:
    try:
        resolved = path.resolve()
        try:
            info = resolved.stat()
            if info.st_ino:
                # APFS/NTFS can resolve differently cased aliases to one directory.
                return f'inode:{info.st_dev}:{info.st_ino}'
        except OSError:
            pass
        return os.path.normcase(str(resolved))
    except (OSError, RuntimeError):
        return os.path.normcase(str(path.absolute()))


def _configured(root: Path, value: str, home: Path) -> Path:
    if value.startswith('~/') or value.startswith('~\\'):
        return home / value[2:]
    path = Path(value)
    return path if path.is_absolute() else root / path


def discover(extra_paths: Sequence[Path] = (), *, home: Path | None = None,
             platform: str | None = None, environ: Mapping[str, str] | None = None,
             include_defaults: bool = True) -> DiscoveryResult:
    """Inspect known roots and selected folders, never recurse through arbitrary disks.

    Only version/instance metadata and whitelisted launcher settings are read.
    Hash/format validation remains in the existing packaging engine.
    """
    home = home if home is not None else Path.home()
    roots = default_roots(home, platform, environ) if include_defaults else []
    roots += [('Custom location', p.expanduser().absolute()) for p in extra_paths]
    found: list[Installation] = []
    warnings: list[str] = []
    visited: dict[tuple[str, str], int] = {}
    visited_roots: set[str] = set()

    def children(path: Path) -> list[Path]:
        try:
            return sorted((p for p in path.iterdir() if p.is_dir()), key=lambda p: p.name.casefold())
        except OSError as exc:
            warnings.append(f'Cannot scan {path}: {exc.strerror or "permission denied"}')
            return []

    def add(instance: Path, launcher: str, libraries: Path | None = None, launcher_root: Path | None = None) -> None:
        version = 'Unknown'
        client: Path | None = None
        problem = ''
        name = instance.name
        loaders: tuple[ModLoader, ...] = ()
        packaging_problem = ''
        base_problem = ''
        try:
            if (instance / 'mmc-pack.json').is_file():
                settings_path = instance / 'instance.cfg'
                if settings_path.is_file():
                    try:
                        name = _settings(settings_path).get('name', name)
                    except (OSError, ValueError, configparser.Error):
                        warnings.append(f'Cannot read instance name: {settings_path}')
                # Preserve the version in the display even for an unsupported modded instance.
                components = read_json(instance / 'mmc-pack.json').get('components', [])
                if isinstance(components, list):
                    for component in components:
                        if isinstance(component, dict) and component.get('uid') == 'net.minecraft':
                            version = version_id(component.get('version'))
                            break
                installation = MultiMCAdapter().inspect(instance, None, libraries, launcher_root)
            else:
                metadata_path = instance / 'metadata/version.json'
                if not metadata_path.is_file():
                    metadata_path = instance / f'{instance.name}.json'
                local_metadata = read_json(metadata_path)
                version = version_id(local_metadata.get('id'))
                loaders = profile_loaders(local_metadata)
                if 'inheritsFrom' in local_metadata:
                    version = 'Unknown'
                if (instance / 'metadata/version.json').is_file():
                    installation = resolve_local(instance, version)
                else:
                    installation = OfficialAdapter().inspect(instance)
            version, client = installation.version, installation.client
            loaders = installation.loaders
            packaging_problem = installation.packaging_problem
            base_problem = installation.base_problem
            if not client.is_file():
                problem = f'Local Minecraft client JAR missing: {client}. No replacement will be downloaded.'
        except (OSError, ValueError, KeyError, TypeError) as exc:
            problem = str(exc)
        key = (_canonical(instance), version)
        library_root = libraries
        if library_root is None and (instance / 'mmc-pack.json').is_file() and instance.parent.name == 'instances':
            library_root = instance.parent.parent / 'libraries'
        preparation = None
        if len(loaders) == 1 and loaders[0].name == 'Fabric' and not (problem or packaging_problem or base_problem):
            from .fabric_remap import installation_hint
            preparation = installation_hint(instance, version, library_root, launcher_root)
        candidate = Installation(name, launcher, version, instance, client, problem, launcher_root, library_root, loaders, packaging_problem, base_problem, preparation)
        if key not in visited:
            visited[key] = len(found)
            found.append(candidate)
        else:
            previous = found[visited[key]]
            if candidate.available and (not previous.available or (previous.launcher_root is None and launcher_root is not None)):
                found[visited[key]] = candidate

    for launcher, original in roots:
        root = original
        # Accept a game directory chosen inside a Prism/MultiMC instance.
        try:
            if (platform or sys.platform) == 'darwin' and root.name.casefold() == 'multimc.app':
                if (root / 'Data').is_dir():
                    root = root / 'Data'
                elif (root / 'Contents/MacOS/instances').is_dir():
                    root = root / 'Contents/MacOS'
                launcher = 'MultiMC'
            if root.name in ('.minecraft', 'minecraft') and (root.parent / 'mmc-pack.json').is_file():
                root = root.parent
            accessible = root.is_dir()
        except OSError:
            warnings.append(f'Cannot inspect folder: {root}')
            continue
        key = _canonical(root)
        if key in visited_roots:
            continue
        visited_roots.add(key)
        if not accessible:
            if launcher == 'Custom location':
                warnings.append(f'Folder does not exist or cannot be read: {root}')
            continue
        recognized = False
        try:
            if (root / 'mmc-pack.json').is_file():
                recognized = True
                owner = root.parent.parent
                libraries = None
                for filename in ('prismlauncher.cfg', 'PrismLauncher.cfg', 'multimc.cfg', 'MultiMC.cfg'):
                    if (owner / filename).is_file():
                        libraries = _configured(owner, _settings(owner / filename).get('librariesdir', 'libraries'), home)
                        launcher = 'Prism Launcher' if 'prism' in filename.casefold() else 'MultiMC'
                        break
                add(root, launcher if launcher != 'Custom location' else 'Prism / MultiMC', libraries, owner if libraries is not None else None)
            elif (root / 'metadata/version.json').is_file():
                recognized = True
                add(root, 'Portable package', launcher_root=root)
            elif (root / f'{root.name}.json').is_file():
                recognized = True
                add(root, 'Official launcher')
            elif (root / 'versions').is_dir():
                recognized = True
                for directory in children(root / 'versions'):
                    if (directory / f'{directory.name}.json').is_file():
                        add(directory, 'Official launcher', launcher_root=root)
            else:
                settings: dict[str, str] = {}
                for filename in ('prismlauncher.cfg', 'PrismLauncher.cfg', 'multimc.cfg', 'MultiMC.cfg'):
                    if (root / filename).is_file():
                        settings = _settings(root / filename)
                        if launcher == 'Custom location':
                            launcher = 'Prism Launcher' if 'prism' in filename.casefold() else 'MultiMC'
                        break
                instances = _configured(root, settings.get('instancedir', 'instances'), home)
                libraries = _configured(root, settings.get('librariesdir', 'libraries'), home)
                if root.name == 'instances' and not (root / 'instances').is_dir():
                    instances, libraries = root, root.parent / 'libraries'
                if instances.is_dir():
                    for directory in children(instances):
                        if (directory / 'mmc-pack.json').is_file():
                            recognized = True
                            add(directory, launcher if launcher != 'Custom location' else 'Prism / MultiMC', libraries, root if root.name != 'instances' else root.parent)
            if launcher == 'Custom location' and not recognized:
                warnings.append(f'No supported installation layout found in {root}. Select a launcher data folder, instance, or version folder.')
        except (OSError, ValueError, configparser.Error) as exc:
            warnings.append(f'Cannot inspect {root}: {exc}')
    found.sort(key=lambda p: (p.launcher.casefold(), p.name.casefold(), p.version, str(p.instance)))
    return DiscoveryResult(tuple(found), tuple(warnings))
