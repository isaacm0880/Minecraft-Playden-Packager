"""Evaluate metadata against Windows x64, irrespective of the packaging host."""
import re
import hashlib
import zipfile
from pathlib import Path
from typing import Any
from .downloads import safe_path, digest


def allowed(item: dict[str, Any]) -> bool:
    rules = item.get('rules')
    if rules is None:
        return True
    result = False
    target_version = (10, 0, 19045)
    for rule in rules:
        os = rule.get('os', {})
        matches = (os.get('name', 'windows') == 'windows'
                   and os.get('arch', 'x86_64') in ('x86_64', 'amd64')
                   and ('version' not in os or re.search(os['version'], '10.0.19045') is not None)
                   and all(value is False for value in rule.get('features', {}).values()))
        version_range = os.get('versionRange', {})
        for boundary, sign in [('min', 1), ('max', -1)]:
            if boundary in version_range:
                boundary_version = tuple(int(v) for v in version_range[boundary].split('.'))
                matches = matches and (target_version >= boundary_version if sign == 1 else target_version < boundary_version)
        if matches:
            result = rule['action'] == 'allow'
    return result


def resolve(metadata: dict[str, Any]) -> list[tuple[dict[str, Any], bool, list[str]]]:
    result = []
    for lib in metadata['libraries']:
        if not allowed(lib):
            continue
        downloads = lib.get('downloads', {})
        artifact = downloads.get('artifact')
        classifier = lib['name'].split(':')[3:]
        # Recent metadata supplies ARM64 natives under the same Windows rule.
        if classifier and (any(x in classifier[0] for x in ('arm64', 'aarch64')) or classifier[0].endswith('windows-x86')):
            continue
        excludes = lib.get('extract', {}).get('exclude', ['META-INF/'])
        native = lib.get('natives', {}).get('windows')
        if not artifact and not native:
            raise ValueError(f'Required Windows library has no downloadable artifact: {lib["name"]}')
        if artifact:
            result.append((artifact, bool(classifier and classifier[0].startswith('natives-')), excludes))
        if native:
            native = native.replace('${arch}', '64')
            result.append((downloads['classifiers'][native], True, excludes))
    unique: dict[str, tuple[dict[str, Any], bool, list[str]]] = {}
    for entry in result:
        path = entry[0]['path']
        if path in unique and unique[path] != entry:
            raise ValueError(f'Conflicting Windows dependency artifact: {path}')
        unique.setdefault(path, entry)
    return list(unique.values())


def coalesce(libraries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Old Mojang metadata repeats a Java artifact beside its native map."""
    import copy
    result: dict[str, dict[str, Any]] = {}
    for raw in libraries:
        if not allowed(raw):
            continue
        name = raw['name']
        if name not in result:
            result[name] = copy.deepcopy(raw)
            continue
        previous = result[name]
        if previous == raw:
            continue
        old = previous.get('downloads', {})
        new = raw.get('downloads', {})
        if old.get('artifact') != new.get('artifact'):
            raise ValueError(f'Conflicting Windows library: {name}')
        for field in ('natives', 'extract'):
            if field in previous and field in raw and previous[field] != raw[field]:
                raise ValueError(f'Conflicting Windows {field}: {name}')
            if field in raw:
                previous[field] = copy.deepcopy(raw[field])
        classifiers = old.setdefault('classifiers', {})
        for classifier, spec in new.get('classifiers', {}).items():
            if classifier in classifiers and classifiers[classifier] != spec:
                raise ValueError(f'Conflicting native classifier: {name} {classifier}')
            classifiers[classifier] = copy.deepcopy(spec)
    return list(result.values())


def extract_natives(jar: Path, root: Path, excludes: list[str]) -> None:
    with zipfile.ZipFile(jar) as archive:
        for info in archive.infolist():
            safe_path(root, info.filename)
            if info.is_dir() or any(info.filename.startswith(x) for x in excludes):
                continue
            if info.filename.lower().endswith('.dll'):
                target = root / Path(info.filename).name
                data = archive.read(info)
                if target.exists() and target.read_bytes() != data:
                    raise ValueError(f'Conflicting native DLL: {target.name}')
                root.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)


def classpath(metadata: dict[str, Any]) -> list[str]:
    return ['libraries/' + spec['path'] for spec, _, _ in resolve(metadata)] + ['versions/client.jar']


def validate_natives(root: Path, metadata: dict[str, Any]) -> list[str]:
    """Require each selected native archive's DLLs, not merely any one DLL."""
    errors = []
    for spec, native, excludes in resolve(metadata):
        if not native:
            continue
        relative = 'libraries/' + spec['path']
        try:
            jar = safe_path(root, relative)
            if not jar.is_file():
                errors.append(f'Required native archive missing: {relative}')
                continue
            if spec.get('sha1') and digest(jar) != spec['sha1']:
                errors.append(f'Native archive official SHA-1 mismatch: {relative}')
            with zipfile.ZipFile(jar) as archive:
                for member in archive.infolist():
                    safe_path(root / 'natives', member.filename)
                    if member.is_dir() or any(member.filename.startswith(exclude) for exclude in excludes):
                        continue
                    if member.filename.lower().endswith('.dll'):
                        target = root / 'natives' / Path(member.filename).name
                        if not target.is_file():
                            errors.append(f'Required native DLL missing: natives/{target.name} (from {relative})')
                        elif digest(target, 'sha256') != hashlib.sha256(archive.read(member)).hexdigest():
                            errors.append(f'Native DLL differs from its verified archive: natives/{target.name}')
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
            errors.append(f'Invalid native dependency {relative}: {exc}')
    return errors
