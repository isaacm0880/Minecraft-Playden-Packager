"""Loopback integration against the actual receiver C sources; no PS5 contacted."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import subprocess
import threading
import time

import pytest

from minecraft_playden import recipe
from minecraft_playden.network_protocol import (
    BEGIN, BEGIN_INFO, COMMIT, DATA, END_FILE, ENTRY, ENTRY_INFO, HEADER,
    MAX_FRAME, VERIFY, VERIFY_PROGRESS, ACK, NetworkTransferError, Session, pairing_key,
)
from minecraft_playden.operations import CancellationToken


@pytest.fixture(scope='session')
def receiver_binary(tmp_path_factory):
    if os.name != 'posix' or not shutil.which('cc'):
        pytest.skip('Receiver host integration requires a POSIX C compiler; Python protocol tests run everywhere')
    binary = tmp_path_factory.mktemp('receiver-build') / 'receiver'
    root = Path(__file__).resolve().parents[1]
    flags = ['-O2']
    if os.environ.get('PLAYDEN_RECEIVER_SANITIZE') == '1':
        flags = ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    subprocess.run(['cc', '-std=c11', '-DHOST_TEST', '-Wall', '-Wextra', '-Werror', *flags,
                    str(root / 'ps5/receiver/main.c'), str(root / 'ps5/receiver/vendor/sha-256.c'),
                    '-o', str(binary)], check=True, capture_output=True)
    return binary


@pytest.fixture
def receiver(receiver_binary, tmp_path):
    @contextlib.contextmanager
    def start(*options, usb=True):
        sandbox = tmp_path / f'console-{len(list(tmp_path.glob("console-*")))}'
        sandbox.mkdir()
        if usb:
            (sandbox / 'usb0').mkdir()
        process = subprocess.Popen([str(receiver_binary), '--bind', '127.0.0.1', '--port', '0',
                                    '--test-root', str(sandbox), *options],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        lines = queue.Queue()
        reader = threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            line = lines.get(timeout=10)
            match = re.fullmatch(r'PDLAN/1 address=127.0.0.1 port=(\d+) token=([0-9a-f]{64})\n', line)
            assert match, 'Receiver did not start successfully'
            port, token = int(match[1]), match[2]
            yield sandbox, port, token
        finally:
            process.terminate()
            try:
                output, error = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                output, error = process.communicate(timeout=3)
            reader.join(1)
            assert 'ERROR: AddressSanitizer' not in error and 'runtime error:' not in error, error
            assert process.returncode == 0, 'Receiver exited abnormally: ' + error
    return start


@contextlib.contextmanager
def paired(port, token):
    with socket.create_connection(('127.0.0.1', port), timeout=2) as stream:
        session = Session(stream, CancellationToken(), timeout=2)
        session.authenticate(pairing_key(token))
        yield session


def begin(session, count, total, *, destination=0, name='Package'):
    encoded = name.encode()
    session.send(BEGIN, BEGIN_INFO.pack(destination, len(encoded), count, total) + encoded)
    return session.ack()


def entry(session, path, data=None, *, kind=None, digest=None):
    encoded = path.encode('utf-8') if isinstance(path, str) else path
    is_file = int(data is not None) if kind is None else kind
    size = len(data) if data is not None else 0
    digest = hashlib.sha256(data).digest() if data is not None and digest is None else digest or bytes(32)
    session.send(ENTRY, ENTRY_INFO.pack(is_file, len(encoded), size) + digest + encoded)
    session.ack()
    if data is not None:
        for offset in range(0, len(data), MAX_FRAME):
            session.send(DATA, data[offset:offset + MAX_FRAME])
        session.send(END_FILE)
        session.ack()


def verify(session):
    session.send(VERIFY)
    while True:
        kind, data = session.receive()
        if kind == ACK:
            assert data == b'verified'
            return
        assert kind == VERIFY_PROGRESS


def contents():
    files = {
        'recipe.json': json.dumps(recipe.generate('Minecraft fixture')).encode(),
        'playden.args': b'-Xmx2G\n', 'java/bin/java.exe': b'MZfixture',
        'java/bin/server/jvm.dll': b'MZjvm', 'java/release': b'fixture-runtime',
        'metadata/version.json': b'{"id":"fixture"}',
        'instances/default/minecraft/saves/世界/level.dat': b'world-fixture',
    }
    return files


def inventory(files):
    return json.dumps({'schema': 2, 'launch_profile': 'official', 'source': 'local',
                       'version': 'fixture', 'username': 'Player', 'classpath': [],
                       'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()
                                 if not name.startswith('instances/')}}).encode()


def upload(session, files, *, destination=0, name='Package'):
    directories = {str(parent).replace('\\', '/') for path in files
                   for parent in Path(path).parents if str(parent) != '.'}
    begin(session, len(files) + len(directories), sum(map(len, files.values())), destination=destination, name=name)
    for path in sorted(directories):
        entry(session, path)
    for path, data in sorted(files.items()):
        entry(session, path, data)


def wait_clean(sandbox):
    def stages():
        return list((sandbox / 'internal').glob('.pdlan-*')) + list((sandbox / 'usb0/playden/import').glob('.pdlan-*'))
    deadline = time.monotonic() + 3
    while stages() and time.monotonic() < deadline:
        time.sleep(.01)
    assert not stages()


@pytest.mark.parametrize('destination,subroot', [(0, 'internal'), (1, 'usb0/playden/import')])
def test_real_receiver_verifies_complete_packages_and_never_overwrites(receiver, destination, subroot):
    files = contents()
    # Exercise SHA-256 padding boundaries and multi-frame files across C/Python.
    for size in [0, 1, 55, 56, 63, 64, 65, 128, MAX_FRAME * 2 + 9]:
        files[f'extras/{size}'] = bytes(i % 251 for i in range(size))
    files['package.json'] = inventory(files)
    with receiver() as (sandbox, port, token):
        with paired(port, token) as session:
            upload(session, files, destination=destination)
            assert not (sandbox / subroot / 'Package').exists()
            verify(session)
            assert not (sandbox / subroot / 'Package').exists()
            session.send(COMMIT)
            assert session.ack().decode().endswith('/Package')
        output = sandbox / subroot / 'Package'
        assert {p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob('*') if p.is_file()} == files
        with paired(port, token) as session, pytest.raises(NetworkTransferError, match='already exists'):
            upload(session, files, destination=destination)
        wait_clean(sandbox)
        assert (output / 'recipe.json').read_bytes() == files['recipe.json']


@pytest.mark.parametrize('path', ['../escape', '/absolute', 'a/../../b', 'C:/file', 'x\\y', 'a\x00b', 'a//b', '.', 'NUL', 'x.', b'bad\xff'])
def test_receiver_independently_rejects_unsafe_paths(receiver, path):
    with receiver() as (sandbox, port, token), paired(port, token) as session:
        begin(session, 1, 0)
        with pytest.raises(NetworkTransferError):
            entry(session, path)
    wait_clean(sandbox)
    assert not (sandbox / 'internal/Package').exists()


@pytest.mark.parametrize('fault', ['duplicate', 'case', 'symlink-type', 'hash', 'underflow', 'early-commit'])
def test_receiver_rejects_invalid_entry_state(receiver, fault):
    with receiver() as (sandbox, port, token), paired(port, token) as session:
        begin(session, 2, 10)
        with pytest.raises(NetworkTransferError):
            if fault in ('duplicate', 'case'):
                entry(session, 'Folder')
                entry(session, 'Folder' if fault == 'duplicate' else 'folder')
            elif fault == 'symlink-type':
                entry(session, 'link', kind=2)
            elif fault == 'hash':
                entry(session, 'file', b'bytes', digest=bytes(32))
            elif fault == 'underflow':
                session.send(ENTRY, ENTRY_INFO.pack(1, 4, 10) + hashlib.sha256(b'x').digest() + b'file')
                session.ack()
                session.send(DATA, b'x')
                session.send(END_FILE)
                session.ack()
            else:
                session.send(COMMIT)
                session.ack()
    wait_clean(sandbox)


@pytest.mark.parametrize('fault', ['recipe', 'traversal-recipe', 'extra-launch', 'duplicate-key', 'inventory'])
def test_receiver_rejects_invalid_recipe_and_inventory(receiver, fault):
    files = contents()
    document = json.loads(files['recipe.json'])
    if fault == 'recipe':
        files['recipe.json'] = b'{"incomplete":true}'
    elif fault == 'traversal-recipe':
        document['launch_entries'][0]['executable'] = '../../escape'
        files['recipe.json'] = json.dumps(document).encode()
    elif fault == 'extra-launch':
        document['launch_entries'].append({'executable': '/unsafe'})
        files['recipe.json'] = json.dumps(document).encode()
    elif fault == 'duplicate-key':
        files['recipe.json'] = files['recipe.json'][:-1] + b',"executable":"java/bin/java.exe"}'
    files['package.json'] = inventory(files)
    if fault == 'inventory':
        files['package.json'] = files['package.json'].replace(hashlib.sha256(files['playden.args']).hexdigest().encode(), b'0' * 64)
    with receiver() as (sandbox, port, token), paired(port, token) as session:
        upload(session, files)
        with pytest.raises(NetworkTransferError, match='Invalid recipe'):
            verify(session)
    wait_clean(sandbox)


@pytest.mark.parametrize('attack', ['root', 'stage-parent', 'stage-file', 'corrupt-file'])
def test_symlinks_and_changed_staging_are_rejected_without_touching_targets(receiver, tmp_path, attack):
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'sentinel'
    sentinel.write_bytes(b'keep')
    files = contents()
    files['package.json'] = inventory(files)
    with receiver() as (sandbox, port, token):
        if attack == 'root':
            (sandbox / 'internal').symlink_to(outside, target_is_directory=True)
        with paired(port, token) as session:
            if attack == 'root':
                with pytest.raises(NetworkTransferError, match='unsafe'):
                    upload(session, files)
            elif attack == 'stage-parent':
                begin(session, 1, 1)
                stage = next(sandbox.glob('internal/.pdlan-*/package'))
                (stage / 'linked').symlink_to(outside, target_is_directory=True)
                with pytest.raises(NetworkTransferError, match='unsafe'):
                    entry(session, 'linked/escape', b'x')
            else:
                upload(session, files)
                stage = next(sandbox.glob('internal/.pdlan-*/package'))
                target = stage / 'playden.args'
                if attack == 'stage-file':
                    target.unlink()
                    target.symlink_to(sentinel)
                else:
                    target.write_bytes(b'x' * target.stat().st_size)
                with pytest.raises(NetworkTransferError, match='Invalid recipe'):
                    verify(session)
    assert sentinel.read_bytes() == b'keep'
    assert sorted(p.name for p in outside.iterdir()) == ['sentinel']
    wait_clean(sandbox)


def test_storage_preflight_and_usb_unavailable_never_fall_back(receiver):
    with receiver('--test-free-bytes', '0') as (sandbox, port, token), paired(port, token) as session:
        with pytest.raises(NetworkTransferError, match='Insufficient storage'):
            begin(session, 1, 100)
        assert not list(sandbox.rglob('.pdlan-*'))
    with receiver(usb=False) as (sandbox, port, token), paired(port, token) as session:
        with pytest.raises(NetworkTransferError, match='no internal fallback'):
            begin(session, 1, 100, destination=1)
        assert not (sandbox / 'internal').exists()


def test_actual_write_space_failure_discards_staging(receiver):
    with receiver('--test-write-limit', '0') as (sandbox, port, token), paired(port, token) as session:
        begin(session, 1, 10)
        session.send(ENTRY, ENTRY_INFO.pack(1, 4, 10) + hashlib.sha256(b'0123456789').digest() + b'file')
        session.ack()
        session.send(DATA, b'0123456789')
        with pytest.raises(NetworkTransferError, match='Insufficient storage'):
            session.ack()
    wait_clean(sandbox)
    assert not (sandbox / 'internal/Package').exists()


def test_disconnect_after_verification_still_does_not_publish(receiver):
    files = contents()
    files['package.json'] = inventory(files)
    with receiver() as (sandbox, port, token):
        with paired(port, token) as session:
            upload(session, files)
            verify(session)
        wait_clean(sandbox)
        assert not (sandbox / 'internal/Package').exists()


def test_disconnect_discards_private_staging_and_publishes_nothing(receiver):
    with receiver() as (sandbox, port, token):
        with paired(port, token) as session:
            begin(session, 1, 1024)
            session.send(ENTRY, ENTRY_INFO.pack(1, 4, 1024) + bytes(32) + b'file')
            session.ack()
            session.send(DATA, b'incomplete')
        wait_clean(sandbox)
        assert list((sandbox / 'internal').iterdir()) == []


def test_pairing_failure_creates_no_directories(receiver):
    with receiver() as (sandbox, port, token):
        with pytest.raises(NetworkTransferError), paired(port, '0' * 64):
            pass
        assert sorted(p.name for p in sandbox.iterdir()) == ['usb0']


@pytest.mark.parametrize('attack', ['tamper', 'replay', 'oversized'])
def test_c_receiver_checks_frame_authentication_order_and_bounds(receiver, attack):
    with receiver() as (sandbox, port, token), paired(port, token) as session:
        if attack == 'replay':
            begin(session, 1, 0)
            session.sent = 0
            session.send(ENTRY, ENTRY_INFO.pack(0, 1, 0) + bytes(32) + b'a')
        elif attack == 'tamper':
            session.write(HEADER.pack(BEGIN, 1, 0) + b'x' + bytes(32))
        else:
            session.write(HEADER.pack(BEGIN, MAX_FRAME + 1, 0))
        with pytest.raises(NetworkTransferError):
            session.ack()
    wait_clean(sandbox)
