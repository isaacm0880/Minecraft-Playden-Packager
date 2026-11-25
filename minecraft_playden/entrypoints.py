"""Validate declared legacy launch indirection without guessing or running classes."""
import struct
from pathlib import Path
from typing import Any
import zipfile

from .dependencies import allowed

LEGACY_MAIN = 'net.minecraft.launchwrapper.Launch'
LEGACY_CLIENT = 'net.minecraft.client.Minecraft'
LEGACY_CLASSES = (
    'net/minecraft/launchwrapper/Launch.class',
    'net/minecraft/launchwrapper/VanillaTweaker.class',
    'net/minecraft/launchwrapper/injector/VanillaTweakInjector.class',
)
MAX_CLASS_BYTES = 16 * 1024 * 1024


def legacy_wrapper(metadata: dict[str, Any]) -> str | None:
    """Recognize the metadata's official VanillaTweaker contract, not a version list."""
    if metadata['mainClass'] != LEGACY_MAIN:
        return None
    from .metadata import java_major
    if java_major(metadata) != 8:
        raise ValueError('Legacy LaunchWrapper requires the metadata-selected Java 8 runtime')
    if metadata.get('assetIndex', {}).get('id') != 'pre-1.6':
        raise ValueError('Legacy LaunchWrapper requires the pre-1.6 resource layout')
    matches = [library for library in metadata['libraries']
               if ':'.join(library['name'].split(':')[:2]) == 'net.minecraft:launchwrapper'
               and allowed(library)]
    if len(matches) != 1:
        raise ValueError('Legacy LaunchWrapper requires exactly one declared Windows launchwrapper dependency')
    artifact = matches[0].get('downloads', {}).get('artifact', {})
    if not artifact.get('sha1') or not artifact.get('path'):
        raise ValueError('Legacy LaunchWrapper dependency must have a verified artifact')
    return 'libraries/' + artifact['path']


def client_main(metadata: dict[str, Any]) -> str:
    return LEGACY_CLIENT if legacy_wrapper(metadata) is not None else metadata['mainClass']


def check_assets(metadata: dict[str, Any], index: dict[str, Any]) -> None:
    if legacy_wrapper(metadata) is not None and index.get('map_to_resources') is not True:
        raise ValueError('Legacy LaunchWrapper requires a mapped pre-1.6 resource index')


def class_contract(data: bytes, expected_name: str, *, game_directory: bool = False) -> None:
    """Bounded class-file structure/method check; does not load or execute bytecode."""
    if len(data) > MAX_CLASS_BYTES:
        raise ValueError('Legacy entry-point class exceeds the inspection limit')
    position = 0
    def take(size: int) -> bytes:
        nonlocal position
        if size < 0 or size > len(data) - position:
            raise ValueError('Truncated legacy entry-point class')
        result = data[position:position + size]
        position += size
        return result
    def u2() -> int:
        return struct.unpack('>H', take(2))[0]
    def u4() -> int:
        return struct.unpack('>I', take(4))[0]
    if take(4) != b'\xca\xfe\xba\xbe':
        raise ValueError('Legacy entry point is not a Java class')
    u2()  # minor version
    major = u2()
    if not 45 <= major <= 52:
        raise ValueError('Legacy entry point requires a Java class compatible with Java 8')
    pool: list[Any] = [None] * u2()
    index = 1
    while index < len(pool):
        tag = take(1)[0]
        if tag == 1:
            # Only ASCII class/method/descriptor names below are interpreted.
            pool[index] = (tag, take(u2()).decode('utf-8', errors='replace'))
        elif tag in (7, 8, 16):
            pool[index] = (tag, u2())
        elif tag in (3, 4, 9, 10, 11, 12, 18):
            take(4)
        elif tag in (5, 6):
            take(8)
            if index + 1 >= len(pool):
                raise ValueError('Invalid two-slot legacy constant-pool entry')
            index += 1
        elif tag == 15:
            take(3)
        else:
            raise ValueError('Unsupported legacy class constant-pool entry')
        index += 1
    def entry(number: int, tag: int) -> Any:
        if not 0 < number < len(pool) or pool[number] is None or pool[number][0] != tag:
            raise ValueError('Invalid legacy class constant-pool reference')
        return pool[number][1]
    def text(number: int) -> str:
        return str(entry(number, 1))
    access = u2()
    name = text(entry(u2(), 7))
    if name != expected_name.replace('.', '/') or not access & 0x0001:
        raise ValueError('Legacy entry-point class name or public access does not match')
    u2()  # superclass
    take(u2() * 2)  # interfaces
    def attributes() -> dict[str, bytes]:
        names = {}
        for _ in range(u2()):
            name = text(u2())
            if name == 'Code' and name in names:
                raise ValueError('Duplicate legacy method Code attribute')
            names[name] = take(u4())
        return names
    def has_body(code: bytes) -> bool:
        if len(code) < 12:
            return False
        length = struct.unpack_from('>I', code, 4)[0]
        locals_count = struct.unpack_from('>H', code, 2)[0]
        if not locals_count or not 0 < length <= 65535 or length > len(code) - 12:
            return False
        position = 8 + length
        exceptions = struct.unpack_from('>H', code, position)[0]
        position += 2 + exceptions * 8
        if position + 2 > len(code):
            return False
        count = struct.unpack_from('>H', code, position)[0]
        position += 2
        for _ in range(count):
            if position + 6 > len(code):
                return False
            text(struct.unpack_from('>H', code, position)[0])
            position += 6 + struct.unpack_from('>I', code, position + 2)[0]
            if position > len(code):
                return False
        return position == len(code)
    directories = 0
    for _ in range(u2()):
        flags, _, descriptor = u2(), u2(), text(u2())
        if flags & 0x0008 and descriptor == 'Ljava/io/File;':
            directories += 1
        attributes()
    found = 0
    for _ in range(u2()):
        flags, name, descriptor = u2(), text(u2()), text(u2())
        names = attributes()
        if name == 'main' and descriptor == '([Ljava/lang/String;)V':
            if flags & 0x0009 == 0x0009 and not flags & (0x0400 | 0x0100) and has_body(names.get('Code', b'')):
                found += 1
            else:
                raise ValueError('Legacy entry point requires a public static main method body')
    attributes()
    if position != len(data) or found != 1:
        raise ValueError('Legacy entry point requires public static void main(String[]) with a method body')
    if game_directory and directories != 1:
        raise ValueError('Legacy VanillaTweaker requires one unambiguous static File game-directory field')


def verify_legacy_class(archive: zipfile.ZipFile, name: str, *, game_directory: bool = False) -> None:
    member = name.replace('.', '/') + '.class'
    matches = [item for item in archive.infolist() if item.filename == member]
    if len(matches) != 1 or not 0 < matches[0].file_size <= MAX_CLASS_BYTES:
        raise ValueError(f'Legacy entry-point class missing, duplicated or oversized: {member}')
    class_contract(archive.read(matches[0]), name, game_directory=game_directory)
