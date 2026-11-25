"""Loopback integration against the actual receiver C sources; no PS5 contacted."""
import contextlib
import errno
import hashlib
import hmac
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
    def start(*options, usb=True, sandbox_root=None):
        sandbox = sandbox_root if sandbox_root is not None else tmp_path / f'console-{len(list(tmp_path.glob("console-*")))}'
        sandbox.mkdir(exist_ok=sandbox_root is not None)
        if usb:
            (sandbox / 'usb0').mkdir(exist_ok=sandbox_root is not None)
        process = subprocess.Popen([str(receiver_binary), '--bind', '127.0.0.1', '--port', '0', '--test-legacy-token',
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
            start.diagnostics.append(output)
            assert 'ERROR: AddressSanitizer' not in error and 'runtime error:' not in error, error
            assert process.returncode == 0, 'Receiver exited abnormally: ' + error
    start.diagnostics = []
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
        found = []
        for relative in ('playden-packager-temp', 'usb0/playden-packager-temp'):
            try:
                found.extend((sandbox / relative).glob('.pdlan-*'))
            except FileNotFoundError:
                pass  # The receiver may remove this empty parent during scandir.
        return found
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
                stage = next(sandbox.glob('playden-packager-temp/.pdlan-*/package'))
                (stage / 'linked').symlink_to(outside, target_is_directory=True)
                with pytest.raises(NetworkTransferError, match='unsafe'):
                    entry(session, 'linked/escape', b'x')
            else:
                upload(session, files)
                stage = next(sandbox.glob('playden-packager-temp/.pdlan-*/package'))
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


def test_recorded_frame_cannot_be_replayed_in_a_new_paired_session(receiver):
    with receiver() as (sandbox, port, token):
        with paired(port, token) as first:
            body = BEGIN_INFO.pack(0, 7, 1, 0) + b'Package'
            header = HEADER.pack(BEGIN, len(body), 0)
            recorded = header + body + hmac.digest(first.key, b'C' + header + body, 'sha256')
        with paired(port, token) as second:
            second.write(recorded)
            with pytest.raises(NetworkTransferError):
                second.ack()
        assert not (sandbox / 'internal').exists()


def test_replaced_stage_root_is_rejected_at_commit(receiver, tmp_path):
    files = contents()
    files['package.json'] = inventory(files)
    outside = tmp_path / 'outside-stage'
    outside.mkdir()
    (outside / 'sentinel').write_bytes(b'keep')
    with receiver() as (sandbox, port, token), paired(port, token) as session:
        upload(session, files)
        verify(session)
        stage = next(sandbox.glob('playden-packager-temp/.pdlan-*/package'))
        stage.rename(stage.with_name('original'))
        stage.symlink_to(outside, target_is_directory=True)
        session.send(COMMIT)
        with pytest.raises(NetworkTransferError, match='Publication failed'):
            session.ack()
    wait_clean(sandbox)
    assert (outside / 'sentinel').read_bytes() == b'keep'
    assert sorted(p.name for p in outside.iterdir()) == ['sentinel']


@pytest.mark.parametrize('result,raw_errno,expected',
    [(-1, code, code) for code in (errno.EACCES, errno.EPERM, errno.EINVAL, errno.EIO,
                                  errno.ENOSYS, errno.ENOTDIR, errno.ELOOP, errno.EBADF)] +
    [(code, errno.ENOENT, code) for code in (errno.EACCES, errno.EINVAL, errno.EIO, errno.ENOSYS)] +
    [(-1, 0, errno.EIO), (-2, errno.ENOENT, errno.EIO)])
def test_stat_errors_are_not_existing_packages_or_available_destinations(receiver, result, raw_errno, expected):
    with receiver('--test-stat-result', str(result), '--test-stat-errno', str(raw_errno)) as (sandbox, port, token):
        with paired(port, token) as session, pytest.raises(NetworkTransferError) as failure:
            begin(session, 1, 1)
        message = str(failure.value)
        assert 'Cannot check destination: fstatat' in message
        assert f'errno={expected} ({os.strerror(expected)})' in message
        assert f'raw_result={result}' in message and f'raw_errno={raw_errno}' in message
        assert 'already exists' not in message
        assert not list((sandbox / 'internal').iterdir())
    output = receiver.diagnostics[-1]
    assert f'errno={expected} ({os.strerror(expected)})' in output
    # The absolute lstat probe sees ENOENT, but must never override fstatat's error.
    assert f'comparison=lstat name=Package raw_result=-1 errno={errno.ENOENT}' in output


def test_sdk_positive_enoent_overrides_stale_errno_and_allows_only_a_new_stage(receiver):
    with receiver('--test-stat-result', str(errno.ENOENT), '--test-stat-errno', str(errno.EEXIST)) as (sandbox, port, token):
        with paired(port, token) as session:
            assert begin(session, 1, 1) == b'ready'
        wait_clean(sandbox)
        assert not list((sandbox / 'internal').iterdir())
    output = receiver.diagnostics[-1]
    assert f'raw_result={errno.ENOENT}' in output
    assert f'raw_errno={errno.EEXIST} errno={errno.ENOENT} ({os.strerror(errno.ENOENT)})' in output


@pytest.mark.parametrize('result,code', [(errno.ENOSYS, errno.ENOENT), (-1, errno.EINVAL)])
def test_unsupported_descriptor_stat_probe_stops_before_staging(receiver, result, code):
    expected = errno.ENOSYS if result > 0 else code
    with receiver('--test-stat-result', str(result), '--test-stat-errno', str(code), '--test-stat-dot') as (sandbox, port, token):
        with paired(port, token) as session, pytest.raises(NetworkTransferError) as failure:
            begin(session, 1, 1)
        assert 'fstatat self-check failed' in str(failure.value)
        assert f'errno={expected} ({os.strerror(expected)})' in str(failure.value)
        assert not list((sandbox / 'internal').iterdir())
    assert 'name=.' in receiver.diagnostics[-1]
    assert 'comparison=lstat' not in receiver.diagnostics[-1]


@pytest.mark.parametrize('kind', ['directory', 'file', 'symlink', 'dangling-symlink', 'fifo'])
def test_all_existing_destination_types_are_refused_without_following_links(receiver, tmp_path, kind):
    outside = tmp_path / 'keep-outside'
    outside.write_bytes(b'keep')
    with receiver() as (sandbox, port, token):
        parent = sandbox / 'internal'
        parent.mkdir()
        target = parent / 'Package'
        if kind == 'directory':
            target.mkdir()
        elif kind == 'file':
            target.write_bytes(b'keep')
        elif kind == 'fifo':
            os.mkfifo(target)
        else:
            target.symlink_to(outside if kind == 'symlink' else tmp_path / 'absent')
        with paired(port, token) as session, pytest.raises(NetworkTransferError, match='already exists'):
            begin(session, 1, 1)
        assert sorted(path.name for path in parent.iterdir()) == ['Package']
        assert os.path.lexists(target)
        assert outside.read_bytes() == b'keep'
    assert 'raw_result=0' in receiver.diagnostics[-1]


def test_sdk_carry_translation_preserves_descriptor_values_and_translates_errors(receiver_binary, tmp_path):
    source = tmp_path / 'syscall-contract.c'
    source.write_text(r'''
#include <assert.h>
#include <errno.h>
#include "sdk_fs.h"
int main(void) {
    errno = EACCES;
    assert(receiver_syscall_result(2, 0) == 2); /* a valid fd, not ENOENT */
    assert(errno == EACCES);
    assert(receiver_syscall_result(0, 0) == 0);
    assert(receiver_syscall_result(ENOENT, 1) == -1 && errno == ENOENT);
    assert(receiver_syscall_result(EACCES, 1) == -1 && errno == EACCES);
    assert(receiver_syscall_result(ENOSYS, 1) == -1 && errno == ENOSYS);
    assert(receiver_syscall_result(0, 1) == -1 && errno == EIO);
    return 0;
}
''')
    binary = tmp_path / 'syscall-contract'
    include = Path(__file__).resolve().parents[1] / 'ps5/receiver'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', str(include),
                    str(source), '-o', str(binary)], check=True, capture_output=True)
    subprocess.run([str(binary)], check=True, timeout=5)


