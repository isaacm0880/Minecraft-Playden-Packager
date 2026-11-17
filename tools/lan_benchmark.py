"""Reproducible development benchmark. No payload deployment or remote deletion.

Prepare offline fixtures once, then reuse the same dataset for all comparisons.
Loopback invokes the C receiver only inside owned temporary directories. Remote
mode must be explicitly selected and publishes fresh packages on the user's PS5.
"""
import argparse
import contextlib
import getpass
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
from minecraft_playden.network_transfer import NetworkRequest
from minecraft_playden.network_protocol import MAX_FRAME


def fingerprint(package: Path) -> str:
    result = hashlib.sha256()
    for path in sorted(package.rglob('*')):
        if path.is_symlink():
            raise ValueError('Benchmark datasets must not contain links.')
        if path.is_file():
            relative = path.relative_to(package).as_posix().encode()
            with path.open('rb') as handle:
                digest = hashlib.file_digest(handle, 'sha256').digest()
            result.update(len(relative).to_bytes(4, 'big') + relative + digest)
    return result.hexdigest()


def prepare(output: Path, large_mib: int, small_count: int) -> None:
    # Dev-only helper performs a real export with offline dependency fixtures.
    sys.path.insert(0, str(REPOSITORY / 'tests'))
    from lan_fixtures import build_package
    output.mkdir()  # Never reuse or remove an existing dataset.
    for label in ('large', 'small'):
        directory = output / label
        directory.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            package = build_package(directory)
        target = package / 'instances/default/minecraft/benchmark'
        target.mkdir()
        block = bytes(range(256)) * 4096
        if label == 'large':
            with (target / 'large.bin').open('wb') as handle:
                for _ in range(large_mib):
                    handle.write(block)
        else:
            for index in range(small_count):
                (target / f'{index:06d}.bin').write_bytes(block[:4096])
        print(json.dumps({'dataset': label, 'sha256': fingerprint(package)}))


