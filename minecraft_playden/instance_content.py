"""Copy local instance data by default; never execute or resolve a mod loader."""
import json
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .downloads import digest, safe_path
from .importers import MultiMCAdapter, OfficialAdapter, read_json, version_id
from .modloaders import ModLoader, profile_loaders
from .operations import CancellationToken
from .progress import Progress, ProgressCallback
from .volumes import publish_without_replace

# Exact, root-relative exclusions only. A mod's config/accounts.json is data.
GAME_EXCLUSIONS = frozenset({
    'launcher_accounts.json', 'launcher_accounts_microsoft_store.json',
    'launcher_profiles.json', 'launcher_profiles_microsoft_store.json',
    'launcher.exe', 'launcher.dll', 'minecraftlauncher.exe',
    'logs', 'crash-reports', '.cache', 'webcache', 'tmp', '.tmp',
})
WRAPPER_EXCLUSIONS = frozenset({'instance.cfg', 'instance.cfg.bak', 'accounts.json',
                                'logs', 'crash-reports', '.cache', 'metacache', 'tmp', '.tmp'})


@dataclass(frozen=True)
class InstanceContent:
    root: Path
    game: Path
    version: str
    loaders: tuple[ModLoader, ...]
    metadata: tuple[Path, ...]
    wrapper: bool = False
    known: bool = True

    @property
    def loader(self) -> str:
        return ', '.join(loader.label for loader in self.loaders) or ('Vanilla' if self.known else 'Unknown')


def metadata_path(root: Path, relative: str) -> Path:
    path = safe_path(root, relative)
    actual = path.resolve(strict=True)
    for scope in (root, root / '.minecraft', root / 'minecraft'):
        scope = scope.resolve()
        if actual.is_relative_to(scope):
            name = actual.relative_to(scope)
            if name.parts and (excluded(name) or excluded(name, wrapper=True)):
                raise ValueError('Loader metadata references excluded launcher data')
    return path


def inspect(instance: Path, version: str | None = None) -> InstanceContent:
    root = instance.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Instance must be a directory')
    if (root / 'instances').is_dir() and any((root / name).is_file() for name in ('prismlauncher.cfg', 'multimc.cfg')):
        raise ValueError('Select a Minecraft instance, not the launcher application directory')
    if (root / 'mmc-pack.json').is_file():
        metadata_path(root, 'mmc-pack.json')
        safe_path(root, 'patches')
        for patch in (root / 'patches').glob('*.json'):
            metadata_path(root, patch.relative_to(root).as_posix())
        found, loaders, limitation = MultiMCAdapter().details(root, version)
        games = [root / name for name in ('.minecraft', 'minecraft') if (root / name).is_dir()]
        if len(games) > 1:
            raise ValueError('Prism/MultiMC instance has ambiguous .minecraft and minecraft directories')
        metadata: tuple[Path, ...] = (root / 'mmc-pack.json',)
        if (root / 'patches').exists():
            metadata += (root / 'patches',)
        result = InstanceContent(root, games[0] if games else root / '.minecraft', found, loaders, metadata, wrapper=True, known=not limitation)
    elif (root / 'metadata/version.json').is_file():
        document = read_json(metadata_path(root, 'metadata/version.json'))
        found = version_id(document.get('id'))
        if version is not None and version != found:
            raise ValueError('Requested version does not match instance metadata')
        from .profiles import game_directory
        package = read_json(metadata_path(root, 'package.json')) if (root / 'package.json').is_file() else {}
        game = root / game_directory(package.get('launch_profile', 'official'))
        result = InstanceContent(root, game, found, profile_loaders(document), (root / 'metadata',))
    else:
        game = root.parent.parent if root.parent.name.casefold() == 'versions' else root
        versions = game / 'versions'
        directory: Path | None
        if root.parent.name.casefold() == 'versions':
            directory = root
        elif version is not None:
            directory = versions / version_id(version)
        else:
            choices = [p for p in versions.iterdir() if p.is_dir() and (p / f'{p.name}.json').is_file()] if versions.is_dir() else []
            if len(choices) > 1:
                raise ValueError('Instance has multiple versions; specify --version')
            directory = choices[0] if choices else None
        if directory is not None and (directory / f'{directory.name}.json').is_file():
            for metadata_file in versions.glob('*/*.json'):
                metadata_path(game, metadata_file.relative_to(game).as_posix())
            inspected = OfficialAdapter().inspect(directory)
            result = InstanceContent(game, game, inspected.version, inspected.loaders, (directory,))
        else:
            result = InstanceContent(game, game, version_id(version) if version else 'Unknown', (), (), known=False)
    if not result.game.resolve().is_relative_to(result.root) or (not result.game.is_dir() and (result.game.exists() or result.game.is_symlink() or not result.wrapper)):
        raise ValueError('Minecraft game directory is missing or escapes the instance')
    return result


def excluded(relative: Path, *, wrapper: bool = False, exclude_saves: bool = False) -> bool:
    # Only the top-level component has special meaning. Never filter recursively
    # by substrings such as token/account, extension, or an unfamiliar mod name.
    first = relative.parts[0].casefold()
    names = WRAPPER_EXCLUSIONS if wrapper else GAME_EXCLUSIONS
    return first in names or (not wrapper and exclude_saves and first == 'saves') or (
        not wrapper and re.fullmatch(r'launcher_log\.txt(?:\.[0-9]+)?', first) is not None)