@pytest.mark.parametrize('destination,import_path,temp_path', [
    (0, 'internal', 'playden-packager-temp'),
    (1, 'usb0/playden/import', 'usb0/playden-packager-temp'),
])
def test_staging_is_outside_import_and_cleanup_preserves_unknown_directories(
        receiver, destination, import_path, temp_path):
    with receiver() as (sandbox, port, token):
        temporary = sandbox / temp_path
        temporary.mkdir(parents=True)
        unknown = temporary / '.pdlan-unknown'
        unknown.mkdir()
        (unknown / 'keep').write_bytes(b'not owned by this session')
        with paired(port, token) as session:
            begin(session, 1, 3, destination=destination)
            entry(session, 'test', b'abc')
            assert list((sandbox / import_path).iterdir()) == []
            stages = list(temporary.glob('.pdlan-*/package'))
            assert len(stages) == 1
            assert (stages[0] / 'test').read_bytes() == b'abc'
        deadline = time.monotonic() + 3
        while stages[0].parent.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert not stages[0].parent.exists()
        assert (unknown / 'keep').read_bytes() == b'not owned by this session'
        assert list((sandbox / import_path).iterdir()) == []


@pytest.mark.parametrize('action', ['symlink', 'replacement'])
def test_temporary_root_changes_fail_closed(receiver, tmp_path, action):
    outside = tmp_path / 'outside-temp'
    outside.mkdir()
    (outside / 'keep').write_bytes(b'untouched')
    files = contents()
    files['package.json'] = inventory(files)
    with receiver() as (sandbox, port, token):
        temporary = sandbox / 'playden-packager-temp'
        if action == 'symlink':
            temporary.symlink_to(outside, target_is_directory=True)
            with paired(port, token) as session, pytest.raises(NetworkTransferError):
                begin(session, 1, 0)
        else:
            with paired(port, token) as session:
                upload(session, files)
                verify(session)
                temporary.rename(sandbox / 'moved-temp')
                temporary.mkdir()
                (temporary / 'keep').write_bytes(b'replacement')
                session.send(COMMIT)
                with pytest.raises(NetworkTransferError):
                    session.ack()
            assert (temporary / 'keep').read_bytes() == b'replacement'
        assert (outside / 'keep').read_bytes() == b'untouched'
        assert not (sandbox / 'internal/Package').exists()


