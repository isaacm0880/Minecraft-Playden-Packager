"""Explicit launch profiles; default Mojang behavior stays unchanged.

reference-26.3 reproduces the supplied console-tested installation's configuration
for a separate diagnostic build. It is not a claim of new PS5 compatibility.
"""
import copy
from typing import Any
from . import dependencies

DEFAULT_PROFILE = 'official'
REFERENCE_PROFILE = 'reference-26.3'
PROFILES = (DEFAULT_PROFILE, REFERENCE_PROFILE)

# Published Mojang library mirror URLs and SHA-1 values verified against the
# read-only reference's LWJGL 3.4.3 metadata and its installed JARs.
SPNG_LIBRARIES = [
    {'name': 'org.lwjgl:lwjgl-spng:3.4.3', 'downloads': {'artifact': {
        'path': 'org/lwjgl/lwjgl-spng/3.4.3/lwjgl-spng-3.4.3.jar',
        'url': 'https://libraries.minecraft.net/org/lwjgl/lwjgl-spng/3.4.3/lwjgl-spng-3.4.3.jar',
        'sha1': '3a134111ce70d69463278a2a9ab5a20c84cd6484', 'size': 120677}}},
    {'name': 'org.lwjgl:lwjgl-spng:3.4.3:natives-windows', 'downloads': {'artifact': {
        'path': 'org/lwjgl/lwjgl-spng/3.4.3/lwjgl-spng-3.4.3-natives-windows.jar',
        'url': 'https://libraries.minecraft.net/org/lwjgl/lwjgl-spng/3.4.3/lwjgl-spng-3.4.3-natives-windows.jar',
        'sha1': 'ac816c4521d282440f9867be23ae2e076bae7c15', 'size': 108983}}},
]


def check_profile(metadata: dict[str, Any], profile: str) -> None:
    if profile not in PROFILES:
        raise ValueError(f'Unknown launch profile: {profile}')
    if profile == REFERENCE_PROFILE:
        if metadata['id'] != '26.3' or metadata['mainClass'] != 'net.minecraft.client.main.Main':
            raise ValueError('reference-26.3 is only for vanilla Minecraft 26.3')
        if not any(library['name'] == 'org.lwjgl:lwjgl:3.4.3' for library in metadata['libraries']):
            raise ValueError('reference-26.3 requires the reference LWJGL 3.4.3 dependency set')


def effective_metadata(metadata: dict[str, Any], profile: str = DEFAULT_PROFILE) -> dict[str, Any]:
    check_profile(metadata, profile)
    result = copy.deepcopy(metadata)
    if profile == REFERENCE_PROFILE:
        names = {library['name'] for library in result['libraries']}
        for library in SPNG_LIBRARIES:
            if library['name'] not in names:
                result['libraries'].append(copy.deepcopy(library))
    return result


def client_path(metadata: dict[str, Any], profile: str = DEFAULT_PROFILE) -> str:
    check_profile(metadata, profile)
    if profile == REFERENCE_PROFILE:
        return 'libraries/com/mojang/minecraft/26.3/minecraft-26.3-client.jar'
    return 'versions/client.jar'


def game_directory(profile: str = DEFAULT_PROFILE) -> str:
    return 'instances/test/minecraft' if profile == REFERENCE_PROFILE else 'instances/default/minecraft'


def classpath(metadata: dict[str, Any], profile: str = DEFAULT_PROFILE) -> list[str]:
    effective = effective_metadata(metadata, profile)
    if profile == DEFAULT_PROFILE:
        return dependencies.classpath(effective)
    # The reference combines ordinary Minecraft dependencies, then LWJGL's Java
    # libraries, then the client, and appends Netty Unix-common (a Java dependency).
    java = ['libraries/' + spec['path'] for spec, native, _ in dependencies.resolve(effective) if not native]
    lwjgl = sorted(path for path in java if path.startswith('libraries/org/lwjgl/'))
    unix_common = [path for path in java if '/netty-transport-native-unix-common/' in path]
    ordinary = sorted(path for path in java if path not in lwjgl and path not in unix_common)
    return ordinary + lwjgl + [client_path(metadata, profile)] + unix_common
