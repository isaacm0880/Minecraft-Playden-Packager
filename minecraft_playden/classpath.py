"""Validate actual Java classpath paths, archives and dependency entry points."""
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Any

from .arguments import parse_argfile, classpath_argument
from .dependencies import resolve
from .downloads import digest, safe_path
from .entrypoints import client_main, legacy_wrapper, verify_legacy_class, LEGACY_MAIN, LEGACY_CLASSES

# Stable public entry points for modern vanilla dependencies. This is a smoke
# check for known failure modes, not exhaustive JVM linkage or console testing.
CRITICAL_CLASSES: dict[str, tuple[str, ...]] = {
    'it.unimi.dsi:fastutil': ('it/unimi/dsi/fastutil/floats/FloatList.class',),
    'com.google.guava:guava': ('com/google/common/collect/ImmutableList.class',),
    'com.google.code.gson:gson': ('com/google/gson/Gson.class',),
    'io.netty:netty-buffer': ('io/netty/buffer/ByteBuf.class',),
    'io.netty:netty-common': ('io/netty/util/ReferenceCounted.class',),
    'io.netty:netty-transport': ('io/netty/channel/Channel.class',),
    'io.netty:netty-transport-native-unix-common': ('io/netty/channel/unix/Errors.class',),
    'org.lwjgl:lwjgl-spng': ('org/lwjgl/util/spng/SPNG.class',),
    'org.lwjgl:lwjgl': ('org/lwjgl/system/MemoryUtil.class',),
}
FLOAT_LIST = 'it/unimi/dsi/fastutil/floats/FloatList.class'


def argument_classpath(root: Path, name: str, cwd: Path, main_class: str,
                       expected: list[str]) -> list[str]:
    """Check what Windows Java will resolve, not just a regenerated text string."""
    errors: list[str] = []
    try:
        value = classpath_argument(parse_argfile((root / name).read_text(encoding='utf-8')), main_class)
        actual = []
        for entry in value.split(';'):
            windows = PureWindowsPath(entry)
            if not entry or windows.drive or windows.root or '*' in entry or '?' in entry:
                raise ValueError(f'Classpath must contain explicit portable relative JAR paths: {entry!r}')
            path = (cwd / entry.replace('\\', '/')).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError(f'Classpath path escapes the package: {entry}')
            relative = path.relative_to(root.resolve()).as_posix()
            actual.append(relative)
            if not path.is_file():
                errors.append(f'{name}: classpath JAR missing from working directory {cwd.relative_to(root) or "."}: {entry}')
        if len(actual) != len(set(actual)):
            errors.append(f'{name}: duplicate classpath entries')
        if actual != expected:
            missing = [p for p in expected if p not in actual]
            extra = [p for p in actual if p not in expected]
            detail = f' Missing: {missing}.' if missing else ''
            detail += f' Unexpected: {extra}.' if extra else ''
            errors.append(f'{name}: effective Windows classpath differs from required dependencies.{detail}')
    except (OSError, ValueError) as exc:
        errors.append(f'{name}: invalid Java classpath: {exc}')
    return errors


def validate_classpath(root: Path, metadata: dict[str, Any], expected: list[str],
                       client: str = 'versions/client.jar', client_main_class: str | None = None) -> list[str]:
    errors: list[str] = []
    specs = {'libraries/' + spec['path']: spec for spec, _, _ in resolve(metadata)}
    specs[client] = metadata['downloads']['client']
    required_by_path: dict[str, set[str]] = {}
    for library in metadata['libraries']:
        artifact = library.get('downloads', {}).get('artifact')
        if artifact:
            coordinate = ':'.join(library['name'].split(':')[:2])
            if len(library['name'].split(':')) == 3 and coordinate in CRITICAL_CLASSES:
                required_by_path['libraries/' + artifact['path']] = set(CRITICAL_CLASSES[coordinate])
    client_metadata = {**metadata, 'mainClass': client_main_class or metadata['mainClass']}
    target = client_main(client_metadata)
    wrapper = legacy_wrapper(client_metadata)
    required_by_path[client] = {target.replace('.', '/') + '.class'}
    if wrapper is not None:
        if wrapper not in expected:
            errors.append('Declared legacy LaunchWrapper is missing from the launch classpath')
        required_by_path.setdefault(wrapper, set()).update(LEGACY_CLASSES)
    # Modern vanilla requires Fastutil even if broken metadata/resolution omitted
    # the entire library. Inventory self-consistency alone cannot detect that.
    remaining = {FLOAT_LIST} if (client_main_class or metadata['mainClass']) == 'net.minecraft.client.main.Main' else set()
    for relative in expected:
        try:
            jar = safe_path(root, relative)
            if not jar.is_file():
                errors.append(f'Required classpath JAR missing: {relative}')
                continue
            spec = specs[relative]
            if spec.get('size') is not None and jar.stat().st_size != spec['size']:
                errors.append(f'Classpath JAR size mismatch: {relative}')
            if spec.get('sha1') and digest(jar) != spec['sha1']:
                errors.append(f'Classpath JAR official SHA-1 mismatch: {relative}')
            with zipfile.ZipFile(jar) as archive:
                members = set(archive.namelist())
                needed = required_by_path.get(relative, set()) | (remaining & members)
                for class_name in sorted(needed):
                    if class_name not in members:
                        errors.append(f'Critical dependency class missing: {class_name} in {relative}')
                    else:
                        # Read the member as well to verify decompression and its CRC.
                        if not archive.read(class_name):
                            errors.append(f'Critical dependency class is empty: {class_name} in {relative}')
                        remaining.discard(class_name)
                if wrapper is not None and relative == wrapper:
                    verify_legacy_class(archive, LEGACY_MAIN)
                if wrapper is not None and relative == client:
                    verify_legacy_class(archive, target, game_directory=True)
        except (OSError, ValueError, zipfile.BadZipFile, KeyError, RuntimeError, NotImplementedError) as exc:
            errors.append(f'Invalid classpath JAR {relative}: {exc}')
    for class_name in sorted(remaining):
        errors.append(f'Critical dependency class absent from the exported classpath: {class_name} (Fastutil)')
    errors.extend(argument_classpath(root, 'playden.args', root / 'java/bin', metadata['mainClass'], expected))
    return errors