@pytest.mark.parametrize('options', [('--test-stage-device-mismatch',),
    ('--test-rename-errno', str(errno.EXDEV)), ('--test-rename-errno', str(errno.EIO))])
def test_cross_device_or_failed_rename_does_not_publish(receiver, options):
    files = contents()
    files['package.json'] = inventory(files)
    with receiver(*options) as (sandbox, port, token):
        with paired(port, token) as session, pytest.raises(NetworkTransferError):
            upload(session, files)
            verify(session)
            session.send(COMMIT)
            session.ack()
        wait_clean(sandbox)
        assert list((sandbox / 'internal').iterdir()) == []


def test_directory_walks_and_cleanup_ignore_shared_descriptor_offsets(receiver_binary, tmp_path):
    root = Path(__file__).resolve().parents[1]
    source, binary = tmp_path / 'scan.c', tmp_path / 'scan'
    source.write_text(r'''
#define main receiver_main
#include "main.c"
#undef main
#include <assert.h>
int main(int argc, char **argv) {
    assert(argc == 2);
    int root = open(argv[1], O_RDONLY | O_DIRECTORY | O_NOFOLLOW);
    assert(root >= 0);
    int file = openat(root, "first", O_WRONLY | O_CREAT | O_EXCL, 0600);
    assert(file >= 0); close(file);
    /* Consume a dup of the pinned directory. New walks must start independently. */
    DIR *old = fdopendir(dup(root)); assert(old);
    while (readdir(old)) {}
    closedir(old);
    file = openat(root, "second", O_WRONLY | O_CREAT | O_EXCL, 0600);
    assert(file >= 0); close(file);
    Wire wire = {.fd = -1};
    for (int attempt = 0; attempt < 2; ++attempt) {
        size_t count = 0;
        assert(count_tree(root, 0, &count, &wire, 0) && count == 2);
    }
    assert(remove_contents(root));
    size_t count = 0;
    assert(count_tree(root, 0, &count, &wire, 0) && count == 0);
    close(root);
    return 0;
}
''')
    sandbox = tmp_path / 'scan-root'
    sandbox.mkdir()
    flags = ['-O2']
    if os.environ.get('PLAYDEN_RECEIVER_SANITIZE') == '1':
        flags = ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    subprocess.run(['cc', '-std=c11', '-DHOST_TEST', *flags, '-I', str(root / 'ps5/receiver'),
                    str(source), str(root / 'ps5/receiver/vendor/sha-256.c'), '-o', str(binary)], check=True)
    subprocess.run([str(binary), str(sandbox)], check=True)
    assert list(sandbox.iterdir()) == []


