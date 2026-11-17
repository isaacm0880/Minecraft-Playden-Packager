"""Synthetic PE headers for structural checks; these are never executed."""
import struct


def pe(machine=0x8664):
    data = bytearray(96)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 64)
    data[64:68] = b'PE\0\0'
    struct.pack_into('<H', data, 68, machine)
    return bytes(data)


def runtime(root, major, os_name='Windows', architecture='x86_64', machine=0x8664):
    (root / 'bin/server').mkdir(parents=True)
    (root / 'bin/java.exe').write_bytes(pe(machine))
    (root / 'bin/server/jvm.dll').write_bytes(pe(machine))
    version = '1.8.0_472' if major == 8 else f'{major}.0.1'
    (root / 'release').write_text(f'JAVA_VERSION="{version}"\nOS_NAME="{os_name}"\nOS_ARCH="{architecture}"\n')
    return root


def install_runtime(metadata, downloader, java):
    from minecraft_playden.metadata import java_major
    from minecraft_playden.runtime import inspect
    major = java_major(metadata)
    runtime(java, major)
    return inspect(java, major)
