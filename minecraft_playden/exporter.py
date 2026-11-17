"""Build in an isolated sibling staging directory, then publish without overwrite."""
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal
from . import arguments, dependencies, runtime, profiles, recipe, fabric, assets
from .downloads import Downloader, digest, safe_path
from .versions import VersionResolver
from .metadata import logging_spec
from .validator import validate
from .sources import ClientSource
from .instance_content import check_separate
from .volumes import publish_without_replace
from .operations import CancellationToken
from .progress import Progress, ProgressCallback

INSTRUCTIONS = '''Playden configuration:
  Program: java/bin/java.exe
  Launch arguments: @../../playden.args

Note: Online play limited to offline mode servers, authentication is not yet implemented'''


def build(output: Path, downloader: Downloader, source: ClientSource, username: str = 'Player',
          progress: ProgressCallback | None = None, launch_profile: str = profiles.DEFAULT_PROFILE,
          cancellation: CancellationToken | None = None, exclude_saves: bool = False,
          privacy_cleanup: bool = False, instance_name: str | None = None,
          prepare_fabric: bool | Literal['auto'] = False, remap_java: Path | None = None) -> None:
    def check_cancelled() -> None:
        if cancellation is not None:
            cancellation.check()
    def report(stage: str, message: str, completed: int = 0, total: int | None = None) -> None:
        check_cancelled()
        if progress is not None:
            progress(Progress(stage, message, completed, total))
        elif completed == 0 or stage == 'complete':
            print(message, flush=True)
    output = output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError(f'Refusing to overwrite existing output: {output}')
    report('source', 'Checking selected client source...')
    version = source.prepare()
    source.configure_downloads(downloader, version)
    from .compatibility import assess, Status
    compatibility = assess(version, source.content.loaders if source.content else ())
    if not compatibility.permits_build:
        raise ValueError(compatibility.label)
    if compatibility.status in (Status.UNTESTED, Status.RETEST):
        report('compatibility', compatibility.evidence)
    if source.content is not None:
        check_separate(source.content.root, output)
        check_separate(source.content.root, downloader.cache)
    if source.fabric is not None:
        report('loader', f'Fabric Loader {source.fabric.loader} for Minecraft {source.fabric.minecraft} (Windows x64)')
        check_separate(source.fabric.libraries, output)
        check_separate(source.fabric.libraries, downloader.cache)
        if source.fabric.launcher_root is not None:
            check_separate(source.fabric.launcher_root, output)
            check_separate(source.fabric.launcher_root, downloader.cache)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.playden-', dir=output.parent) as temporary:
        root = Path(temporary) / 'package'
        root.mkdir()
        report('metadata', f'Resolving official metadata for {version}...')
        metadata = VersionResolver(downloader).resolve(version)
        def save(name: str, data: object) -> None:
            path = safe_path(root, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        source.check(metadata)
        save('metadata/version.json', metadata)
        effective = profiles.effective_metadata(metadata, launch_profile)
        classpath = profiles.classpath(metadata, launch_profile)
        fabric_data = None
        if source.fabric is not None:
            if launch_profile != profiles.DEFAULT_PROFILE:
                raise ValueError('Fabric supports the official launch profile; reference-26.3 remains Vanilla only')
            fabric_data = source.fabric.plan(metadata, downloader)
            save('metadata/fabric.json', fabric_data)
            effective = fabric.derive(metadata, fabric_data)
            classpath = dependencies.classpath(effective)
        from .compatibility import assess_metadata
        capability = assess_metadata(effective)
        if not capability.can_package:
            raise ValueError(capability.problem)
        libraries = dependencies.resolve(effective)
        logging = None if '_fabric_logging' in effective else logging_spec(effective)
        if logging is not None:
            downloader.fetch(logging, safe_path(root, logging['path']))
        report('libraries', 'Downloading public Windows x64 libraries...', 0, len(libraries))
        local_artifacts = {record['path'] for record in fabric_data['artifacts']} if fabric_data is not None else set()
        if source.fabric is not None and fabric_data is not None:
            source.fabric.copy_libraries(root, fabric_data, cancellation)
        for number, (spec, native, excludes) in enumerate(libraries, 1):
            relative = 'libraries/' + spec['path']
            jar = safe_path(root, relative) if spec['path'] in local_artifacts else downloader.fetch(spec, safe_path(root, relative))
            if native:
                dependencies.extract_natives(jar, root / 'natives', excludes)
            report('libraries', 'Downloading public Windows x64 libraries...', number, len(libraries))
        client = profiles.client_path(metadata, launch_profile)
        report('client', 'Acquiring verified client from the selected source...')
        (root / client).parent.mkdir(parents=True, exist_ok=True)
        source.acquire(metadata, downloader, root / client)
        index_spec = {**metadata['assetIndex'], 'path': f'assets/indexes/{metadata["assetIndex"]["id"]}.json'}
        index = downloader.json(index_spec)
        downloader.fetch(index_spec, safe_path(root, f'assets/indexes/{index_spec["id"]}.json'))
        asset_files = assets.files(index, index_spec['id'], profiles.game_directory(launch_profile))
        def asset(item: dict) -> None:
            check_cancelled()
            sha = item['hash']
            relative = f'assets/objects/{sha[:2]}/{sha}'
            downloader.fetch({'url': f'https://resources.download.minecraft.net/{sha[:2]}/{sha}',
                              'sha1': sha, 'size': item['size'], 'path': relative}, safe_path(root, relative))
        unique = {v['hash']: v for v in index['objects'].values()}
        report('assets', 'Downloading public Minecraft assets...', 0, len(unique))
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(asset, item) for item in unique.values()]
            for number, future in enumerate(as_completed(futures), 1):
                future.result()
                report('assets', 'Downloading public Minecraft assets...', number, len(unique))
        report('java', 'Selecting metadata-matched Windows x64 Java runtime (local/cache first)...')
        downloader.progress = progress
        save('metadata/runtime.json', {**runtime.install(effective, downloader, root / 'java'), 'metadata_adapter': 1})
        (root / profiles.game_directory(launch_profile)).mkdir(parents=True)
        report('instance', 'Preserving instance files' + (' without world saves...' if exclude_saves else ', including world saves...'))
        source.copy_instance(root, profiles.game_directory(launch_profile), exclude_saves, cancellation)
        assets.materialize(root, index, index_spec['id'], profiles.game_directory(launch_profile), cancellation)
        if privacy_cleanup:
            from .privacy import cleanup
            report('privacy', 'Removing known private data from the staged copy...')
            cleanup(root / 'instances', cancellation)
            cleanup(root / 'metadata/local-instance', cancellation)
        report('arguments', 'Generating portable Playden launch arguments...')
        arguments.write(root, effective if fabric_data is not None else metadata, classpath, username, launch_profile)
        recipe.write(root, f'Minecraft {version}')
        if instance_name is not None:
            save('metadata/instance-name.json', {'schema': 1, 'name': instance_name})
        (root / 'PLAYDEN.txt').write_text(f'Game title: Minecraft {version}\n\n' + INSTRUCTIONS, encoding='utf-8')
        report('hashing', 'Hashing immutable package files...')
        mapped = {root / name for name, _ in asset_files}
        paths = [p for p in root.rglob('*') if p.is_file() and (not p.is_relative_to(root / 'instances') or p in mapped)]
        files = {}
        for number, path in enumerate(paths, 1):
            files[path.relative_to(root).as_posix()] = digest(path, 'sha256', cancellation)
            report('hashing', 'Hashing immutable package files...', number, len(paths))
        save('package.json', {'schema': 2, 'launch_profile': launch_profile, 'source': source.mode, 'version': version, 'username': username, 'classpath': classpath, 'files': files,  **({'loader': {'name': 'Fabric', 'version': source.fabric.loader}} if source.fabric else {})})
        if prepare_fabric and fabric_data is not None:
            from .fabric_remap import prepare_staged, package_status
            state = package_status(root, validated=False, cancellation=cancellation)
            if prepare_fabric is True or state.required:
                prepare_staged(root, remap_java, cancellation, progress)
            else:
                report('prepare-check', state.message)
        report('validation', 'Validating package inventory and launch configuration...')
        errors = validate(root, cancellation=cancellation, progress=progress)
        if errors:
            raise ValueError('\n'.join(errors))
        if output.exists():
            raise ValueError('Output appeared during build; refusing overwrite')
        if source.fabric is not None:
            source.fabric.check_unchanged()
        report('export', 'Publishing the validated package...')
        check_cancelled()
        publish_without_replace(root, output)
        # Publication is the commit point. A late cancel cannot turn a validated
        # published package into a cancelled result.
        event = Progress('complete', 'Package validated and ready for console testing.', 1, 1)
        if progress is not None:
            progress(event)
        else:
            print(event.message, flush=True)