@pytest.mark.parametrize('compiler', ['cc', 'clang'])
def test_freebsd_syscall_clobbers_preserve_progress_total_and_live_pointers(tmp_path, compiler):
    import platform
    if os.name != 'posix' or platform.machine().lower() not in ('x86_64', 'amd64') or not shutil.which(compiler):
        pytest.skip('x86-64 POSIX compiler required for the FreeBSD ABI simulation')
    source = tmp_path / 'syscall-registers.c'
    source.write_text(r'''
#define RECEIVER_SYSCALL_ABI_TEST
#include "sdk_fs.h"
#include <assert.h>
/* Emulate FreeBSD's volatile register return, including secondary RDX output.
   No Linux/FreeBSD syscall or physical device is invoked by this test. */
__asm__(".text\n.global receiver_test_gate\nreceiver_test_gate:\n"
        "xor %r8d,%r8d\nxor %r9d,%r9d\nxor %r10d,%r10d\n"
        "xor %edi,%edi\nxor %esi,%esi\nxor %edx,%edx\n"
        "mov $77,%eax\nstc\nret\n");
__attribute__((noinline)) static uint64_t retained_total(long a, uintptr_t b, uintptr_t c,
                                                       uintptr_t d, uint64_t total) {
    ReceiverSyscall result = receiver_syscall4(a, b, c, d, 0);
    assert(result.value == 77 && result.failed == 1);
    return total;
}
__attribute__((noinline)) static uint64_t progress(long a, uintptr_t b, uintptr_t c,
                                                 uintptr_t d, uint64_t total, uint64_t *state) {
    ReceiverSyscall result = receiver_syscall4(a, b, c, d, 0);
    assert(result.value == 77 && result.failed == 1);
    *state += 128;
    return total;
}
int main(void) {
    volatile uint64_t totals[] = {UINT64_C(937944806), UINT64_C(4294967301),
                                  UINT64_C(274877906944)};
    for (unsigned i = 0; i < 3; ++i)
        assert(retained_total(499, 1, 2, 3, totals[i]) == totals[i]);
    uint64_t state = 0;
    assert(progress(499, 1, 2, 3, UINT64_C(937944806), &state) == UINT64_C(937944806));
    assert(state == 128);
    return 0;
}
''')
    binary = tmp_path / 'syscall-registers'
    include = Path(__file__).resolve().parents[1] / 'ps5/receiver'
    subprocess.run([compiler, '-std=c11', '-O2', '-mno-red-zone', '-Wall', '-Wextra', '-Werror',
                    '-I', str(include), str(source), '-o', str(binary)], check=True, capture_output=True)
    subprocess.run([str(binary)], check=True, timeout=5)


