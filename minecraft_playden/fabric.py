"""Resolve installed modern Fabric client profiles without executing launchers."""
import copy
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from .dependencies import allowed, coalesce, resolve as dependencies
from .downloads import Downloader, digest, safe_path
from .operations import CancellationToken
from .importers import read_json, version_id

FABRIC = 'net.fabricmc.fabric-loader'
INTERMEDIARY = 'net.fabricmc.intermediary'
MAINS = {'net.fabricmc.loader.impl.launch.knot.KnotClient', 'net.fabricmc.loader.launch.knot.KnotClient'}
SUPPORTED = {'net.minecraft', 'net.minecraft.java', 'org.lwjgl', 'org.lwjgl3', FABRIC, INTERMEDIARY}


def coordinate(name: str) -> tuple[str, str]:
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.+-]+:[A-Za-z0-9_.+-]+:[A-Za-z0-9_.+-]+(?::[A-Za-z0-9_.+-]+)?', name):
        raise ValueError(f'Invalid Fabric Maven coordinate: {name!r}')
    parts = name.split(':')
    if any(part in ('.', '..') for part in parts) or any(not part for part in parts[0].split('.')):
        raise ValueError('Unsafe Fabric Maven coordinate')
    group, artifact, version = parts[:3]
    classifier = '-' + parts[3] if len(parts) == 4 else ''
    return ':'.join(parts[:2] + parts[3:]), f'{group.replace(".", "/")}/{artifact}/{version}/{artifact}-{version}{classifier}.jar'


