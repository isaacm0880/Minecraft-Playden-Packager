"""The only client-acquisition boundary used by the build workflow."""
import shutil
import zipfile
from pathlib import Path
from typing import Any, Literal
from .downloads import Downloader, digest
from .operations import CancellationToken
from .importers import resolve_local
from .fabric import FabricInstallation, inspect as inspect_fabric
from .instance_content import InstanceContent, inspect, copy_into


def verify_client(path: Path, metadata: dict[str, Any]) -> None:
    if not path.is_file():
        raise ValueError('Local Minecraft client JAR is missing; no replacement will be downloaded')
    spec = metadata['downloads']['client']
    if spec.get('size') is not None and path.stat().st_size != spec['size']:
        raise ValueError('Client JAR size does not match the selected official version')
    if not spec.get('sha1'):
        raise ValueError('Official client metadata has no SHA-1; cannot verify the supplied client')
    if digest(path) != spec['sha1']:
        raise ValueError('Client JAR hash does not match the selected official version')
    try:
        with zipfile.ZipFile(path) as jar:
            if metadata['mainClass'].replace('.', '/') + '.class' not in jar.namelist():
                raise ValueError('Minecraft main class is missing from the client JAR')
    except zipfile.BadZipFile:
        raise ValueError('Client is not a valid JAR') from None


class ClientSource:
    """No default mode: every build must opt into an acquisition policy."""

    def __init__(self, mode: Literal['local'], version: str | None = None,
                 instance: Path | None = None, client_jar: Path | None = None,
                 adapter: str = 'auto',
                 libraries_root: Path | None = None, launcher_root: Path | None = None):
        if mode != 'local':
            raise ValueError('Only local Minecraft clients are supported. Select an installed instance or --client-jar.')
        self.mode = mode
        self.version = version
        self.instance = instance
        self.client_jar = client_jar
        self.adapter = adapter
        self.libraries_root = libraries_root
        self.launcher_root = launcher_root
        self.fabric: FabricInstallation | None = None
        self._local: Path | None = None
        self.content: InstanceContent | None = None

    def prepare(self) -> str:
        # Recheck installed files on every build, including when this object is reused.
        self._local = None
        self.content = None
        self.fabric = None
        if self.instance is not None:
            self.content = inspect(self.instance, self.version)
            if self.content.loaders:
                if len(self.content.loaders) == 1 and self.content.loaders[0].name == 'Fabric':
                    layouts = ('auto', 'prism', 'multimc') if (self.instance / 'mmc-pack.json').is_file() else ('auto', 'official')
                    if self.adapter not in layouts:
                        raise ValueError('Selected adapter does not match the Fabric installation layout')
                    try:
                        self.fabric = inspect_fabric(self.instance, self.version, self.libraries_root, self.launcher_root)
                        self.fabric.check_local_requirements()
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        raise ValueError(f'Fabric launch support (Phase 2B): {exc}') from exc
                    self._local = self.fabric.client
                    if self.client_jar is not None and self.client_jar.resolve() != self._local.resolve():
                        raise ValueError('Explicit client JAR differs from selected Fabric base client')
                    return self.fabric.minecraft
                from .compatibility import FORGE_BROKEN
                if any(loader.name == 'Forge' for loader in self.content.loaders):
                    raise ValueError(FORGE_BROKEN)
                raise ValueError('Modded launch resolution for this loader is not implemented. Use import-instance for a file-only import (Phase 2B).')
        installation = resolve_local(self.instance, self.version, self.client_jar, self.adapter)
        if installation.loaders:
            raise ValueError('Loader metadata was not resolved into a launch plan; refusing a Vanilla export')
        self._local = installation.client
        return installation.version

    def check(self, metadata: dict[str, Any]) -> None:
        if self._local is None:
            raise ValueError('Local source has not been prepared')
        verify_client(self._local, metadata)

    def acquire(self, metadata: dict[str, Any], downloader: Downloader, target: Path) -> None:
        self.check(metadata)
        assert self._local is not None
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._local, target)
        verify_client(target, metadata)

    def copy_instance(self, root: Path, game_directory: str, exclude_saves: bool = False,
                      cancellation: CancellationToken | None = None) -> None:
        if self.content is not None:
            copy_into(self.content, root, game_directory, exclude_saves, cancellation)
