"""Bounded Java-8 invocation, retaining original JVM settings and game argv."""
import hashlib
import io
import re
import struct
import zipfile
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .arguments import classpath_argument, parse_argfile
from .downloads import safe_path

JAR = 'playden-java8.jar'
COMMAND = 'playden.java8-command'
MAIN = 'org.minecraftplayden.Java8Launch'
CLASS = 'org/minecraftplayden/Java8Launch.class'
MAX_BYTES = 4096
MAX_WORDS = 32
# Reproducibly compiled from resources/java8/Java8Launch.java; see docs/JAVA8-LAUNCH.md.
BOOTSTRAP_SHA256 = '174127a697b1b2b67ce7ed9baf907e817cb47f011103e063a181adadc66610ac'


def windows_command(words: list[str]) -> str:
    """Playden/prospero-win groups double quotes, with no escaping syntax."""
    if len(words) > MAX_WORDS:
        raise ValueError('Java 8 launch exceeds Playden\'s 32 argument words')
    if any('"' in word or any(ord(c) < 32 or ord(c) == 127 for c in word) for word in words):
        raise ValueError('Java 8 JVM options contain quotes/control characters unsupported by Playden')
    command = ' '.join('"' + word + '"' for word in words)
    if len(command.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Java 8 launch exceeds Playden\'s 4096-byte argument limit')
    return command


def _manifest(classpath: list[str]) -> bytes:
    # Encoded relative URLs are resolved against the generated JAR at package root.
    urls = []
    for path in classpath:
        safe_path(Path('/java8-package'), path)
        if (not path.endswith('.jar') or any(part in ('', '.', '..') for part in path.split('/'))
                or re.search(r'[\\;*?\x00-\x1f\x7f]', path)):
            raise ValueError('Java 8 classpath requires explicit portable JAR paths')
        urls.append(quote(path, safe='/'))
    if not urls or len(urls) != len(set(urls)):
        raise ValueError('Java 8 classpath is empty or contains duplicates')
    text = ('Class-Path: ' + ' '.join(urls)).encode('ascii')
    lines = [b'Manifest-Version: 1.0']
    # Manifest continuation lines include a leading space; max 72 bytes per line.
    lines.append(text[:72])
    text = text[72:]
    while text:
        lines.append(b' ' + text[:71])
        text = text[71:]
    return b'\r\n'.join(lines) + b'\r\n\r\n'


def artifacts(metadata: dict[str, Any], classpath: list[str], argfile: str) -> tuple[bytes, str]:
    words = parse_argfile(argfile)
    main = metadata['mainClass']
    if main not in ('net.minecraft.client.main.Main', 'net.fabricmc.loader.impl.launch.knot.KnotClient',
                    'net.fabricmc.loader.launch.knot.KnotClient'):
        raise ValueError('Unsupported Java 8 Minecraft main class')
    if classpath_argument(words, main) != ';'.join('../../' + path for path in classpath):
        raise ValueError('Java 8 classpath differs from resolved dependencies')
    boundary = words.index(main)
    jvm, game = words[:boundary], words[boundary + 1:]
    indices = [i for i, word in enumerate(jvm) if word in ('-cp', '-classpath')]
    if len(indices) != 1:
        raise ValueError('Java 8 requires one -cp/-classpath option')
    jvm[indices[0] + 1] = '../../' + JAR
    command = windows_command([*jvm, MAIN])
    def string(value: str) -> bytes:
        data = value.encode('utf-8')
        if len(data) > 1048576:
            raise ValueError('Java 8 game argument is too long')
        return struct.pack('>I', len(data)) + data
    if len(game) > 10000:
        raise ValueError('Too many Java 8 game arguments')
    plan = struct.pack('>I', 0x50444a38) + string(main) + struct.pack('>I', len(game)) + b''.join(map(string, game))
    bootstrap = files('minecraft_playden').joinpath('resources/java8/Java8Launch.class').read_bytes()
    if hashlib.sha256(bootstrap).hexdigest() != BOOTSTRAP_SHA256:
        raise ValueError('Java 8 bootstrap resource checksum mismatch')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as jar:
        for name, data in [('META-INF/MANIFEST.MF', _manifest(classpath)), (CLASS, bootstrap), ('playden-launch.bin', plan)]:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            jar.writestr(info, data)
    return output.getvalue(), command


def write(root: Path, metadata: dict[str, Any], classpath: list[str]) -> str:
    archive, command = artifacts(metadata, classpath, (root / 'playden.args').read_text(encoding='utf-8'))
    for name, data in ((JAR, archive), (COMMAND, command.encode('utf-8'))):
        with safe_path(root, name).open('xb') as stream:
            stream.write(data)
    return command


def validate(root: Path, metadata: dict[str, Any], classpath: list[str], argfile: str) -> list[str]:
    archive, command = artifacts(metadata, classpath, argfile)
    errors = []
    if safe_path(root, JAR).read_bytes() != archive:
        errors.append('Java 8 adapter differs from resolved classpath, bootstrap or game arguments')
    if safe_path(root, COMMAND).read_bytes() != command.encode('utf-8'):
        errors.append('Java 8 command differs from preserved JVM arguments')
    return errors