def copy_tree(source: Path, destination: Path, boundary: Path,
              skip: Callable[[Path], bool] = lambda path: False,
              cancellation: CancellationToken | None = None,
              forbidden: Callable[[Path], bool] = lambda path: False) -> None:
    """Dereference contained links; fail on escapes, cycles and special files."""
    boundary = boundary.resolve()
    def visit(path: Path, target: Path, relative: Path, parents: frozenset[Path]) -> None:
        if cancellation is not None:
            cancellation.check()
        if relative.parts and skip(relative):
            return
        try:
            actual = path.resolve(strict=True)
        except RuntimeError as exc:  # Python 3.11 reports symlink loops this way.
            raise ValueError(f'Cyclic instance path: {relative}') from exc
        if not actual.is_relative_to(boundary):
            raise ValueError(f'Instance path escapes its root: {relative}')
        if any('\\' in part or ':' in part or part in ('.', '..') for part in relative.parts):
            raise ValueError(f'Instance path cannot be relocated safely: {relative}')
        if forbidden(actual):
            raise ValueError(f'Instance link references an excluded file: {relative}')
        before = actual.stat()
        mode = before.st_mode
        if stat.S_ISDIR(mode):
            if actual in parents:
                raise ValueError(f'Cyclic instance directory link: {relative}')
            target.mkdir(parents=True, exist_ok=True)
            for child in sorted(path.iterdir()):
                visit(child, target / child.name, relative / child.name, parents | {actual})
        elif stat.S_ISREG(mode):
            target.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation also detects destination collisions.
            with actual.open('rb') as incoming, target.open('xb') as outgoing:
                while block := incoming.read(1024 * 1024):
                    if cancellation is not None:
                        cancellation.check()
                    outgoing.write(block)
            after = actual.stat()
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError(f'Instance changed during import: {relative}')
        else:
            raise ValueError(f'Instance contains an unsupported special file: {relative}')
    visit(source, destination, Path(), frozenset())


def copy_into(content: InstanceContent, root: Path, game_directory: str,
              exclude_saves: bool = False, cancellation: CancellationToken | None = None) -> dict:
    check_separate(content.root, root)
    game = safe_path(root, game_directory)
    def forbidden(path: Path) -> bool:
        for scope, wrapper in ((content.game, False), (content.root, content.wrapper)):
            scope = scope.resolve()
            if path.is_relative_to(scope):
                relative = path.relative_to(scope)
                if relative.parts and excluded(relative, wrapper=wrapper, exclude_saves=exclude_saves):
                    return True
        return False
    if content.game.exists():
        copy_tree(content.game, game, content.root,
                  lambda path: excluded(path, exclude_saves=exclude_saves), cancellation, forbidden)
    else:
        game.mkdir(parents=True, exist_ok=True)
    if content.wrapper:
        game_name = content.game.name
        copy_tree(content.root, root / 'instances/default/instance-files', content.root,
                  lambda path: path.parts[0] in {game_name, 'mmc-pack.json', 'patches'}
                  or excluded(path, wrapper=True), cancellation, forbidden)
    for metadata in content.metadata:
        relative = metadata.relative_to(content.root).as_posix()
        target = safe_path(root / 'metadata/local-instance', relative)
        # Version directories include installed loader outputs and parent metadata;
        # preserve all files instead of inventing a metadata extension allowlist.
        copy_tree(metadata, target, content.root, cancellation=cancellation, forbidden=forbidden)
    report = {'schema': 1, 'version': content.version, 'loader': content.loader,
              'loaders': [{'name': loader.name, 'version': loader.version} for loader in content.loaders],
              'saves_included': not exclude_saves, 'launch_status': 'unverified',
              'message': 'Files imported. Mod-loader launch resolution belongs to Phase 2B.'}
    (root / 'metadata').mkdir(parents=True, exist_ok=True)
    (root / 'metadata/instance-import.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def check_separate(source: Path, output: Path) -> None:
    source, output = source.resolve(), output.resolve()
    if source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError('Output/cache must be separate from the source instance')


def import_instance(instance: Path, output: Path, version: str | None = None,
                    exclude_saves: bool = False, *, cancellation: CancellationToken | None = None,
                    progress: ProgressCallback | None = None) -> None:
    def report(stage: str, message: str) -> None:
        if cancellation is not None:
            cancellation.check()
        if progress is not None:
            progress(Progress(stage, message))
    report('source', 'Inspecting instance for a file-only import (launch unverified)…')
    content = inspect(instance, version)
    output = output.absolute()
    check_separate(content.root, output)
    if output.exists() or output.is_symlink():
        raise ValueError('Refusing to overwrite existing output')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.playden-import-', dir=output.parent) as temporary:
        root = Path(temporary) / 'package'
        root.mkdir()
        report('instance', 'Preserving instance files' + (' without world saves…' if exclude_saves else ', including world saves…'))
        copy_into(content, root, 'instances/default/minecraft', exclude_saves, cancellation)
        (root / 'IMPORT.txt').write_text(
            'Phase 2A file import only. No launchable package has been verified.\n'
            'Mod-loader launch resolution belongs to Phase 2B.\n', encoding='utf-8')
        report('hashing', 'Hashing imported metadata…')
        files = {path.relative_to(root).as_posix(): digest(path, 'sha256', cancellation)
                 for path in root.rglob('*') if path.is_file() and not path.is_relative_to(root / 'instances')}
        (root / 'package.json').write_text(json.dumps({
            'schema': 2, 'kind': 'instance-import', 'launch_status': 'unverified',
            'version': content.version, 'files': files}, indent=2) + '\n', encoding='utf-8')
        from .validator import validate
        errors = validate(root, cancellation=cancellation, progress=progress)
        if errors:
            raise ValueError('\n'.join(errors))
        report('export', 'Publishing validated file-only import (launch unverified)…')
        publish_without_replace(root, output)
        if progress is not None:
            progress(Progress('complete', 'Instance files imported. Launchability remains unverified.', 1, 1))