def library(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError('Fabric library must be an object')
    _, path = coordinate(raw.get('name', ''))
    result = copy.deepcopy(raw)
    downloads = result.get('downloads', {})
    if not isinstance(downloads, dict):
        raise ValueError('Invalid Fabric artifact downloads')
    artifact = downloads.get('artifact')
    if artifact is not None and not isinstance(artifact, dict):
        raise ValueError('Invalid Fabric artifact metadata')
    if artifact is not None and not isinstance(artifact.get('url'), str):
        raise ValueError('Invalid Fabric artifact URL')
    if artifact is None:
        repository = result.get('url', 'https://maven.fabricmc.net/')
        if not isinstance(repository, str):
            raise ValueError('Invalid Fabric repository URL')
        url = repository.rstrip('/') + '/' + path
        artifact = {'path': path, 'url': url}
        for field in ('sha1', 'size'):
            if field in result:
                artifact[field] = result[field]
        result['downloads'] = {'artifact': artifact}
    elif 'path' not in artifact:
        parsed = urlsplit(artifact.get('url', ''))
        relative = parsed.path.lstrip('/')
        # Prism encodes LWJGL/Mojang native classifiers in artifact names.
        # Keep the exact version and use the published Maven file's coordinate.
        group = result['name'].split(':')[0]
        if group in ('org.lwjgl', 'com.mojang') and parsed.hostname == 'libraries.minecraft.net':
            parts = relative.split('/')
            prefix = group.split('.')
            if len(parts) != len(prefix) + 3 or parts[:len(prefix)] != prefix:
                raise ValueError(f'Invalid Prism native artifact URL: {result["name"]}')
            artifact_name, revision, filename = parts[len(prefix):]
            suffix = filename.removeprefix(f'{artifact_name}-{revision}').removesuffix('.jar')
            if filename != f'{artifact_name}-{revision}{suffix}.jar' or suffix not in ('', '-natives-windows', '-natives-windows-x86', '-natives-windows-arm64'):
                raise ValueError(f'Unsupported Prism native artifact: {result["name"]}')
            canonical = f'{group}:{artifact_name}:{revision}' + (':' + suffix[1:] if suffix else '')
            alias = f'{group}:{artifact_name}{suffix}:{revision}'
            if result['name'] not in (canonical, alias):
                raise ValueError(f'Prism artifact URL changes its coordinate/version: {result["name"]}')
            result['name'] = canonical
            _, path = coordinate(result['name'])
        if relative != path:
            raise ValueError('Fabric artifact URL does not match its Maven path')
        artifact['path'] = path
    elif artifact.get('path') != path:
        raise ValueError('Fabric library path does not match its Maven coordinate')
    if not isinstance(artifact.get('url'), str) or not artifact['url'].startswith('https://'):
        raise ValueError('Fabric artifact URLs must use HTTPS')
    if 'sha1' in artifact and (not isinstance(artifact['sha1'], str) or not re.fullmatch('[0-9a-f]{40}', artifact['sha1'])):
        raise ValueError('Invalid Fabric artifact checksum')
    if 'size' in artifact and (type(artifact['size']) is not int or artifact['size'] <= 0):
        raise ValueError('Invalid Fabric artifact size')
    safe_path(Path('/fabric-libraries'), path)
    if result.get('natives'):
        raise ValueError('Custom Fabric native classifier maps are unsupported; use Mojang Windows natives')
    return result


def windows_library(raw: dict[str, Any]) -> bool:
    if not isinstance(raw, dict):
        raise ValueError('Fabric library must be an object')
    try:
        applies = allowed(raw)
    except (AttributeError, TypeError, KeyError, re.error) as exc:
        raise ValueError('Invalid Fabric platform rules') from exc
    if not applies:
        return False
    identity = raw.get('name', '') + str(raw.get('downloads', {}).get('artifact', {}).get('url', ''))
    if any(tag in identity for tag in ('natives-linux', 'natives-macos', 'natives-osx', 'natives-windows-arm64', 'natives-windows-x86')):
        return False
    classifier = raw.get('name', '').split(':')[3:]
    return not classifier or not any(tag in classifier[0] for tag in ('linux', 'osx', 'macos', 'arm64', 'aarch64', 'windows-x86'))


def merge_libraries(layers: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    # Descendant profiles take precedence over their parents. Identical entries
    # collapse; two versions of one artifact in the same layer are ambiguous.
    result: dict[str, dict[str, Any]] = {}
    for index in reversed(range(len(layers))):
        layer = coalesce(layers[index]) if index == 0 else layers[index]
        seen: dict[str, dict[str, Any]] = {}
        for raw in layer:
            if not windows_library(raw):
                continue
            value = library(raw) if index else copy.deepcopy(raw)
            key, _ = coordinate(value.get('name', ''))
            if key in seen and seen[key] != value:
                raise ValueError(f'Conflicting Fabric dependencies in one profile: {key}')
            seen[key] = value
            if key not in result:
                result[key] = value
    return list(result.values())


def argument_lists(doc: dict[str, Any]) -> dict[str, list[Any]]:
    from .arguments import parse_argfile
    args = doc.get('arguments', {})
    if not isinstance(args, dict) or set(args) - {'jvm', 'game', 'default-user-jvm'}:
        raise ValueError('Unsupported Fabric arguments metadata')
    result = {section: copy.deepcopy(args.get(section, []))
              for section in ('default-user-jvm', 'jvm', 'game')}
    if any(not isinstance(value, list) for value in result.values()):
        raise ValueError('Fabric argument sections must be lists')
    extras = doc.get('+jvmArgs', [])
    if not isinstance(extras, list):
        raise ValueError('Fabric +jvmArgs must be a list')
    result['jvm'] += extras
    if 'minecraftArguments' in doc:
        if not isinstance(doc['minecraftArguments'], str):
            raise ValueError('Invalid installed game argument string')
        result['game'] = parse_argfile(doc['minecraftArguments'])
    for section, values in result.items():
        for value in values:
            if isinstance(value, dict) and not windows_library({'name': 'argument:conditional:1', 'rules': value.get('rules')}):
                continue
            words = [value] if isinstance(value, str) else value.get('value', []) if isinstance(value, dict) else []
            words = [words] if isinstance(words, str) else words
            if not isinstance(words, list) or not words or any(not isinstance(word, str) for word in words):
                raise ValueError('Invalid Fabric argument value')
            for word in words:
                # Preserve portable options, never host paths, native-directory
                # replacements, agents, argument files, or another main/classpath.
                portable = re.sub(r'\$\{(?:game_directory|assets_root|library_directory|natives_directory)\}(/[A-Za-z0-9_.+/-]+)?',
                                  lambda match: 'PORTABLE' if '..' not in (match.group(1) or '').split('/') else '/unsafe', word)
                stripped = re.sub(r'\$\{[^}]+\}', 'PLACEHOLDER', portable)
                if (any(c in stripped for c in ('/', '\\', '\n', '\r', '\x00')) or (':' in stripped and not stripped.startswith('-XX:'))
                        or word.startswith(('@', '-javaagent', '-agent', '-Djava.library.path', '-Dorg.lwjgl.librarypath'))
                        or (section != 'game' and (not word.startswith('-') or word in ('-cp', '-classpath', '--class-path')))):
                    raise ValueError(f'Unsupported or nonportable Fabric {section} argument: {word}')
    game_words = []
    for value in result['game']:
        if isinstance(value, str):
            game_words.append(value)
        elif allowed(value):
            words = value['value']
            game_words.extend([words] if isinstance(words, str) else words)
    auth = {'--accessToken': '${auth_access_token}', '--uuid': '${auth_uuid}',
            '--xuid': '${auth_xuid}', '--clientId': '${clientid}'}
    for position, word in enumerate(game_words):
        if word in auth and (position + 1 >= len(game_words) or game_words[position + 1] != auth[word]):
            raise ValueError('Literal Minecraft credential/identity arguments are unsupported')
        if any(word.startswith(flag + '=') for flag in auth):
            raise ValueError('Minecraft credential arguments must retain offline placeholders')
    return result


def check_component_requirements(doc: dict[str, Any], versions: dict[str, str]) -> None:
    """Check mandatory edges without selecting versions from optional hints."""
    owner = doc.get('uid', doc.get('id', 'Fabric profile'))
    requirements = doc.get('requires', [])
    if not isinstance(requirements, list) or any(not isinstance(item, dict) for item in requirements):
        raise ValueError(f'Invalid component requirements for {owner}')
    for requirement in requirements:
        uid = requirement.get('uid')
        if not isinstance(uid, str) or not uid:
            raise ValueError(f'Invalid required component UID for {owner}')
        if 'suggests' in requirement and (not isinstance(requirement['suggests'], str) or not requirement['suggests']):
            raise ValueError(f'Invalid suggested version for component {uid} (from {owner})')
        if 'suggests' in requirement and 'equals' not in requirement:
            # A suggestion neither installs a component nor changes its pin.
            continue
        if uid not in versions:
            raise ValueError(f'Component {owner} requires missing component {uid}')
        if 'equals' in requirement:
            try:
                expected = version_id(requirement['equals'])
            except ValueError as exc:
                raise ValueError(f'Invalid required version for component {uid} (from {owner})') from exc
            if expected != versions[uid]:
                raise ValueError(f'Component {owner} requires {uid} {expected}; selected version is {versions[uid]}')


def derive(base: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    if data.get('schema') != 1 or data.get('minecraft') != base['id']:
        raise ValueError('Fabric launch plan Minecraft version mismatch')
    loader = version_id(data['loader'])
    documents = data['profiles']
    if not isinstance(documents, list) or not documents:
        raise ValueError('Missing installed Fabric profiles')
    versions = {'net.minecraft': base['id'], FABRIC: loader, INTERMEDIARY: base['id']}
    seen_components: set[str] = set()
    for doc in documents:
        if not isinstance(doc, dict):
            raise ValueError('Invalid Fabric profile object')
        uid = doc.get('uid')
        if uid is not None:
            if not isinstance(uid, str) or uid not in SUPPORTED:
                raise ValueError(f'Unsupported Fabric component: {uid!r}')
            revision = version_id(doc.get('version'))
            if uid in seen_components:
                raise ValueError(f'Duplicate Fabric component in launch plan: {uid}')
            if uid in versions and revision != versions[uid]:
                raise ValueError(f'Component {uid} version {revision} conflicts with selected version {versions[uid]}')
            seen_components.add(uid)
            versions[uid] = revision
    result = copy.deepcopy(base)
    layers = [base['libraries']]
    parent_ids = {base['id']}
    for doc in documents:
        if not isinstance(doc, dict):
            raise ValueError('Invalid Fabric profile object')
        if 'inheritsFrom' in doc and doc['inheritsFrom'] not in parent_ids:
            raise ValueError('Fabric profile inherits from a different Minecraft version')
        if 'id' in doc:
            parent_ids.add(version_id(doc['id']))
            result['launch_version'] = doc['id']
        main = doc.get('mainClass')
        if main is not None:
            if not isinstance(main, str):
                raise ValueError('Invalid Fabric main class')
            if main not in MAINS and main != base['mainClass']:
                raise ValueError('Unsupported Fabric client main class')
            result['mainClass'] = main
        if any(field in doc for field in ('tweakers', '+tweakers', 'jarMods')):
            raise ValueError('Legacy tweakers/JAR patches are unsupported')
        check_component_requirements(doc, versions)
        if 'mainJar' in doc:
            main_jar = doc['mainJar']
            if not isinstance(main_jar, dict) or main_jar.get('name') != f'com.mojang:minecraft:{base["id"]}:client':
                raise ValueError('Custom patched Minecraft client JARs are unsupported')
            cached = main_jar.get('downloads', {}).get('artifact', {})
            fresh = base.get('downloads', {}).get('client', {})
            if fresh and any(cached.get(k) != fresh.get(k) for k in ('sha1', 'size')):
                raise ValueError('Installed client metadata differs from official Minecraft')
        if 'logging' in doc:
            logging = doc['logging']
            if not isinstance(logging, dict) or logging.get('argument') != '-Dlog4j.configurationFile=${path}':
                raise ValueError('Unsupported Fabric logging configuration')
            spec = copy.deepcopy(logging.get('file', {}))
            if not isinstance(spec, dict):
                raise ValueError('Invalid Fabric logging file metadata')
            identifier = version_id(spec.get('id'))
            if urlsplit(spec.get('url', '')).hostname != 'piston-data.mojang.com' or not spec.get('url', '').startswith('https://') or not re.fullmatch('[0-9a-f]{40}', spec.get('sha1', '')):
                raise ValueError('Logging configuration requires a verified Mojang download')
            spec['path'] = f'assets/log_configs/{identifier}'
            result['_fabric_logging'] = spec
        if 'assetIndex' in doc and base.get('assetIndex') and (not isinstance(doc['assetIndex'], dict) or doc['assetIndex'].get('id') != base['assetIndex']['id']):
            raise ValueError('Installed Fabric asset index differs from base Minecraft')
        if 'javaVersion' in doc:
            from .metadata import java_major
            required_major = java_major(doc)
            if required_major > java_major(result):
                result['javaVersion'] = copy.deepcopy(doc['javaVersion'])
        args = argument_lists(doc)
        for section in ('default-user-jvm', 'jvm', 'game'):
            if 'minecraftArguments' in doc and section == 'game':
                result['arguments']['game'] = args['game']
            else:
                result['arguments'].setdefault(section, []).extend(args[section])
        if not isinstance(doc.get('libraries', []), list) or not isinstance(doc.get('+libraries', []), list):
            raise ValueError('Invalid Fabric libraries')
        libs = doc.get('libraries', []) + doc.get('+libraries', [])
        layers.append(libs)
    if result['mainClass'] not in MAINS:
        raise ValueError('Fabric client main class is missing')
    if '_fabric_logging' in result:
        spec = result['_fabric_logging']
        result['arguments']['jvm'].append('-Dlog4j.configurationFile=${assets_root}/log_configs/' + spec['id'])
    result['libraries'] = merge_libraries(layers)
    names = [lib['name'] for lib in result['libraries']]
    if f'net.fabricmc:fabric-loader:{loader}' not in names:
        raise ValueError('Selected Fabric Loader artifact/version is missing or changed')
    requires_mappings = any(name.startswith('net.fabricmc:intermediary:') for name in names) or any(
        requirement.get('uid') == INTERMEDIARY and ('equals' in requirement or 'suggests' not in requirement)
        for doc in documents for requirement in doc.get('requires', []))
    if requires_mappings and f'net.fabricmc:intermediary:{base["id"]}' not in names:
        raise ValueError(f'Required component {INTERMEDIARY} library for Minecraft {base["id"]} is missing or changed')
    return result


def downloadable_artifact(spec: dict[str, Any]) -> bool:
    """Existing pinned public-library fallback, shared with local preflight."""
    url = urlsplit(spec.get('url', ''))
    return bool(re.fullmatch('[0-9a-f]{40}', spec.get('sha1', '')) and
                url.scheme == 'https' and url.hostname in ('libraries.minecraft.net', 'maven.fabricmc.net') and
                url.path.lstrip('/') == spec['path'] and not url.query and not url.fragment)


@dataclass
class FabricInstallation:
    minecraft: str
    loader: str
    client: Path
    libraries: Path
    profiles: list[dict[str, Any]]
    metadata_files: tuple[Path, ...]
    fingerprint: str
    needs_metadata: bool = False
    instance: Path | None = None
    launcher_root: Path | None = None
    selected_version: str | None = None
    downloaded: dict[str, Path] = field(default_factory=dict, init=False, repr=False)

    def check_local_requirements(self) -> None:
        """No HTTP or hashing: report known, mandatory local-only artifacts early.

        Metadata fallback and checksum-pinned public dependencies still resolve
        in the worker, exactly as in plan(). This is not console verification.
        """
        layers = [doc.get('libraries', []) + doc.get('+libraries', []) for doc in self.profiles]
        for raw in merge_libraries([[], *layers]):
            spec = raw['downloads']['artifact']
            if not safe_path(self.libraries, spec['path']).is_file() and not downloadable_artifact(spec):
                raise ValueError(f'Fabric library missing: {spec["path"]}. Complete installation in the launcher first.')

    def plan(self, base: dict[str, Any], downloader: Downloader) -> dict[str, Any]:
        docs = self.profiles
        if self.needs_metadata:
            url = f'https://meta.fabricmc.net/v2/versions/loader/{quote(self.minecraft, safe="")}/{quote(self.loader, safe="")}/profile/json'
            doc = downloader.json({'url': url})
            if not isinstance(doc, dict) or doc.get('inheritsFrom') != self.minecraft:
                raise ValueError('Official Fabric metadata returned a different Minecraft profile')
            docs = [*docs, doc]
        parent = read_json(self.metadata_files[-1]) if self.metadata_files[-1].parent.parent.name.casefold() == 'versions' else None
        if parent is not None and 'inheritsFrom' not in parent:
            for key in ('mainClass', 'libraries', 'arguments', 'javaVersion', 'assetIndex'):
                if key in parent and parent[key] != base.get(key):
                    raise ValueError(f'Installed base Minecraft {key} differs from resolved official metadata; use a standard base and a Fabric child profile')
        data: dict[str, Any] = {'schema': 1, 'minecraft': self.minecraft, 'loader': self.loader, 'profiles': docs}
        effective = derive(base, data)
        records = []
        for spec, _, _ in dependencies(effective):
            path = safe_path(self.libraries, spec['path'])
            # Mojang artifacts are acquired through the unchanged base pipeline;
            # any dependency introduced/overridden by Fabric is local-only.
            if any(base_spec == spec for base_spec, _, _ in dependencies(base)):
                continue
            if not path.is_file() and downloadable_artifact(spec):
                path = downloader.fetch(spec)
                self.downloaded[spec['path']] = path
            if not path.is_file():
                raise ValueError(f'Fabric library missing: {spec["path"]}. Complete installation in the launcher first.')
            if spec.get('sha1') and digest(path) != spec['sha1']:
                raise ValueError(f'Fabric library checksum mismatch: {spec["path"]}')
            if spec.get('size') is not None and path.stat().st_size != spec['size']:
                raise ValueError(f'Fabric library size mismatch: {spec["path"]}')
            records.append({'path': spec['path'], 'sha256': digest(path, 'sha256'), 'size': path.stat().st_size})
        data['artifacts'] = records
        if '_fabric_logging' in effective:
            spec = effective['_fabric_logging']
            path = downloader.fetch(spec)
            self.downloaded[spec['path']] = path
            data['logging'] = {**spec, 'sha256': digest(path, 'sha256')}
        return data

    def check_unchanged(self) -> None:
        if self.instance is None:
            return
        latest = inspect(self.instance, self.selected_version, libraries=self.libraries, launcher_root=self.launcher_root)
        if latest.fingerprint != self.fingerprint or latest.loader != self.loader:
            raise ValueError('Fabric metadata changed during packaging; rescan and retry')

    def copy_libraries(self, root: Path, data: dict[str, Any], cancellation: CancellationToken | None = None) -> None:
        entries = [(record, root / 'libraries') for record in data['artifacts']]
        if 'logging' in data:
            entries.append((data['logging'], root))
        for record, destination in entries:
            source = self.downloaded.get(record['path']) or safe_path(self.libraries, record['path'])
            if digest(source, 'sha256') != record['sha256']:
                raise ValueError('Fabric dependency changed during packaging')
            target = safe_path(destination, record['path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open('rb') as incoming, target.open('xb') as outgoing:
                while block := incoming.read(1024 * 1024):
                    if cancellation is not None:
                        cancellation.check()
                    outgoing.write(block)
            if digest(target, 'sha256') != record['sha256']:
                raise ValueError('Fabric dependency changed during copying')
        if fingerprint(self.metadata_files) != self.fingerprint:
            raise ValueError('Fabric metadata changed during packaging; rescan and retry')


def fingerprint(paths: tuple[Path, ...]) -> str:
    return hashlib.sha256(b''.join(path.read_bytes() for path in paths)).hexdigest()


def inspect(instance: Path, version: str | None = None, libraries: Path | None = None, launcher_root: Path | None = None) -> FabricInstallation:
    from .instance_content import metadata_path
    instance = instance.resolve()
    if (instance / 'metadata/fabric.json').exists():
        raise ValueError('Packaged Fabric reimport is unsupported; select the original launcher instance or use import-instance')
    docs: list[dict[str, Any]] = []
    files: list[Path] = []
    if (instance / 'mmc-pack.json').is_file():
        manifest_path = metadata_path(instance, 'mmc-pack.json')
        manifest = read_json(manifest_path)
        files.append(manifest_path)
        components = manifest.get('components', [])
        if not isinstance(components, list) or any(not isinstance(c, dict) for c in components):
            raise ValueError('Invalid Prism/MultiMC components')
        by_uid: dict[str, dict[str, Any]] = {}
        for component in components:
            uid = component.get('uid')
            if not isinstance(uid, str) or uid not in SUPPORTED:
                raise ValueError(f'Unsupported Fabric component: {uid!r}')
            if uid in by_uid:
                raise ValueError(f'Duplicate/conflicting component {uid}: {by_uid[uid].get("version")} and {component.get("version")}')
            by_uid[uid] = component
        game = version_id(by_uid.get('net.minecraft', {}).get('version'))
        for patch in (instance / 'patches').glob('*.json'):
            if patch.stem not in SUPPORTED:
                raise ValueError('Fabric custom patches are unsupported unless they name a known component')
        loader = version_id(by_uid.get(FABRIC, {}).get('version'))
        if version is not None and version != game:
            raise ValueError('Selected Fabric Minecraft version changed')
        if INTERMEDIARY in by_uid and by_uid[INTERMEDIARY].get('version') != game:
            raise ValueError(f'Component {INTERMEDIARY} version {by_uid[INTERMEDIARY].get("version")} conflicts with selected Minecraft {game}')
        selected_versions = {uid: c.get('version', '') for uid, c in by_uid.items()}
        selected_versions[INTERMEDIARY] = game
        if libraries is None:
            owner = launcher_root or instance.parent.parent
            libraries = owner / 'libraries'
            import configparser
            for config in ('prismlauncher.cfg', 'PrismLauncher.cfg', 'multimc.cfg', 'MultiMC.cfg'):
                if (owner / config).is_file():
                    settings = configparser.ConfigParser(interpolation=None)
                    try:
                        settings.read(owner / config, encoding='utf-8-sig')
                    except configparser.Error as exc:
                        raise ValueError('Cannot read launcher library settings') from exc
                    value = settings.get('General', 'LibrariesDir', fallback='libraries')
                    configured = Path(value).expanduser()
                    libraries = configured if configured.is_absolute() else owner / configured
                    break
        owner = launcher_root or instance.parent.parent
        if not (owner / 'meta').is_dir() and (libraries.parent / 'meta').is_dir():
            owner = libraries.parent
        requested = [(uid, c.get('version')) for uid, c in by_uid.items() if uid != 'net.minecraft.java']
        if INTERMEDIARY not in by_uid:
            requested.append((INTERMEDIARY, game))
        loaded = []
        for uid, revision in requested:
            revision = version_id(revision)
            patch = instance / 'patches' / f'{uid}.json'
            path = patch if patch.is_file() else owner / 'meta' / uid / f'{revision}.json'
            if not path.is_file():
                if uid in ('org.lwjgl', 'org.lwjgl3'):
                    raise ValueError(f'Missing installed metadata for selected component {uid} {revision}')
                continue
            boundary = instance if patch.is_file() else owner / 'meta'
            path = safe_path(boundary, path.relative_to(boundary).as_posix())
            doc = read_json(path)
            if doc.get('uid') != uid or doc.get('version') != revision:
                raise ValueError(f'Installed component {uid} metadata mismatch: expected {uid} {revision}, found {doc.get("uid")} {doc.get("version")}')
            check_component_requirements(doc, selected_versions)
            for requirement in doc.get('requires', []):
                dependency = requirement['uid']
                if ('suggests' in requirement and 'equals' not in requirement
                        and dependency not in selected_versions and dependency in SUPPORTED
                        and dependency != 'net.minecraft.java'):
                    # Use an already cached hint only when no installed pin is
                    # selected. Do not fetch component metadata or choose latest.
                    try:
                        suggested = version_id(requirement['suggests'])
                    except ValueError as exc:
                        raise ValueError(f'Invalid suggested version for component {dependency}') from exc
                    suggested_patch = instance / 'patches' / f'{dependency}.json'
                    suggested_cache = owner / 'meta' / dependency / f'{suggested}.json'
                    if suggested_patch.is_file() or suggested_cache.is_file():
                        selected_versions[dependency] = suggested
                        requested.append((dependency, suggested))
            files.append(path)
            loaded.append(doc)
        docs = sorted(loaded, key=lambda doc: doc.get('order', 0))
        # Metadata fallback is exact-version Fabric Meta, never latest-loader.
        if (any(doc.get('uid') == FABRIC for doc in docs)
                and not any(doc.get('uid') == INTERMEDIARY for doc in docs)
                and (INTERMEDIARY in by_uid or any(
                    req.get('uid') == INTERMEDIARY and ('equals' in req or 'suggests' not in req)
                    for doc in docs for req in doc.get('requires', [])))):
            # The manifest pins intermediary to the base game even when the
            # launcher cached no separate (implicit) component JSON.
            docs.append({'uid': INTERMEDIARY, 'version': game, 'libraries': [
                {'name': f'net.fabricmc:intermediary:{game}'}]})
        source_owner = owner
        client = safe_path(libraries, f'com/mojang/minecraft/{game}/minecraft-{game}-client.jar')
    else:
        root = instance.parent.parent if instance.parent.name.casefold() == 'versions' else instance
        if instance.parent.name.casefold() == 'versions':
            directory = instance
        elif version is not None:
            directory = root / 'versions' / version_id(version)
        else:
            raise ValueError('Select an installed official Fabric version profile')
        source_owner = root
        libraries = libraries or root / 'libraries'
        selected_id = directory.name
        seen: set[str] = set()
        while True:
            if len(seen) >= 32 or directory.name in seen:
                raise ValueError('Cyclic or excessive Fabric version inheritance')
            seen.add(directory.name)
            path = metadata_path(root, f'versions/{directory.name}/{directory.name}.json')
            doc = read_json(path)
            if doc.get('id') != directory.name:
                raise ValueError('Installed Fabric profile ID mismatch')
            files.append(path)
            if 'inheritsFrom' not in doc:
                game = version_id(doc.get('id'))
                client = safe_path(root, f'versions/{game}/{game}.jar')
                break
            docs.insert(0, doc)
            directory = root / 'versions' / version_id(doc['inheritsFrom'])
        versions = {lib['name'].split(':')[2] for doc in docs for lib in doc.get('libraries', [])
                    if isinstance(lib, dict) and isinstance(lib.get('name'), str) and lib['name'].startswith('net.fabricmc:fabric-loader:')}
        if len(versions) != 1:
            raise ValueError('Official Fabric profile needs exactly one installed Fabric Loader version')
        loader = version_id(versions.pop())
        if version is not None and version not in (selected_id, game):
            raise ValueError('Requested version does not match selected Fabric profile')
    if not client.is_file():
        raise ValueError('Installed Fabric base Minecraft client is missing; no replacement will be downloaded')
    _, loader_path = coordinate(f'net.fabricmc:fabric-loader:{loader}')
    if not safe_path(libraries, loader_path).is_file():
        raise ValueError('Fabric Loader library is missing; complete the local installation first')
    if docs and (not (instance / 'mmc-pack.json').is_file() or any(doc.get('uid') == FABRIC for doc in docs)):
        derive({'id': game, 'mainClass': 'net.minecraft.client.main.Main',
                'libraries': [], 'arguments': {'jvm': [], 'game': []}},
               {'schema': 1, 'minecraft': game, 'loader': loader, 'profiles': docs})
    metadata_files = tuple(files)
    return FabricInstallation(game, loader, client, libraries.resolve(), docs, metadata_files, fingerprint(metadata_files),
                              (instance / 'mmc-pack.json').is_file() and not any(doc.get('uid') == FABRIC for doc in docs), instance, source_owner, version)


def validate(root: Path, base: dict[str, Any], data: dict[str, Any]) -> list[str]:
    errors = []
    try:
        effective = derive(base, data)
        base_specs = [spec for spec, _, _ in dependencies(base)]
        expected = [spec['path'] for spec, _, _ in dependencies(effective) if spec not in base_specs]
        if [record['path'] for record in data['artifacts']] != expected:
            raise ValueError('Fabric artifact inventory differs from resolved profiles')
        for record in data['artifacts']:
            path = safe_path(root / 'libraries', record['path'])
            if not path.is_file() or path.stat().st_size != record['size'] or digest(path, 'sha256') != record['sha256']:
                errors.append(f'Invalid Fabric artifact: {record["path"]}')
        if '_fabric_logging' in effective:
            spec = effective['_fabric_logging']
            if {k: v for k, v in data.get('logging', {}).items() if k != 'sha256'} != spec:
                raise ValueError('Fabric logging metadata does not match installed profile')
            path = safe_path(root, spec['path'])
            if digest(path) != spec['sha1'] or digest(path, 'sha256') != data['logging']['sha256']:
                errors.append('Invalid Fabric logging configuration')
        for coordinate_name, required in [
            (f'net.fabricmc:fabric-loader:{data["loader"]}', [effective['mainClass'].replace('.', '/') + '.class', 'net/fabricmc/loader/api/FabricLoader.class']),
            (f'net.fabricmc:intermediary:{base["id"]}', ['mappings/mappings.tiny']),
        ]:
            if coordinate_name.startswith('net.fabricmc:intermediary:') and coordinate_name not in [lib['name'] for lib in effective['libraries']]:
                continue
            _, relative = coordinate(coordinate_name)
            with zipfile.ZipFile(safe_path(root / 'libraries', relative)) as jar:
                for member in required:
                    if not jar.read(member):
                        errors.append(f'Empty Fabric entry point: {member}')
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        errors.append(f'Invalid Fabric launch plan: {exc}')
    return errors
