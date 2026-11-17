"""Discover/probe a host JDK for headless preparation; never acquire Java or use Wine."""
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping

from .operations import CancellationToken
from .process_environment import desktop_environment


@dataclass(frozen=True)
class HostJdk:
    executable: Path
    major: int


def candidates(packaged: Path | None = None, *, platform: str | None = None,
               environ: Mapping[str, str] | None = None, home: Path | None = None) -> list[Path]:
    platform = platform or sys.platform
    env = os.environ if environ is None else environ
    home = home or Path.home()
    binary = 'java.exe' if platform == 'win32' else 'java'
    paths = []
    if env.get('JAVA_HOME'):
        paths.append(Path(env['JAVA_HOME']) / 'bin' / binary)
    on_path = shutil.which(binary, path=env.get('PATH', ''))
    if on_path and not (platform == 'darwin' and on_path == '/usr/bin/java'):
        paths.append(Path(on_path))
    if platform == 'win32':
        if packaged is not None:
            paths.append(packaged)
        for name in ('ProgramFiles', 'ProgramW6432', 'LOCALAPPDATA'):
            if env.get(name):
                root = Path(env[name])
                for pattern in ('Java/*/bin/java.exe', 'Eclipse Adoptium/*/bin/java.exe', 'Microsoft/*/bin/java.exe', 'Zulu/*/bin/java.exe'):
                    paths.extend(sorted(root.glob(pattern), reverse=True)[:16])
    elif platform == 'darwin':
        for root in (Path('/Library/Java/JavaVirtualMachines'), home / 'Library/Java/JavaVirtualMachines'):
            paths.extend(sorted(root.glob('*/Contents/Home/bin/java'), reverse=True)[:16])
        for root in (Path('/opt/homebrew/opt'), Path('/usr/local/opt')):
            paths.extend(sorted(root.glob('openjdk*/bin/java'), reverse=True)[:16])
    else:
        for root, pattern in ((Path('/usr/lib/jvm'), '*/bin/java'), (Path('/usr/lib64/jvm'), '*/bin/java'),
                              (Path('/usr/java'), '*/bin/java'), (Path('/opt'), 'jdk*/bin/java')):
            paths.extend(sorted(root.glob(pattern), reverse=True)[:16])
    result = []
    seen: set[Path] = set()
    for path in paths:
        try:
            identity = path.resolve()
        except (OSError, RuntimeError):
            continue
        if identity not in seen and path.is_file():
            seen.add(identity)
            result.append(path)
    return result[:32]


def probe(executable: Path, cancellation: CancellationToken | None = None, *, timeout: float = 10) -> HostJdk:
    executable = executable.expanduser().absolute()
    if cancellation is not None:
        cancellation.check()
    if not executable.is_file():
        raise ValueError('Selected Java executable does not exist')
    with executable.open('rb') as stream:
        magic = stream.read(4)
    if (os.name != 'nt' and magic[:2] == b'MZ') or (os.name == 'nt' and magic[:2] != b'MZ') or (sys.platform == 'darwin' and magic == b'\x7fELF'):
        raise ValueError('Select a host-native JDK; the Windows Java runtime for PS5 cannot prepare on Linux/macOS')
    env = desktop_environment()
    for key in ('JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS', 'CLASSPATH'):
        env.pop(key, None)
    process = subprocess.Popen([str(executable), '-XshowSettings:properties', '--list-modules'],
                               env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        end = time.monotonic() + timeout
        while True:
            if cancellation is not None:
                cancellation.check()
            if time.monotonic() >= end:
                raise ValueError('Java validation timed out')
            try:
                output, _ = process.communicate(timeout=.1)
                break
            except subprocess.TimeoutExpired:
                pass
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    text = output.decode('utf-8', errors='replace')
    version = re.search(r'^\s*java\.version\s*=\s*(\S+)', text, re.M)
    major = re.match(r'(?:1\.)?(\d+)', version[1]) if version else None
    if process.returncode or major is None or int(major[1]) < 17:
        raise ValueError('Preparation requires a host-native JDK 17 or newer')
    if not re.search(r'^jdk\.compiler(?:@[^\r\n]+)?\s*$', text, re.M):
        raise ValueError('Java has no jdk.compiler module; select a full JDK 17+, not a JRE')
    operating_system = re.search(r'^\s*os\.name\s*=\s*([^\r\n]+)', text, re.M)
    expected = 'Windows' if os.name == 'nt' else 'Mac OS X' if sys.platform == 'darwin' else 'Linux'
    if operating_system is None or not operating_system[1].strip().startswith(expected):
        raise ValueError('Selected Java does not identify as a native JDK for this host')
    return HostJdk(executable, int(major[1]))


def find(executable: Path | None = None, packaged: Path | None = None,
         cancellation: CancellationToken | None = None) -> HostJdk:
    paths = [executable] if executable is not None else candidates(packaged)
    errors = []
    for path in paths:
        try:
            return probe(path, cancellation)
        except (OSError, ValueError) as exc:
            errors.append(f'{path}: {exc}')
    detail = '\n'.join(errors[-5:])
    raise ValueError('No suitable host-native JDK found for Fabric preparation. Select a Java executable in '
                     'the Host preparation JDK field (JDK 17+, including jdk.compiler), or use --remap-java PATH. '
                     'The Windows runtime packaged for PS5 is separate. No Java will be downloaded and no Wine will be used.'
                     + ('\n' + detail if detail else ''))
