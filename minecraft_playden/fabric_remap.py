"""Native Fabric cache preparation, using the installed loader's own code."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from . import fabric, profiles
from .downloads import digest, safe_path
from .operations import CancellationToken
from .process_environment import desktop_environment
from .progress import Progress, ProgressCallback

RECORD = 'metadata/fabric-remap.json'
HELPER = Path(__file__).parent / 'resources/remap/FabricRemap.java'



@dataclass(frozen=True)
class PreparationStatus:
    required: bool
    prepared: bool
    message: str


def package_status(root: Path, *, validated: bool = False,
                   cancellation: CancellationToken | None = None) -> PreparationStatus:
    inventory = json.loads((root / 'package.json').read_text(encoding='utf-8'))
    if inventory.get('loader', {}).get('name') != 'Fabric':
        return PreparationStatus(False, False, 'Fabric preparation is not applicable.')
    if not validated:
        from .validator import validate as validate_package
        errors = validate_package(root, cancellation=cancellation)
        if errors:
            raise ValueError('\n'.join(errors))
    metadata = json.loads((root / 'metadata/version.json').read_text(encoding='utf-8'))
    data = json.loads((root / 'metadata/fabric.json').read_text(encoding='utf-8'))
    from .dependencies import classpath as resolved_classpath
    classpath = resolved_classpath(fabric.derive(metadata, data))
    try:
        _, _, classes, _ = _mapping(root, classpath, metadata['id'])
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        if RECORD in inventory['files']:
            raise
        return PreparationStatus(True, False, 'Fabric preparation needs attention: mappings could not be resolved. Prepare Fabric before transfer; the preparation worker will report the missing or unsupported input.')
    if not classes:
        return PreparationStatus(False, False, 'Fabric does not require game JAR remapping.')
    if RECORD in inventory['files']:
        return PreparationStatus(True, True, 'Matching Fabric preparation cache validated; no additional preparation is necessary.')
    return PreparationStatus(True, False, 'Fabric preparation required: this unprepared package may fail during first-launch remapping on Playden.')


def installation_hint(instance: Path, version: str, libraries: Path | None = None,
                      launcher_root: Path | None = None) -> bool | None:
    """Read-only scan hint; the exporter decides from resolved/verified metadata."""
    try:
        installed = fabric.inspect(instance, version, libraries, launcher_root)
        paths = [lib.get('name', '') for doc in installed.profiles
                 for lib in doc.get('libraries', []) + doc.get('+libraries', [])]
        _, relative = fabric.coordinate(f'net.fabricmc:intermediary:{version}')
        mapping = installed.libraries / relative
        if mapping.is_file():
            _, _, classes, _ = _read_mapping(mapping, str(mapping))
            return bool(classes)
        requires = any(lib.startswith('net.fabricmc:intermediary:') for lib in paths) or any(
            req.get('uid') == fabric.INTERMEDIARY for doc in installed.profiles for req in doc.get('requires', []))
        return True if requires else None if installed.needs_metadata else False
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
        return None  # A worker will resolve missing metadata and report precise errors.


def _context(root: Path) -> tuple[dict, dict, dict, list[str]]:
    inventory = json.loads((root / 'package.json').read_text(encoding='utf-8'))
    if inventory.get('loader', {}).get('name') != 'Fabric' or inventory.get('launch_profile', 'official') != 'official':
        raise ValueError('Headless remapping requires a validated official-profile Fabric launch package')
    metadata = json.loads((root / 'metadata/version.json').read_text(encoding='utf-8'))
    data = json.loads((root / 'metadata/fabric.json').read_text(encoding='utf-8'))
    effective = fabric.derive(metadata, data)
    from .arguments import parse_argfile
    tokens = parse_argfile((root / 'playden.args').read_text(encoding='utf-8'))
    boundary = tokens.index(effective['mainClass'])
    overrides = ('development', 'runtimeMappingNamespace', 'gameMappingNamespace', 'mappingPath',
                 'gameJarPath', 'gameVersion', 'debug.deobfuscateWithClasspath')
    properties = [token[2:].split('=', 1)[0] for token in tokens[:boundary] if token.startswith('-D')]
    if any(name in {'fabric.' + key for key in overrides} or name.startswith('fabric.gameJarPath.') for name in properties):
        raise ValueError('Custom Fabric namespace/JAR/version overrides require a separate preparation adapter')
    game_args = tokens[boundary + 1:]
    game_dirs = [game_args[i + 1] for i, token in enumerate(game_args[:-1]) if token == '--gameDir']
    if (game_args and game_args[-1] == '--gameDir') or not game_dirs or any(value != '../../' + profiles.game_directory() for value in game_dirs) or any(token.startswith(('--gameDir=', '--gameVersion')) for token in game_args):
        raise ValueError('Fabric preparation requires the standard portable game directory and automatic game-version detection')
    from .dependencies import classpath
    return inventory, metadata, data, classpath(effective)


def _mapping(root: Path, classpath: list[str], version: str) -> tuple[str | None, str, dict[str, str], str]:
    _, relative = fabric.coordinate(f'net.fabricmc:intermediary:{version}')
    mapping_path = 'libraries/' + relative
    if mapping_path not in classpath:
        return None, '', {}, ''
    return _read_mapping(safe_path(root, mapping_path), mapping_path)


def _read_mapping(path: Path, mapping_path: str) -> tuple[str, str, dict[str, str], str]:
    with zipfile.ZipFile(path) as jar:
        lines = jar.read('mappings/mappings.tiny').decode('utf-8').splitlines()
        manifest = jar.read('META-INF/MANIFEST.MF').decode('utf-8') if 'META-INF/MANIFEST.MF' in jar.namelist() else ''
    header = lines[0].split('\t') if lines else []
    namespaces = header[1:] if header[:1] == ['v1'] else header[3:] if header[:2] == ['tiny', '2'] else []
    source = 'official' if 'official' in namespaces else 'clientOfficial'
    if source not in namespaces or 'intermediary' not in namespaces:
        raise ValueError('Installed intermediary mappings lack the required client source/intermediary namespaces')
    source_column, target_column = namespaces.index(source), namespaces.index('intermediary')
    classes = {}
    for line in lines[1:]:
        parts = line.split('\t')
        if parts[0] != ('CLASS' if header[0] == 'v1' else 'c'):
            continue
        names = parts[1:]
        if len(names) != len(namespaces):
            raise ValueError('Invalid intermediary class mapping')
        classes[names[source_column]] = names[target_column]
    mapping_name = re.search(r'^Mapping-Name: (.+)\r?$', manifest, re.M)
    name = mapping_name[1].rstrip('\r') if mapping_name else ''
    if name and (not re.fullmatch(r'[A-Za-z0-9_.+ -]+', name) or name in ('.', '..')):
        raise ValueError('Unsafe Fabric mapping cache name')
    return mapping_path, source, classes, name


def _inputs(root: Path, classpath: list[str], cancellation: CancellationToken | None = None) -> dict[str, str]:
    names = ['metadata/version.json', 'metadata/fabric.json', 'playden.args', *classpath]
    return {name: digest(safe_path(root, name), 'sha256', cancellation) for name in names}


def _output_path(record: dict, game: str, mapping_name: str) -> str:
    normalized = record['normalized_version']
    if not isinstance(normalized, str) or not normalized:
        raise ValueError('Invalid normalized Minecraft cache version')
    key = re.sub(r'[^\w\-. ]+', '_', f'minecraft-{normalized}-{record["loader"]}', flags=re.ASCII)
    filename = 'client-intermediary' + ('-' + mapping_name if mapping_name else '') + '.jar'
    relative = f'{game}/.fabric/remappedJars/{key}/{filename}'
    safe_path(Path('/package'), relative)
    return relative


def _verify_jar(root: Path, output: str, classes: dict[str, str], cancellation: CancellationToken | None = None) -> None:
    """Read every member/CRC and require mapped counterparts of client classes."""
    with zipfile.ZipFile(root / 'versions/client.jar') as original, zipfile.ZipFile(safe_path(root, output)) as remapped:
        before = {name[:-6] for name in original.namelist() if name.endswith('.class')}
        after = {name[:-6] for name in remapped.namelist() if name.endswith('.class')}
        expected = {classes.get(name, name) for name in before}
        changed = {classes[name] for name in before if name in classes and classes[name] != name}
        if not changed or not expected.issubset(after):
            raise ValueError('Remapped client JAR is incomplete or mappings were not applied')
        for info in remapped.infolist():
            if cancellation is not None:
                cancellation.check()
            if info.is_dir():
                continue
            with remapped.open(info) as stream:
                read = 0
                while block := stream.read(1024 * 1024):
                    read += len(block)
                    if cancellation is not None:
                        cancellation.check()
                if info.filename.endswith('.class') and read == 0:
                    raise ValueError('Remapped client JAR contains empty class entries')


def validate(root: Path, inventory: dict, cancellation: CancellationToken | None = None) -> list[str]:
    if RECORD not in inventory['files'] and not (root / RECORD).exists():
        return []  # Older and unprepared packages remain valid structural packages.
    try:
        if RECORD not in inventory['files']:
            raise ValueError('Fabric preparation record is not inventoried')
        inv, metadata, data, classpath = _context(root)
        record = json.loads((root / RECORD).read_text(encoding='utf-8'))
        if (record.get('schema') != 1 or record.get('minecraft') != metadata['id'] or record.get('loader') != data['loader']
                or record.get('inputs') != _inputs(root, classpath, cancellation)):
            raise ValueError('Fabric preparation inputs changed; rebuild and prepare a fresh package')
        mapping, source, classes, mapping_name = _mapping(root, classpath, metadata['id'])
        if record.get('mode') == 'not-required':
            if classes or record.get('outputs') != {}:
                raise ValueError('Fabric remapping was required but no cache was prepared')
            return []
        if record.get('mode') != 'loader-cache' or not classes or record.get('source_namespace') != source:
            raise ValueError('Invalid Fabric preparation mode or namespace')
        output = _output_path(record, profiles.game_directory(), mapping_name)
        path = safe_path(root, output)
        if path.with_name(path.name + '.tmp').exists():
            raise ValueError('Incomplete Fabric .tmp cache would invalidate the prepared JAR at startup')
        if (record.get('outputs') != {output: digest(path, 'sha256', cancellation)}
                or output not in inventory['files']):
            raise ValueError('Prepared Fabric cache is missing, changed or not inventoried')
        _verify_jar(root, output, classes, cancellation)
    except (OSError, ValueError, KeyError, TypeError, IndexError, zipfile.BadZipFile) as exc:
        return [f'Invalid Fabric remapping preparation: {exc}']
    return []


def _run(java: Path | None, arguments: list[str], work: Path, cancellation: CancellationToken | None,
         progress: ProgressCallback | None = None) -> None:
    from .host_java import find
    if progress is not None:
        progress(Progress('prepare-java', 'Finding and validating a host-native JDK 17+ with its compiler module...'))
    selected = find(java, Path(arguments[1]).parent.parent / 'java/bin/java.exe', cancellation)
    java = selected.executable
    if progress is not None:
        progress(Progress('remapping', f'Preparing Fabric using native Java {selected.major}: {java}'))
    env = desktop_environment()
    for key in ('JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS', 'CLASSPATH'):
        env.pop(key, None)
    with (work / 'remap.log').open('wb') as log:
        process = subprocess.Popen([str(java), '-Xmx1G', '-Djava.awt.headless=true', '--source', '11', str(HELPER), *arguments],
                                   cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        try:
            end = time.monotonic() + 600
            while process.poll() is None:
                if cancellation is not None:
                    cancellation.check()
                if time.monotonic() >= end:
                    raise ValueError('Fabric preparation exceeded its 10 minute limit')
                try:
                    process.wait(timeout=.1)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    if process.returncode:
        detail = (work / 'remap.log').read_text(encoding='utf-8', errors='replace')[-16384:]
        raise ValueError('Headless Fabric preparation failed. Use a host-native JDK 17+ with its compiler module '
                         'and source-file launcher available. No game was started.\n' + detail)


def _save_inventory(root: Path, inventory: dict, additions: list[str], cancellation: CancellationToken | None) -> None:
    for name in [*inventory['files'], *additions]:
        inventory['files'][name] = digest(safe_path(root, name), 'sha256', cancellation)
    (root / 'package.json').write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')


def prepare_staged(root: Path, java: Path | None = None, cancellation: CancellationToken | None = None,
                   progress: ProgressCallback | None = None) -> None:
    def report(stage: str, message: str) -> None:
        if cancellation is not None:
            cancellation.check()
        if progress is not None:
            progress(Progress(stage, message))
    report('prepare-check', 'Checking Fabric preparation requirements and cache...')
    from .validator import validate as validate_package
    errors = validate_package(root, cancellation=cancellation)
    if errors:
        raise ValueError('\n'.join(errors))
    inventory, metadata, data, classpath = _context(root)
    state = package_status(root, validated=True, cancellation=cancellation)
    if state.prepared:
        report('prepare-reuse', state.message)
        return
    mapping, source, classes, mapping_name = _mapping(root, classpath, metadata['id'])
    record: dict[str, Any] = {'schema': 1, 'minecraft': metadata['id'], 'loader': data['loader'],
                              'inputs': _inputs(root, classpath, cancellation), 'outputs': {}, 'mode': 'not-required'}
    additions = [RECORD]
    if classes:
        assert mapping is not None
        # Installed first-launch caches are mutable and are never trusted as inputs.
        cache = profiles.game_directory() + '/.fabric/remappedJars'
        stale = safe_path(root, cache)
        if stale.exists():
            shutil.rmtree(stale)
        inventory['files'] = {name: value for name, value in inventory['files'].items() if not name.startswith(cache + '/') and name != RECORD}
        report('prepare-java', 'Finding host-native Java for Fabric preparation...')
        with tempfile.TemporaryDirectory(prefix='playden-remap-') as temporary:
            work = Path(temporary)
            game = work / 'game'
            game.mkdir()
            paths = [str(safe_path(root, name).resolve()) for name in classpath if name.startswith('libraries/')]
            if any('\n' in name or '\r' in name for name in paths):
                raise ValueError('Remapping paths must not contain newlines')
            (work / 'classpath.txt').write_text('\n'.join(paths), encoding='utf-8')
            _run(java.expanduser().absolute() if java is not None else None, [str(work / 'classpath.txt'), str((root / 'versions/client.jar').resolve()), str(game),
                                  str(work / 'receipt.json'), str(safe_path(root, mapping).resolve()), source, metadata['id']], work, cancellation, progress)
            receipt = json.loads((work / 'receipt.json').read_text(encoding='utf-8'))
            if receipt['loader'] != data['loader'] or receipt['minecraft'] != metadata['id']:
                raise ValueError('Remapper receipt differs from selected Minecraft/Fabric versions')
            record.update(receipt, mode='loader-cache', source_namespace=source)
            output = _output_path(record, profiles.game_directory(), mapping_name)
            relative = Path(output).relative_to(profiles.game_directory()).as_posix()
            if receipt['relative_output'] != relative:
                raise ValueError('Fabric returned an unexpected cache path')
            target = safe_path(root, output)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_path(game, relative), target)
            record.pop('relative_output')
            report('prepare-validation', 'Validating prepared Fabric cache and mapped client classes...')
            try:
                _verify_jar(root, output, classes, cancellation)
            except (zipfile.BadZipFile, RuntimeError) as exc:
                raise ValueError(f'Remapped client JAR could not be verified: {exc}') from exc
            record['outputs'] = {output: digest(target, 'sha256', cancellation)}
            additions.append(output)
    report('prepare-validation', 'Validating Fabric preparation metadata and package inventory...')
    (root / RECORD).write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    _save_inventory(root, inventory, additions, cancellation)
    errors = validate_package(root, cancellation=cancellation)
    if errors:
        raise ValueError('\n'.join(errors))


def prepare_package(source: Path, output: Path, java: Path | None = None,
                    cancellation: CancellationToken | None = None, progress: ProgressCallback | None = None) -> None:
    """Publish a new prepared copy; never run code in or change the input package."""
    from .validator import validate as validate_package
    from .instance_content import check_separate, copy_tree
    from .volumes import publish_without_replace
    source, output = source.resolve(strict=True), output.absolute()
    check_separate(source, output)
    if output.exists() or output.is_symlink():
        raise ValueError('Choose a new preparation output folder; existing outputs are never overwritten')
    errors = validate_package(source, cancellation=cancellation)
    if errors:
        raise ValueError('\n'.join(errors))
    inventory, _, _, _ = _context(source)
    state = package_status(source, validated=True, cancellation=cancellation)
    if progress is not None:
        progress(Progress('prepare-copy', 'Copying package to a separate preparation staging folder...'))
    cache = profiles.game_directory() + '/.fabric/remappedJars/'
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.playden-remap-', dir=output.parent) as temporary:
        root = Path(temporary) / 'package'
        copy_tree(source, root, source, lambda p: not state.prepared and (p.as_posix() == RECORD or p.as_posix() == cache.rstrip('/') or p.as_posix().startswith(cache)), cancellation)
        if not state.prepared:
            inventory['files'] = {name: value for name, value in inventory['files'].items() if name != RECORD and not name.startswith(cache)}
        (root / 'package.json').write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')
        prepare_staged(root, java, cancellation, progress)
        if cancellation is not None:
            cancellation.check()
        if progress is not None:
            progress(Progress('export', 'Finalising the validated prepared package...'))
        publish_without_replace(root, output)