def test_many_file_verification_heartbeats_are_monotonic_and_keep_exact_total(receiver):
    import struct
    files = contents()
    # The physical report had 5,678 files. Hit dozens of 128-entry heartbeats
    # without making every routine test upload almost a gigabyte.
    for index in range(5678 - len(files) - 1):
        files[f'extras/{index:05}.bin'] = (index.to_bytes(4, 'big') * (1 + index % 3))
    files['package.json'] = inventory(files)
    total = sum(map(len, files.values()))
    with receiver('--diagnostics') as (sandbox, port, token):
        with paired(port, token) as session:
            upload(session, files)
            session.send(VERIFY)
            values = []
            while True:
                kind, body = session.receive()
                if kind == ACK:
                    assert body == b'verified'
                    break
                assert kind == VERIFY_PROGRESS and len(body) == 16
                done, expected = struct.unpack('!QQ', body)
                assert expected == total and 0 <= done <= total
                assert not values or done >= values[-1]
                values.append(done)
            assert len(values) >= 2 * (5678 // 128)
            assert values[-1] == total
            session.send(COMMIT)
            assert session.ack().endswith(b'/Package')
        wait_clean(sandbox)
        assert sum(p.is_file() for p in (sandbox / 'internal/Package').rglob('*')) == 5678


def test_metrics_are_visible_while_receiver_is_alive_after_disconnect(receiver_binary, tmp_path):
    sandbox = tmp_path / 'metrics-console'
    sandbox.mkdir()
    process = subprocess.Popen([str(receiver_binary), '--bind', '127.0.0.1', '--port', '0', '--test-legacy-token',
                                '--test-root', str(sandbox), '--diagnostics', '--test-pause-cleanup'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lines = queue.Queue()
    def drain():
        for line in process.stdout:
            lines.put(line)
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        banner = lines.get(timeout=10)
        match = re.fullmatch(r'PDLAN/1 address=127.0.0.1 port=(\d+) token=([0-9a-f]{64})\n', banner)
        assert match
        port, token = int(match[1]), match[2]
        files = contents()
        files['package.json'] = inventory(files)
        with paired(port, token) as session:
            upload(session, files)
            session.send(VERIFY)
            kind, body = session.receive()
            assert kind == VERIFY_PROGRESS
            # Close during verification rather than requesting receiver termination.
        observed = []
        deadline = time.monotonic() + 5
        while not any(line.startswith('receiver_metrics ') for line in observed):
            observed.append(lines.get(timeout=max(.01, deadline - time.monotonic())))
        metric = next(line for line in observed if line.startswith('receiver_metrics '))
        assert 'published=0' in metric
        assert list((sandbox / 'playden-packager-temp').glob('.pdlan-*'))
        assert not any(line.startswith('receiver_cleanup ') for line in observed)
        process.stdin.write('x')  # Release the deterministic cleanup gate.
        process.stdin.flush()
        while not any(line.startswith('receiver_cleanup ') for line in observed):
            observed.append(lines.get(timeout=5))
        assert observed.index(metric) < next(i for i, line in enumerate(observed) if line.startswith('receiver_cleanup '))
        assert process.poll() is None  # No SIGTERM needed to flush the record.
        wait_clean(sandbox)
        assert not (sandbox / 'internal/Package').exists()
        with paired(port, token) as session:
            assert begin(session, 1, 0, name='Still-Available')
        process.stdin.close()
    finally:
        if not process.stdin.closed:
            process.stdin.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(2)
        assert process.returncode == 0
        assert not process.stderr.read()


def test_socket_io_rejects_impossible_counts_without_underflow(receiver_binary, tmp_path):
    source = tmp_path / 'io-count.c'
    source.write_text(r'''
#include <sys/socket.h>
#include <assert.h>
#include <errno.h>
static int calls;
static ssize_t bad_send(int fd, const void *data, size_t n, int flags) {
    (void)fd; (void)data; (void)flags; ++calls; return (ssize_t)n + 1;
}
static ssize_t bad_recv(int fd, void *data, size_t n, int flags) {
    (void)fd; (void)data; (void)flags; ++calls; return (ssize_t)n + 1;
}
#define send bad_send
#define recv bad_recv
#include "wire.h"
int main(void) {
    char data[1] = {0};
    assert(!io_all(-1, data, 1, 1) && errno == EIO && calls == 1);
    assert(!io_all(-1, data, 1, 0) && errno == EIO && calls == 2);
    return 0;
}
''')
    binary = tmp_path / 'io-count'
    root = Path(__file__).resolve().parents[1]
    flags = ['-O2']
    if os.environ.get('PLAYDEN_RECEIVER_SANITIZE') == '1':
        flags = ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    subprocess.run(['cc', '-std=c11', '-D_POSIX_C_SOURCE=200809L', *flags, '-I', str(root / 'ps5/receiver'),
                    str(source), str(root / 'ps5/receiver/vendor/sha-256.c'), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=5)


@pytest.mark.parametrize('destination,subroot,temporary', [
    (0, 'internal', 'playden-packager-temp'),
    (1, 'usb0/playden/import', 'usb0/playden-packager-temp'),
])
def test_import_modes_are_prepared_inside_private_staging_and_published_recursively(
        receiver, destination, subroot, temporary):
    import stat
    files = contents()
    files['extras/nested/readme.txt'] = b'data, not a POSIX executable'
    files['package.json'] = inventory(files)
    with receiver() as (sandbox, port, token):
        with paired(port, token) as session:
            upload(session, files, destination=destination)
            stage = next((sandbox / temporary).glob('.pdlan-*'))
            assert stat.S_IMODE(stage.stat().st_mode) == 0o700
            assert stat.S_IMODE((stage / 'package/recipe.json').stat().st_mode) == 0o600
            verify(session)
            assert stat.S_IMODE(stage.stat().st_mode) == 0o700
            assert stat.S_IMODE((sandbox / temporary).stat().st_mode) == 0o700
            assert not (sandbox / subroot / 'Package').exists()
            session.send(COMMIT)
            assert session.ack().endswith(b'/Package')
        published = sandbox / subroot / 'Package'
        assert stat.S_IMODE((sandbox / subroot).stat().st_mode) == 0o777
        for path in [published, *published.rglob('*')]:
            assert stat.S_IMODE(path.stat().st_mode) == (0o777 if path.is_dir() else 0o666), path
        if destination:
            assert (sandbox / 'usb0/playden').stat().st_mode & 0o555 == 0o555
        wait_clean(sandbox)


@pytest.mark.parametrize('code', [errno.EPERM, errno.EIO, errno.EOPNOTSUPP])
def test_permission_failure_discards_stage_and_never_publishes(receiver, code):
    files = contents()
    files['package.json'] = inventory(files)
    with receiver('--test-permission-errno', str(code)) as (sandbox, port, token):
        with paired(port, token) as session:
            upload(session, files)
            with pytest.raises(NetworkTransferError, match=f'permissions: errno={code}'):
                verify(session)
        wait_clean(sandbox)
        assert not (sandbox / 'internal/Package').exists()


@pytest.mark.parametrize('when', ['before-begin', 'before-commit'])
def test_metrics_requests_cannot_bypass_transfer_state_or_publish(receiver, when):
    from minecraft_playden.network_protocol import GET_METRICS
    files = contents()
    files['package.json'] = inventory(files)
    with receiver('--diagnostics') as (sandbox, port, token):
        with paired(port, token) as session:
            if when == 'before-commit':
                upload(session, files)
                verify(session)
            session.send(GET_METRICS)
            with pytest.raises(NetworkTransferError):
                session.ack()
        wait_clean(sandbox)
        assert not (sandbox / 'internal/Package').exists()


def test_usb_container_permissions_preserve_existing_special_bits_and_mount_mode(receiver):
    files = contents()
    files['package.json'] = inventory(files)
    with receiver() as (sandbox, port, token):
        parent = sandbox / 'usb0/playden'
        parent.mkdir(mode=0o1770)
        parent.chmod(0o1770)
        mount_mode = (sandbox / 'usb0').stat().st_mode
        with paired(port, token) as session:
            upload(session, files, destination=1)
            verify(session)
            session.send(COMMIT)
            session.ack()
        assert parent.stat().st_mode & 0o1000
        assert parent.stat().st_mode & 0o555 == 0o555
        assert (sandbox / 'usb0').stat().st_mode == mount_mode


@pytest.mark.parametrize('fault', ['valid', 'mac', 'sequence', 'truncated', 'oversized'])
@pytest.mark.parametrize('fragment', [False, True])
def test_buffered_frame_reader_handles_split_and_coalesced_authenticated_frames(receiver_binary, tmp_path, fault, fragment):
    source = tmp_path / 'frame-reader.c'
    source.write_text(r'''
#define _POSIX_C_SOURCE 200809L
#include <sys/socket.h>
#include <stdlib.h>
#include <stdio.h>
#include <assert.h>
static size_t limit;
static ssize_t limited_recv(int fd, void *body, size_t n, int flags) {
    return recv(fd, body, limit && n > limit ? limit : n, flags);
}
#define recv limited_recv
#include "wire.h"
int main(int argc, char **argv) {
    assert(argc == 5);
    Wire wire = {.fd = atoi(argv[1])};
    buffered_reads = atoi(argv[2]); limit = (size_t)atoi(argv[3]);
    diagnostics_enabled = 1;
    for (int i = 0; i < 32; ++i) wire.key[i] = (uint8_t)i;
    uint8_t body[FRAME_MAX], type; size_t size;
    int count = 0;
    while (count < 3 && frame_read(&wire, &type, body, &size)) ++count;
    assert(count == atoi(argv[4]));
    printf("%llu\n", (unsigned long long)metrics.recv_calls);
    free(wire.rx);
    return 0;
}
''')
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path / 'frame-reader'
    flags = ['-O2']
    if os.environ.get('PLAYDEN_RECEIVER_SANITIZE') == '1':
        flags = ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    subprocess.run(['cc', '-std=c11', *flags, '-I', str(root / 'ps5/receiver'), str(source),
                    str(root / 'ps5/receiver/vendor/sha-256.c'), '-o', str(binary)], check=True)
    frames = []
    for sequence, (kind, body) in enumerate([(DATA, b'abc'), (DATA, bytes(range(256)) * 256), (END_FILE, b'')]):
        header = HEADER.pack(kind, len(body), sequence)
        frames.append(header + body + hmac.digest(bytes(range(32)), b'C' + header + body, 'sha256'))
    expected = 3 if fault == 'valid' else 1
    if fault == 'mac':
        frames[1] = frames[1][:-1] + bytes([frames[1][-1] ^ 1])
    elif fault == 'sequence':
        frames[1] = HEADER.pack(DATA, MAX_FRAME, 7) + frames[1][HEADER.size:]
    elif fault == 'truncated':
        frames = [frames[0], frames[1][:1000]]
    elif fault == 'oversized':
        frames = [HEADER.pack(DATA, MAX_FRAME + 1, 0)]
        expected = 0
    calls = []
    for buffered in (0, 1):
        # Independent sockets ensure each reader sees the identical queued stream.
        parent, child = socket.socketpair()
        with parent, child:
            parent.sendall(b''.join(frames))
            parent.shutdown(socket.SHUT_WR)
            run = subprocess.run([str(binary), str(child.fileno()), str(buffered), '97' if fragment else '0', str(expected)],
                                 pass_fds=(child.fileno(),), capture_output=True, text=True, check=True, timeout=10)
            calls.append(int(run.stdout))
    if fault == 'valid' and not fragment:
        assert calls[1] < calls[0]


def test_legacy_schema_one_client_can_close_without_requesting_profile(receiver):
    from minecraft_playden.network_protocol import GET_METRICS
    from minecraft_playden.network_metrics import decode_receiver_metrics
    files = contents()
    files['package.json'] = inventory(files)
    with receiver('--diagnostics') as (sandbox, port, token):
        with paired(port, token) as session:
            upload(session, files)
            verify(session)
            session.send(COMMIT)
            assert session.ack() == b'/data/playden-import/Package'
            session.send(GET_METRICS)
            body = session.ack()
            assert len(body) == 153 and body[0] == 1
            assert decode_receiver_metrics(body, sum(map(len, files.values())), len(files))['published'] == 1
        wait_clean(sandbox)
        with paired(port, token) as session:
            assert begin(session, 1, 0, name='Next-Transfer') == b'ready;metrics=1'


@pytest.mark.parametrize('fault', [
    'mode', 'layout', 'partial', 'empty', 'extra', 'missing-id', 'duplicate-controller',
])
def test_receiver_accepts_only_complete_builtin_controller_defaults(receiver, fault):
    files = contents()
    document = json.loads(files['recipe.json'])
    if fault == 'mode':
        document['defaults']['controller'] = 'xinput'
    elif fault == 'layout':
        document['defaults']['controller_layout'] = 'cross = key:q'
    elif fault == 'partial':
        del document['defaults']['controller_layout']
    elif fault == 'empty':
        document['defaults'] = {}
    elif fault == 'extra':
        document['defaults']['environment'] = 'PATH=unsafe'
    elif fault == 'missing-id':
        del document['recipe_id']
    files['recipe.json'] = json.dumps(document).encode()
    if fault == 'duplicate-controller':
        files['recipe.json'] = files['recipe.json'].replace(
            b'"controller": "keyboard"', b'"controller": "keyboard", "controller": "keyboard"')
    files['package.json'] = inventory(files)
    with receiver() as (sandbox, port, token), paired(port, token) as session:
        upload(session, files)
        with pytest.raises(NetworkTransferError, match='Invalid recipe'):
            verify(session)
    wait_clean(sandbox)


@pytest.mark.parametrize('fault', ['valid', 'command-mismatch', 'missing-jar', 'unlisted-jar', 'wrong-main', 'missing-command'])
def test_receiver_java8_adapter_is_bound_to_inventory_and_recipe(receiver, fault):
    files = contents()
    command = '"-Xmx4G" "-cp" "../../playden-java8.jar" "org.minecraftplayden.Java8Launch"'
    files['playden.java8-command'] = command.encode()
    files['playden-java8.jar'] = b'owned adapter fixture; receiver checks inventory, Python checks contents'
    document = json.loads(files['recipe.json'])
    document['launch_entries'][0]['arguments'] = command
    if fault == 'command-mismatch':
        document['launch_entries'][0]['arguments'] = command.replace('-Xmx4G', '-Xmx2G')
    if fault == 'missing-jar':
        del files['playden-java8.jar']
    if fault == 'wrong-main':
        document['launch_entries'][0]['arguments'] = command.replace('org.minecraftplayden.Java8Launch', 'other.Main')
        files['playden.java8-command'] = document['launch_entries'][0]['arguments'].encode()
    if fault == 'missing-command':
        del files['playden.java8-command']
    files['recipe.json'] = json.dumps(document).encode()
    files['package.json'] = inventory(files)
    if fault == 'unlisted-jar':
        record = json.loads(files['package.json'])
        del record['files']['playden-java8.jar']
        files['package.json'] = json.dumps(record).encode()
    with receiver() as (sandbox, port, token), paired(port, token) as session:
        upload(session, files)
        if fault == 'valid':
            verify(session)
            session.send(COMMIT)
            session.ack()
            assert (sandbox / 'internal/Package/playden-java8.jar').read_bytes() == files['playden-java8.jar']
        else:
            with pytest.raises(NetworkTransferError, match='Invalid recipe'):
                verify(session)


@pytest.mark.parametrize('fault', ['valid', 'missing-icon', 'unlisted-art', 'bad-crc', 'bad-dimensions', 'both-recipes', 'legacy-unlisted-art'])
def test_receiver_current_metadata_layout_and_artwork_are_strict(receiver, fault):
    from test_artwork import png
    files = contents()
    if fault != 'legacy-unlisted-art':
        files['playden/recipe.json'] = files.pop('recipe.json')
        files['playden/background.png'] = png((48, 24))
    files['playden/icon.png'] = png()
    if fault == 'missing-icon':
        del files['playden/icon.png']
    elif fault == 'bad-crc':
        data = bytearray(files['playden/icon.png'])
        data[-1] ^= 1
        files['playden/icon.png'] = bytes(data)
    elif fault == 'bad-dimensions':
        import struct
        data = bytearray(files['playden/icon.png'])
        struct.pack_into('>I', data, 16, 8193)
        files['playden/icon.png'] = bytes(data)
    elif fault == 'both-recipes':
        files['recipe.json'] = files['playden/recipe.json']
    files['package.json'] = inventory(files)
    if fault in ('unlisted-art', 'legacy-unlisted-art'):
        record = json.loads(files['package.json'])
        del record['files']['playden/icon.png']
        files['package.json'] = json.dumps(record).encode()
    with receiver() as (sandbox, port, token), paired(port, token) as session:
        upload(session, files)
        if fault == 'valid':
            verify(session)
            session.send(COMMIT)
            session.ack()
            output = sandbox / 'internal/Package/playden'
            assert (output / 'recipe.json').read_bytes() == files['playden/recipe.json']
            assert (output / 'icon.png').read_bytes() == files['playden/icon.png']
        else:
            with pytest.raises(NetworkTransferError, match='Invalid recipe'):
                verify(session)


@pytest.mark.parametrize('destination,import_path,temp_path', [
    (0, 'internal', 'playden-packager-temp'),
    (1, 'usb0/playden/import', 'usb0/playden-packager-temp'),
])
@pytest.mark.parametrize('outcome', ['success', 'disconnect', 'verify-failure', 'publish-failure', 'no-space'])
def test_owned_stage_and_empty_parent_are_removed_on_all_transfer_outcomes(
        receiver, destination, import_path, temp_path, outcome):
    options = ('--test-free-bytes', '0') if outcome == 'no-space' else (
        ('--test-rename-errno', str(errno.EIO)) if outcome == 'publish-failure' else ())
    files = contents()
    files['package.json'] = inventory(files)
    if outcome == 'verify-failure':
        files['recipe.json'] = b'broken recipe'
        files['package.json'] = inventory(files)
    with receiver(*options) as (sandbox, port, token):
        with paired(port, token) as session:
            if outcome == 'no-space':
                with pytest.raises(NetworkTransferError, match='Insufficient storage'):
                    begin(session, 1, 100, destination=destination)
            else:
                upload(session, files, destination=destination)
                assert (sandbox / temp_path).is_dir()
                if outcome == 'verify-failure':
                    with pytest.raises(NetworkTransferError, match='Invalid recipe'):
                        verify(session)
                elif outcome != 'disconnect':
                    verify(session)
                    session.send(COMMIT)
                    if outcome == 'publish-failure':
                        with pytest.raises(NetworkTransferError, match='Publication failed'):
                            session.ack()
                    else:
                        assert session.ack().endswith(b'/Package')
        deadline = time.monotonic() + 3
        while (sandbox / temp_path).exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert not (sandbox / temp_path).exists()
        assert (sandbox / import_path / 'Package').exists() is (outcome == 'success')


@pytest.mark.parametrize('destination,import_path,temp_path', [
    (0, 'internal', 'playden-packager-temp'),
    (1, 'usb0/playden/import', 'usb0/playden-packager-temp'),
])
def test_concurrent_receiver_stages_survive_other_transfer_completion(
        receiver, tmp_path, destination, import_path, temp_path):
    shared = tmp_path / 'shared-console'
    files = contents()
    files['package.json'] = inventory(files)
    with receiver(sandbox_root=shared) as (_, port_a, token_a), receiver(sandbox_root=shared) as (_, port_b, token_b):
        with paired(port_b, token_b) as session_b:
            upload(session_b, files, destination=destination, name='Second')
            with paired(port_a, token_a) as session_a:
                upload(session_a, files, destination=destination, name='First')
                verify(session_a)
                session_a.send(COMMIT)
                session_a.ack()
            deadline = time.monotonic() + 3
            while len(list((shared / temp_path).glob('.pdlan-*'))) > 1 and time.monotonic() < deadline:
                time.sleep(.01)
            assert len(list((shared / temp_path).glob('.pdlan-*/package'))) == 1
            verify(session_b)
            session_b.send(COMMIT)
            session_b.ack()
        deadline = time.monotonic() + 3
        while (shared / temp_path).exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert not (shared / temp_path).exists()
        for name in ('First', 'Second'):
            assert (shared / import_path / name / 'recipe.json').read_bytes() == files['recipe.json']


@pytest.mark.parametrize('destination,temp_path', [(0, 'playden-packager-temp'), (1, 'usb0/playden-packager-temp')])
def test_cleanup_failure_is_logged_without_revoking_publication(receiver, destination, temp_path):
    files = contents()
    files['package.json'] = inventory(files)
    with receiver('--test-cleanup-errno', str(errno.EACCES)) as (sandbox, port, token):
        with paired(port, token) as session:
            upload(session, files, destination=destination)
            verify(session)
            session.send(COMMIT)
            assert session.ack().endswith(b'/Package')
        wait_clean(sandbox)
        assert (sandbox / temp_path).is_dir()
    assert 'receiver_cleanup ' in receiver.diagnostics[-1]
    assert 'complete=0 parent_removed=0 errno=13' in receiver.diagnostics[-1]


def test_empty_replacement_temporary_parent_is_preserved(receiver):
    files = contents()
    files['package.json'] = inventory(files)
    with receiver() as (sandbox, port, token):
        temporary = sandbox / 'playden-packager-temp'
        with paired(port, token) as session:
            upload(session, files)
            verify(session)
            temporary.rename(sandbox / 'moved-original-temp')
            temporary.mkdir()
            session.send(COMMIT)
            with pytest.raises(NetworkTransferError, match='Publication failed'):
                session.ack()
        wait_clean(sandbox)
        assert temporary.is_dir() and not list(temporary.iterdir())
        deadline = time.monotonic() + 3
        while list((sandbox / 'moved-original-temp').iterdir()) and time.monotonic() < deadline:
            time.sleep(.01)
        assert not list((sandbox / 'moved-original-temp').iterdir())
