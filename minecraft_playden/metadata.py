"""Small adapters for Mojang metadata generations, independent of console evidence."""
import copy
import re
from typing import Any

from .downloads import safe_path
from pathlib import Path

DEFAULT_JVM = ['-Djava.library.path=${natives_directory}', '-cp', '${classpath}']


def java_major(metadata: dict[str, Any]) -> int:
    java = metadata.get('javaVersion', {})
    if not isinstance(java, dict):
        raise ValueError('Invalid Java runtime metadata')
    major = java.get('majorVersion', 8)
    if type(major) is not int or major < 8:
        raise ValueError('Invalid required Java major version')
    return major


def check_runtime_requirement(metadata: dict[str, Any]) -> int:
    return java_major(metadata)


def normalize(document: dict[str, Any]) -> dict[str, Any]:
    """Adapt legacy game-argument strings without changing the original document."""
    from .arguments import parse_argfile
    result = copy.deepcopy(document)
    java_major(result)
    args = result.setdefault('arguments', {})
    if not isinstance(args, dict) or set(args) - {'jvm', 'game', 'default-user-jvm'}:
        raise ValueError('Unsupported Minecraft argument metadata sections')
    if 'minecraftArguments' in result:
        if not isinstance(result['minecraftArguments'], str):
            raise ValueError('Invalid legacy Minecraft arguments')
        args.setdefault('game', parse_argfile(result['minecraftArguments']))
    args.setdefault('jvm', copy.deepcopy(DEFAULT_JVM))
    args.setdefault('game', [])
    for name, values in args.items():
        if not isinstance(values, list):
            raise ValueError(f'Minecraft {name} arguments must be a list')
        for value in values:
            if isinstance(value, str):
                continue
            if not isinstance(value, dict) or ('rules' in value and not isinstance(value['rules'], list)):
                raise ValueError('Invalid conditional Minecraft argument')
            words = value.get('value')
            if not isinstance(words, str) and (not isinstance(words, list) or any(not isinstance(w, str) for w in words)):
                raise ValueError('Invalid Minecraft argument value')
    if not isinstance(result.get('mainClass'), str) or not result['mainClass']:
        raise ValueError('Minecraft main class is missing')
    if not isinstance(result.get('libraries'), list):
        raise ValueError('Minecraft libraries must be a list')
    return result


def merge(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Resolve a metadata layer: child scalars/artifacts win, arguments accumulate."""
    result = copy.deepcopy(parent)
    for name, value in child.items():
        if name not in ('inheritsFrom', 'libraries', 'arguments', 'downloads'):
            result[name] = copy.deepcopy(value)
    result['downloads'] = {**parent.get('downloads', {}), **child.get('downloads', {})}
    libraries: dict[str, dict[str, Any]] = {}
    from .dependencies import coalesce
    for layer in (coalesce(parent.get('libraries', [])), coalesce(child.get('libraries', []))):
        seen: set[str] = set()
        for library in layer:
            parts = library['name'].split(':')
            key = ':'.join(parts[:2] + parts[3:])
            if key in seen:
                raise ValueError(f'Ambiguous library override: {key}')
            seen.add(key)
            libraries[key] = copy.deepcopy(library)
    result['libraries'] = list(libraries.values())
    result['arguments'] = {}
    for section in ('jvm', 'game', 'default-user-jvm'):
        values = parent.get('arguments', {}).get(section, []) + child.get('arguments', {}).get(section, [])
        if values:
            result['arguments'][section] = copy.deepcopy(values)
    if 'minecraftArguments' in child:
        result['arguments'].pop('game', None)
    result.pop('inheritsFrom', None)
    return result


def logging_spec(metadata: dict[str, Any]) -> dict[str, Any] | None:
    logging = metadata.get('logging', {}).get('client')
    if logging is None:
        return None
    if not isinstance(logging, dict) or logging.get('argument') != '-Dlog4j.configurationFile=${path}':
        raise ValueError('Unsupported Minecraft logging configuration')
    spec = copy.deepcopy(logging.get('file', {}))
    identifier = spec.get('id', '')
    if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_.+-]+', identifier) or identifier in ('.', '..'):
        raise ValueError('Unsafe Minecraft logging filename')
    if not isinstance(spec.get('url'), str) or not spec['url'].startswith('https://') or not re.fullmatch('[0-9a-f]{40}', spec.get('sha1', '')):
        raise ValueError('Minecraft logging requires an HTTPS URL and SHA-1')
    spec['path'] = f'assets/log_configs/{identifier}'
    safe_path(Path('/logging'), spec['path'])
    return spec
