"""Generate Java argument files using portable Windows paths."""
import re
from pathlib import Path
from typing import Any
from . import __version__
from .dependencies import allowed
from .profiles import DEFAULT_PROFILE, REFERENCE_PROFILE, check_profile, game_directory


def quote(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r') + '"'


def default_jvm(metadata: dict[str, Any]) -> list[str]:
    """Adapt automatic defaults only; installed/user JVM additions stay intact."""
    from .metadata import java_major
    major = java_major(metadata)
    result = []
    for item in metadata['arguments'].get('default-user-jvm', []):
        if isinstance(item, str):
            words = [item]
        elif allowed(item):
            value = item['value']
            words = value if isinstance(value, list) else [value]
        else:
            continue
        for word in words:
            if word == '-XX:+UseZGC':
                continue
            # JEP 450/519: experimental in 24, a product option from 25.
            if word == '-XX:+UseCompactObjectHeaders' and major < 25:
                if major != 24 or '-XX:+UnlockExperimentalVMOptions' not in result:
                    continue
            result.append(word)
    return result


def generate(metadata: dict[str, Any], classpath: list[str], prefix: str, username: str = 'Player',
             profile: str = DEFAULT_PROFILE, *, metadata_adapter: bool = True) -> str:
    check_profile(metadata, profile)
    if profile == REFERENCE_PROFILE:
        args = ['-Xmx4G', '-Djava.library.path=' + prefix + 'natives', '-cp',
                ';'.join(prefix + path for path in classpath), metadata['mainClass'],
                '--username', username, '--version', metadata['id'],
                '--gameDir', prefix + game_directory(profile), '--assetsDir', prefix + 'assets',
                '--assetIndex', metadata['assetIndex']['id'],
                '--uuid', '00000000-0000-0000-0000-000000000000', '--accessToken', '0',
                '--versionType', metadata.get('type', 'release')]
        return '\n'.join(quote(arg) if not arg or any(c.isspace() or c in '\\\"\'#' for c in arg) else arg for arg in args) + '\n'
    from .metadata import logging_spec
    from .entrypoints import legacy_wrapper
    logging = None if not metadata_adapter or '_fabric_logging' in metadata else logging_spec(metadata)
    values = {
        'auth_player_name': username, 'version_name': metadata.get('launch_version', metadata['id']),
        'game_directory': prefix + 'instances/default/minecraft', 'assets_root': prefix + 'assets',
        'assets_index_name': metadata['assetIndex']['id'],
        'game_assets': prefix + 'assets/virtual/' + metadata['assetIndex']['id'], 'auth_uuid': '00000000-0000-0000-0000-000000000000',
        'auth_session': '0',
        'user_type': 'legacy', 'user_properties': '{}',
        'auth_access_token': '0', 'clientid': '0', 'auth_xuid': '0',
        'version_type': metadata.get('type', 'release'), 'natives_directory': prefix + 'natives',
        'launcher_name': 'minecraft-playden', 'launcher_version': __version__,
        'classpath': ';'.join(prefix + p for p in classpath), 'classpath_separator': ';',
        'library_directory': prefix + 'libraries',
    }
    if legacy_wrapper(metadata) is not None:
        # Mojang's pre-1.6 index maps resources into the game directory. Its
        # VanillaTweaker also reads window icons from this same asset view.
        values['game_assets'] = prefix + game_directory(profile) + '/resources'
    def expand(value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in values:
                raise ValueError(f'Unsupported launch placeholder: {key}')
            return values[key]
        return re.sub(r'\$\{([^}]+)\}', replace, value)
    args = [] if metadata_adapter and metadata['arguments'].get('default-user-jvm') else ['-Xmx4G']
    for section in (('default-user-jvm', 'jvm', 'game') if metadata_adapter else ('jvm', 'game')):
        if section == 'default-user-jvm':
            args.extend(expand(word) for word in default_jvm(metadata))
            continue
        if section == 'game':
            # Use the proven flat native directory instead of Mojang's extraction subdirectory.
            if logging is not None:
                args.append('-Dlog4j.configurationFile=' + prefix + logging['path'])
            args += ['-Djava.library.path=' + prefix + 'natives', metadata['mainClass']]
        for item in metadata['arguments'].get(section, []):
            if isinstance(item, str):
                args.append(expand(item))
            elif allowed(item):
                value = item['value']
                args.extend(expand(v) for v in (value if isinstance(value, list) else [value]))
    return '\n'.join(quote(a) for a in args) + '\n'


def write(root: Path, metadata: dict[str, Any], classpath: list[str], username: str,
          profile: str = DEFAULT_PROFILE) -> None:
    (root / 'playden.args').write_text(generate(metadata, classpath, '../../', username, profile), encoding='utf-8')


def parse_argfile(text: str) -> list[str]:
    """Parse Java argfile quoting, independently of generation (not shell syntax).

    This handles the generated format plus comments, whitespace, single/double
    quotes and quoted escapes/continuations described by the Java launcher.
    """
    tokens: list[str] = []
    current: list[str] = []
    quote_char: str | None = None
    started = False
    position = 0
    while position < len(text):
        char = text[position]
        if quote_char is None:
            if char.isspace():
                if started:
                    tokens.append(''.join(current))
                    current, started = [], False
            elif char == '#':
                newline = text.find('\n', position)
                position = len(text) if newline < 0 else newline - 1
            elif char in ('"', "'"):
                quote_char, started = char, True
            else:
                current.append(char)
                started = True
        elif char == quote_char:
            quote_char = None
        elif char == '\\':
            position += 1
            if position >= len(text):
                raise ValueError('Unterminated escape in Java argument file')
            escaped = text[position]
            if escaped in '\r\n':
                if escaped == '\r' and position + 1 < len(text) and text[position + 1] == '\n':
                    position += 1
                while position + 1 < len(text) and text[position + 1] in ' \t\f':
                    position += 1
            else:
                current.append({'n': '\n', 'r': '\r', 't': '\t', 'f': '\f'}.get(escaped, escaped))
        else:
            current.append(char)
        position += 1
    if quote_char is not None:
        raise ValueError('Unterminated quote in Java argument file')
    if started:
        tokens.append(''.join(current))
    return tokens


def classpath_argument(tokens: list[str], main_class: str) -> str:
    """Return the single effective JVM classpath, before the game main class."""
    try:
        boundary = tokens.index(main_class)
    except ValueError:
        raise ValueError('Game main class is missing from the Java argument file') from None
    values: list[str] = []
    position = 0
    while position < boundary:
        token = tokens[position]
        if token in ('-cp', '-classpath', '--class-path'):
            if position + 1 >= boundary:
                raise ValueError('JVM classpath option has no value')
            values.append(tokens[position + 1])
            position += 1
        elif token.startswith('--class-path='):
            values.append(token.split('=', 1)[1])
        position += 1
    if len(values) != 1 or not values[0]:
        raise ValueError('Expected exactly one nonempty JVM classpath before the game main class')
    return values[0]