@contextlib.contextmanager
def loopback(binary: Path, sandbox: Path, buffer: int | None, diagnostics: bool, unbuffered: bool = False):
    options = ['--socket-buffer', str(buffer)] if buffer else []
    if diagnostics:
        options.append('--diagnostics')
    if unbuffered:
        options.append('--unbuffered-frames')
    process = subprocess.Popen([str(binary), '--test-root', str(sandbox), '--bind', '127.0.0.1',
                                '--port', '0', '--test-legacy-token', '--once', *options], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    # Drain continuously: diagnostics must never block a payload on a full pipe.
    lines: list[str] = []
    ready: queue.Queue[str] = queue.Queue()
    def read():
        assert process.stdout is not None
        ready.put(process.stdout.readline())
        lines.extend(process.stdout.readlines())
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        banner = ready.get(timeout=10)
        match = re.fullmatch(r'PDLAN/1 address=127.0.0.1 port=(\d+) token=([0-9a-f]{64})\n', banner)
        if not match:
            raise RuntimeError('Receiver failed to start')
        yield int(match[1]), match[2], lines
        if process.wait(timeout=20) != 0:
            raise RuntimeError('Receiver failed')
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(5)
        assert process.stderr is not None
        error = process.stderr.read()
        if error:
            raise RuntimeError(error)


def run(args) -> None:
    host = args.host if args.mode == 'remote' else '127.0.0.1'
    token = (os.environ.get('PLAYDEN_PS5_TOKEN') or getpass.getpass('Receiver pairing token: ')
             if args.mode == 'remote' else '')
    with tempfile.TemporaryDirectory(prefix='playden-benchmark-', dir=getattr(args, 'scratch_dir', None)) as temporary:
        scratch = Path(temporary).resolve()
        binary = args.receiver_binary
        if args.mode == 'loopback' and binary is None:
            binary = scratch / 'receiver'
            subprocess.run(['cc', '-std=c11', '-DHOST_TEST', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(REPOSITORY / 'ps5/receiver/main.c'),
                            str(REPOSITORY / 'ps5/receiver/vendor/sha-256.c'), '-o', str(binary)], check=True)
        for label in args.datasets:
            package = args.dataset / label / 'Minecraft Package'
            digest = fingerprint(package)
            for repeat in range(args.repeat):
                sandbox = scratch / f'{label}-{repeat}'
                sandbox.mkdir()
                (sandbox / 'usb0').mkdir()
                context = (loopback(binary.resolve(), sandbox, args.socket_buffer, not args.legacy_receiver and not args.no_receiver_diagnostics, args.unbuffered_frames)
                           if args.mode == 'loopback' else contextlib.nullcontext((args.port, token, [])))
                with context as (port, pairing, receiver_lines):
                    metrics = {}
                    phases = {}
                    started, cpu = time.perf_counter(), time.process_time()
                    def progress(event):
                        phases.setdefault(event.stage, time.perf_counter() - started)
                    result = NetworkRequest(package, host, pairing, args.destination, port,
                                            f'LAN-Bench-{label}-{uuid.uuid4().hex[:12]}',
                                            args.socket_buffer, args.chunk_size, args.pipeline_files).run(progress, diagnostics=metrics.update)
                    elapsed, cpu_elapsed = time.perf_counter() - started, time.process_time() - cpu
                receiver_metrics = result.receiver_metrics or {}
                for line in receiver_lines:
                    if not result.receiver_metrics and line.startswith('receiver_metrics '):
                        receiver_metrics = {key: float(value) for key, value in
                                            (item.split('=') for item in line.split()[1:])}
                record = {'dataset': label, 'dataset_sha256': digest, 'repeat': repeat,
                          'mode': args.mode, 'scratch_mode': 'explicit' if getattr(args, 'scratch_dir', None) else 'system_temp',
                          'destination': args.destination, 'python': platform.python_version(),
                          'os': platform.system(), 'chunk_size': args.chunk_size, 'socket_buffer': args.socket_buffer,
                          'pipeline_files': args.pipeline_files,
                          'unbuffered_frames': getattr(args, 'unbuffered_frames', False),
                          'receiver_diagnostics': (not args.no_receiver_diagnostics and not args.legacy_receiver
                                                   if args.mode == 'loopback' else None),
                          'bytes': result.bytes_copied, 'files': result.files, 'wall_s': elapsed,
                          'sender_cpu_s': cpu_elapsed, 'mib_s': result.bytes_copied / elapsed / 1048576,
                          'phase_start_s': phases, 'sender': metrics, 'receiver': receiver_metrics}
                print(json.dumps(record, sort_keys=True), flush=True)
                with args.output.open('a', encoding='utf-8') as output:
                    output.write(json.dumps(record, sort_keys=True) + '\n')
                if fingerprint(package) != digest:
                    raise ValueError('Benchmark source changed during transfer')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    prepare_parser = commands.add_parser('prepare', help='Create offline synthetic datasets; no network')
    prepare_parser.add_argument('output', type=Path)
    prepare_parser.add_argument('--large-mib', type=int, default=32)
    prepare_parser.add_argument('--small-count', type=int, default=300)
    for mode in ('loopback', 'remote'):
        sub = commands.add_parser(mode)
        sub.add_argument('dataset', type=Path)
        sub.add_argument('--datasets', choices=['large', 'small'], nargs='+', default=['large', 'small'])
        sub.add_argument('--repeat', type=int, default=3)
        sub.add_argument('--output', required=True, type=Path, help='Append JSONL numeric results')
        sub.add_argument('--destination', choices=['internal', 'usb0'], default='internal')
        sub.add_argument('--socket-buffer', type=int)
        sub.add_argument('--chunk-size', type=int, default=MAX_FRAME)
        sub.add_argument('--pipeline-files', action='store_true')
        if mode == 'remote':
            sub.add_argument('--host', required=True)
            sub.add_argument('--port', type=int, default=9025)
        else:
            sub.add_argument('--no-receiver-diagnostics', action='store_true', help='Compare receiver instrumentation overhead on host')
            sub.add_argument('--unbuffered-frames', action='store_true', help='Compare the original exact-part reader with the same instrumented receiver')
            sub.add_argument('--scratch-dir', type=Path, help='Existing filesystem directory for owned host staging (default system temp)')
            sub.add_argument('--receiver-binary', type=Path)
            sub.add_argument('--legacy-receiver', action='store_true', help='Receiver lacks --diagnostics (comparison only)')
    args = parser.parse_args()
    if args.mode == 'prepare':
        if not 1 <= args.large_mib <= 4096 or not 1 <= args.small_count <= 10000:
            parser.error('Dataset bounds: 1..4096 MiB; 1..10000 small files')
        prepare(args.output, args.large_mib, args.small_count)
    else:
        if not 1 <= args.repeat <= 100:
            parser.error('Repeat must be 1..100')
        run(args)


if __name__ == '__main__':
    main()
