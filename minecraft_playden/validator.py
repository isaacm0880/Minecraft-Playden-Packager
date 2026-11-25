"""Verify the immutable package inventory and regenerated launch configuration."""
import json
from pathlib import Path
from .operations import CancellationToken
from .downloads import digest, safe_path
from .arguments import generate
from .dependencies import resolve, validate_natives
from . import dependencies
from . import profiles, recipe, fabric, assets
from .metadata import logging_spec, check_runtime_requirement
from .classpath import validate_classpath
from .progress import Progress, ProgressCallback


def validate(root: Path, cancellation: CancellationToken | None = None,
             progress: ProgressCallback | None = None) -> list[str]:
    errors = []
    try:
        inventory = json.loads((root / 'package.json').read_text(encoding='utf-8'))
        total = len(inventory['files'])
        for number, (name, expected) in enumerate(inventory['files'].items(), 1):
            if cancellation is not None:
                cancellation.check()
            path = safe_path(root, name)
            if not path.is_file():
                errors.append(f'Missing: {name}')
            elif digest(path, 'sha256', cancellation) != expected:
                errors.append(f'Hash mismatch: {name}')
            if progress is not None:
                progress(Progress('validation', 'Verifying package inventory and SHA-256 hashes…', number, total, name))
        if inventory.get('kind') == 'instance-import':
            if (root / 'recipe.json').exists() or (root / 'recipe.json').is_symlink():
                errors.append('A file-only instance import must not contain a launch recipe')
            if inventory.get('launch_status') != 'unverified':
                errors.append('An instance import cannot claim a verified launch')
            for name in ('metadata/instance-import.json', 'IMPORT.txt'):
                if name not in inventory['files']:
                    errors.append(f'Required file absent from inventory: {name}')
            if not (root / 'instances/default/minecraft').is_dir():
                errors.append('Missing imported Minecraft directory')
            report = json.loads((root / 'metadata/instance-import.json').read_text(encoding='utf-8'))
            if report['version'] != inventory['version'] or report['launch_status'] != 'unverified':
                errors.append('Invalid instance import identity/status')
            if any(name.startswith('instances/') for name in inventory['files']):
                errors.append('Imported user data must be separate from immutable files')
            return errors
        recipe_path = recipe.PATH
        for name in ('java/bin/java.exe', 'java/bin/server/jvm.dll', 'java/release', 'playden.args', recipe_path, 'metadata/version.json'):
            if name not in inventory['files']:
                errors.append(f'Required file absent from inventory: {name}')
        metadata = json.loads((root / 'metadata/version.json').read_text(encoding='utf-8'))
        if metadata['id'] != inventory['version']:
            errors.append('Package version does not match exported Minecraft metadata')
        runtime_record = json.loads((root / 'metadata/runtime.json').read_text(encoding='utf-8')) if 'metadata/runtime.json' in inventory['files'] else {}
        adapter = runtime_record.get('metadata_adapter', 0)
        if type(adapter) is not int or adapter not in (0, 1):
            raise ValueError('Unsupported package metadata adapter')
        profile = inventory.get('launch_profile', profiles.DEFAULT_PROFILE)
        effective = profiles.effective_metadata(metadata, profile)
        client = profiles.client_path(metadata, profile)
        fabric_data = None
        if inventory.get('loader') is not None:
            if inventory['loader'].get('name') != 'Fabric' or profile != profiles.DEFAULT_PROFILE:
                raise ValueError('Unsupported loader/launch profile')
            if 'metadata/fabric.json' not in inventory['files']:
                errors.append('Required file absent from inventory: metadata/fabric.json')
            fabric_data = json.loads((root / 'metadata/fabric.json').read_text(encoding='utf-8'))
            if inventory['loader'].get('version') != fabric_data['loader']:
                errors.append('Fabric Loader version does not match package inventory')
            effective = fabric.derive(metadata, fabric_data)
            errors.extend(fabric.validate(root, metadata, fabric_data))
        elif (root / 'metadata/fabric.json').exists():
            errors.append('Fabric launch plan has no package loader identity')
        check_runtime_requirement(effective)
        if 'metadata/runtime.json' in inventory['files']:
            if adapter == 1 or runtime_record.get('target') is not None:
                from .runtime import inspect as inspect_runtime
                actual_runtime = inspect_runtime(root / 'java', check_runtime_requirement(effective))
                if any(actual_runtime[key] != runtime_record.get(key) for key in ('major', 'release', 'target')):
                    errors.append('Java runtime identity differs from metadata/runtime.json')
        required = [client]
        logging = None if adapter == 0 or '_fabric_logging' in effective else logging_spec(effective)
        if logging is not None:
            required.append(logging['path'])
            if digest(safe_path(root, logging['path'])) != logging['sha1']:
                errors.append('Minecraft logging configuration checksum mismatch')
        if fabric_data is not None and '_fabric_logging' in effective:
            required.append(effective['_fabric_logging']['path'])
        required.extend('libraries/' + spec['path'] for spec, _, _ in resolve(effective))
        index_path = f'assets/indexes/{metadata["assetIndex"]["id"]}.json'
        required.append(index_path)
        index = json.loads(safe_path(root, index_path).read_text(encoding='utf-8'))
        index_spec = metadata['assetIndex']
        if index_spec.get('sha1') and digest(safe_path(root, index_path)) != index_spec['sha1']:
            errors.append('Minecraft asset index checksum mismatch')
        required.extend(name for name, _ in assets.files(index, index_spec['id'], profiles.game_directory(profile)))
        errors.extend(assets.validate(root, index, index_spec['id'], profiles.game_directory(profile), cancellation))
        for name in required:
            if name not in inventory['files']:
                errors.append(f'Required file absent from inventory: {name}')
        expected_cp = dependencies.classpath(effective) if fabric_data is not None else profiles.classpath(metadata, profile)
        if cancellation is not None:
            cancellation.check()
        errors.extend(validate_classpath(root, effective, expected_cp, client, metadata['mainClass'] if fabric_data is not None else None))
        if cancellation is not None:
            cancellation.check()
        errors.extend(validate_natives(root, effective))
        if inventory['classpath'] != expected_cp:
            errors.append('Classpath does not match resolved Windows x64 dependencies')
        if (root / 'playden.args').read_text(encoding='utf-8') != generate(effective if fabric_data is not None else metadata, inventory['classpath'], '../../', inventory['username'], profile, metadata_adapter=adapter == 1):
            errors.append('Invalid portable arguments: playden.args')
        launch_arguments = recipe.ARGUMENTS
        if check_runtime_requirement(effective) == 8:
            from . import java8
            for name in (java8.JAR, java8.COMMAND):
                if name not in inventory['files']:
                    errors.append(f'Required file absent from inventory: {name}')
            preserved = generate(effective, inventory['classpath'], '../../', inventory['username'], profile,
                                 metadata_adapter=adapter == 1)
            errors.extend(java8.validate(root, effective, inventory['classpath'], preserved))
            _, launch_arguments = java8.artifacts(effective, inventory['classpath'], preserved)
        errors.extend(recipe.validate(root, f'Minecraft {inventory["version"]}', launch_arguments=launch_arguments))
        for entry in inventory['classpath']:
            if entry not in inventory['files']:
                errors.append(f'Classpath file not inventoried: {entry}')
        from .fabric_remap import validate as validate_remap
        errors.extend(validate_remap(root, inventory, cancellation))
        if not any(p.startswith('natives/') and p.endswith('.dll') for p in inventory['files']):
            errors.append('No native DLLs')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f'Invalid package: {exc}')
    return errors
