"""Real exporter -> Python LAN client -> actual C receiver, entirely on loopback."""
import io
import json
import socket
import hashlib
import hmac
import threading
from unittest.mock import patch

import pytest

from minecraft_playden.cli import main
from minecraft_playden.network_transfer import NetworkRequest, NetworkResult
from minecraft_playden.network_protocol import (
    ACK, BEGIN, BEGIN_INFO, COMMIT, DATA, END_FILE, ENTRY, ENTRY_INFO,
    HEADER, MAGIC, MAX_FRAME, VERIFY, NetworkTransferError, Session,
)
from minecraft_playden.operations import CancellationToken, OperationCancelled
from minecraft_playden.validator import validate
from lan_fixtures import build_package
from test_network_receiver import receiver, receiver_binary, wait_clean


@pytest.fixture
def built_package(tmp_path):
    return build_package(tmp_path)


def snapshot(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('pipeline', [False, True])
def test_client_streams_complete_package_on_every_host(built_package, pipeline):
    # A Python reference peer exercises real sockets and local file handles on
    # Windows too; the POSIX/PS5 C receiver is tested independently below.
    key, nonce = bytes(range(32)), bytes(range(32, 64))
    errors, received = [], {}
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(5)
    def peer():
        try:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(5)
                def read(n):
                    data = b''
                    while len(data) < n:
                        block = connection.recv(n - len(data))
                        assert block, 'Client disconnected'
                        data += block
                    return data
                connection.sendall(MAGIC + nonce)
                client, proof = read(32), read(32)
                assert proof == hmac.digest(key, b'client' + nonce + client, 'sha256')
                connection.sendall(hmac.digest(key, b'server' + nonce + client, 'sha256'))
                session_key = hmac.digest(key, b'session' + nonce + client, 'sha256')
                sent = sequence = 0
                active, data, expected = '', b'', b''
                while True:
                    header = read(HEADER.size)
                    kind, size, counter = HEADER.unpack(header)
                    assert counter == sequence and size <= MAX_FRAME
                    sequence += 1
                    body, proof = read(size), read(32)
                    assert proof == hmac.digest(session_key, b'C' + header + body, 'sha256')
                    response = b'ok'
                    if kind == BEGIN:
                        dest, name_size, count, total = BEGIN_INFO.unpack(body[:15])
                        assert dest == 0 and body[15:].decode() == built_package.name
                    elif kind == ENTRY:
                        file_kind, path_size, file_size = ENTRY_INFO.unpack(body[:11])
                        active, expected, data = body[43:].decode(), body[11:43], b''
                        assert len(body[43:]) == path_size
                    elif kind == DATA:
                        data += body
                        continue
                    elif kind == END_FILE:
                        assert len(data) == file_size and hashlib.sha256(data).digest() == expected
                        received[active] = data
                    elif kind == VERIFY:
                        response = b'verified'
                    elif kind == COMMIT:
                        response = ('/data/playden-import/' + built_package.name).encode()
                    else:
                        raise AssertionError('Unexpected command')
                    header = HEADER.pack(ACK, len(response), sent)
                    sent += 1
                    connection.sendall(header + response + hmac.digest(session_key, b'S' + header + response, 'sha256'))
                    if kind == COMMIT:
                        break
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=peer)
    thread.start()
    try:
        result = NetworkRequest(built_package, '127.0.0.1', key.hex(), port=listener.getsockname()[1],
                                pipeline_files=pipeline).run()
    finally:
        listener.close()
        thread.join(6)
    assert not thread.is_alive() and not errors, errors
    assert received == snapshot(built_package)
    assert result.files == len(received)


@pytest.mark.parametrize('destination,root', [('internal', 'internal'), ('usb0', 'usb0/playden/import')])
def test_complete_client_transfer_preserves_every_local_byte(receiver, built_package, destination, root):
    before = snapshot(built_package)
    events = []
    with receiver() as (sandbox, port, token):
        result = NetworkRequest(built_package, '127.0.0.1', token, destination, port).run(events.append)
        remote = sandbox / root / built_package.name
        assert result.source == built_package.resolve()
        assert result.files == len(before)
        assert result.bytes_copied == sum(map(len, before.values()))
        assert snapshot(remote) == before == snapshot(built_package)
        assert validate(remote) == []
        assert events[-1].stage == 'network-complete'
        assert {'network-send', 'network-verify', 'network-commit'}.issubset({event.stage for event in events})
        assert token not in repr(NetworkRequest(built_package, '127.0.0.1', token))


@pytest.mark.parametrize('when', ['network-send', 'network-verify'])
def test_cancellation_keeps_local_output_and_no_remote_publication(receiver, built_package, when):
    before = snapshot(built_package)
    cancellation = CancellationToken()
    def progress(event):
        if event.stage == when:
            cancellation.cancel()
    with receiver() as (sandbox, port, token):
        with pytest.raises(OperationCancelled):
            NetworkRequest(built_package, '127.0.0.1', token, port=port).run(progress, cancellation)
        wait_clean(sandbox)
        assert not (sandbox / 'internal' / built_package.name).exists()
        assert snapshot(built_package) == before


def test_cancellation_before_commit_prevents_publication(receiver, built_package):
    cancellation = CancellationToken()
    def progress(event):
        if event.stage == 'network-commit':
            cancellation.cancel()
    # A cancellation already set before sending COMMIT must still prevent it.
    with receiver() as (sandbox, port, token):
        with pytest.raises(OperationCancelled):
            NetworkRequest(built_package, '127.0.0.1', token, port=port).run(progress, cancellation)
        wait_clean(sandbox)
        assert not (sandbox / 'internal' / built_package.name).exists()


def test_cancellation_after_commit_resolves_receipt(receiver, built_package):
    cancellation = CancellationToken()
    original = Session.send
    def send(session, kind, body=b''):
        original(session, kind, body)
        if kind == COMMIT:
            cancellation.cancel()
    with receiver() as (sandbox, port, token), patch.object(Session, 'send', send):
        result = NetworkRequest(built_package, '127.0.0.1', token, port=port).run(cancellation=cancellation)
        assert result.destination.endswith('/' + built_package.name)
        assert (sandbox / 'internal' / built_package.name / 'recipe.json').is_file()


def test_lost_completion_receipt_reports_uncertainty_and_keeps_files(receiver, built_package):
    original_send, original_ack = Session.send, Session.ack
    committed = False
    def send(session, kind, data=b''):
        nonlocal committed
        original_send(session, kind, data)
        if kind == COMMIT:
            committed = True
    def ack(session):
        if committed:
            raise NetworkTransferError('synthetic lost receipt')
        return original_ack(session)
    with receiver() as (sandbox, port, token), patch.object(Session, 'send', send), patch.object(Session, 'ack', ack):
        with pytest.raises(NetworkTransferError, match='receipt was lost') as error:
            NetworkRequest(built_package, '127.0.0.1', token, port=port).run()
        assert '/data/playden-import/' in str(error.value)
        assert built_package.exists()


def test_invalid_local_packages_are_rejected_before_connecting(built_package):
    (built_package / 'playden.args').write_bytes(b'corrupt')
    with patch('socket.create_connection') as connect, pytest.raises(ValueError, match='validation failed'):
        NetworkRequest(built_package, '127.0.0.1', '00' * 32).run()
    connect.assert_not_called()


def test_local_symlinks_are_rejected_before_reading_or_connecting(built_package, tmp_path):
    target = tmp_path / 'outside'
    target.write_bytes(b'private')
    try:
        (built_package / 'link').symlink_to(target)
    except OSError:
        pytest.skip('Host cannot create fixture symlink')
    with patch('socket.create_connection') as connect, pytest.raises(ValueError, match='links'):
        NetworkRequest(built_package, '127.0.0.1', '00' * 32).run()
    connect.assert_not_called()
    assert target.read_bytes() == b'private'


def test_source_mutation_during_upload_prevents_commit(receiver, built_package):
    changed = False
    def progress(event):
        nonlocal changed
        if event.stage == 'network-send' and not changed:
            changed = True
            (built_package / 'new-file').write_bytes(b'changed')
    with receiver() as (sandbox, port, token):
        with pytest.raises(ValueError, match='changed during upload'):
            NetworkRequest(built_package, '127.0.0.1', token, port=port).run(progress)
        wait_clean(sandbox)
        assert not (sandbox / 'internal' / built_package.name).exists()


def test_cli_destination_override_does_not_use_gui_settings(tmp_path, monkeypatch, capsys):
    token = 'ab' * 32
    monkeypatch.setenv('PLAYDEN_PS5_TOKEN', token)
    monkeypatch.setattr('sys.argv', ['packager', 'transfer-ps5', str(tmp_path), '--host', '192.168.1.50', '--destination', 'usb0', '--name', 'Fresh Package'])
    result = NetworkResult(tmp_path, '/mnt/usb0/playden/import/Fresh Package', 1, 2)
    with patch.object(NetworkRequest, 'run', autospec=True, return_value=result) as run:
        assert main() == 0
    request = run.call_args.args[0]
    assert request.destination == 'usb0' and request.package_name == 'Fresh Package'
    assert token not in capsys.readouterr().out


@pytest.mark.parametrize('chunk_size,buffer', [(4096, None), (16384, 4096), (65536, 524288)])
def test_experimental_transfer_sizes_preserve_bytes_and_bounded_diagnostics(
        receiver, built_package, chunk_size, buffer):
    original = snapshot(built_package)
    logs = []
    with receiver('--diagnostics', '--socket-buffer', '524288') as (sandbox, port, token):
        result = NetworkRequest(built_package, '127.0.0.1', token, port=port,
                                chunk_size=chunk_size, socket_buffer=buffer).run(diagnostics=logs.append)
        wait_clean(sandbox)
        assert snapshot(sandbox / 'internal' / built_package.name) == original
    assert len(logs) == 1
    metrics = logs[0]
    assert metrics['bytes'] == result.bytes_copied
    assert metrics['files'] == result.files and metrics['published'] == 1
    for name in ('validation_s', 'prehash_s', 'pairing_s', 'transfer_s', 'verification_s',
                 'commit_s', 'socket_read_s', 'socket_write_s', 'frame_auth_s', 'wall_s'):
        assert metrics[name] >= 0
    encoded = json.dumps(metrics)
    assert token not in encoded and str(built_package) not in encoded
    line = next(line for line in receiver.diagnostics[-1].splitlines() if line.startswith('receiver_metrics '))
    counters = dict(item.split('=') for item in line.split()[1:])
    assert int(counters['bytes']) == result.bytes_copied
    assert int(counters['files']) == result.files
    assert int(counters['published']) == 1
    assert int(counters['fsyncs']) >= result.files
    # Two handshake sends, then one syscall per metadata frame; no three-part replies.
    assert int(counters['send_calls']) < result.files * 3 + 60


@pytest.mark.parametrize('options', [{'chunk_size': 0}, {'chunk_size': 65537},
                                    {'socket_buffer': 0}, {'socket_buffer': 4194305}])
def test_invalid_performance_options_fail_before_connect(tmp_path, options):
    with patch('socket.socket') as connect, pytest.raises(ValueError):
        NetworkRequest(tmp_path, '127.0.0.1', 'ab' * 32, **options).run()
    connect.assert_not_called()


def test_diagnostics_are_emitted_on_preflight_failure_without_secrets(tmp_path):
    logs = []
    with pytest.raises(ValueError):
        NetworkRequest(tmp_path, '127.0.0.1', 'ab' * 32, chunk_size=0).run(diagnostics=logs.append)
    assert len(logs) == 1 and logs[0]['wall_s'] >= 0
    assert 'published' not in logs[0]


def test_socket_buffer_option_failure_is_reported_and_keeps_os_defaults():
    from minecraft_playden.network_metrics import NetworkMetrics
    from minecraft_playden.network_protocol import configure_socket
    from unittest.mock import Mock
    stream = Mock()
    stream.setsockopt.side_effect = OSError('unsupported')
    stream.getsockopt.return_value = 16384
    metrics = NetworkMetrics(True)
    configure_socket(stream, 524288, metrics)
    assert metrics.values['socket_buffer_errors'] == 2
    assert metrics.values['socket_receive_effective'] == 16384


@pytest.mark.parametrize('fault', ['wrong-total', 'over-total', 'backwards', 'bad-length'])
def test_invalid_verification_progress_reports_counters_and_never_commits(receiver, built_package, fault):
    import struct
    from minecraft_playden.network_protocol import VERIFY_PROGRESS
    original_receive = Session.receive
    total = sum(p.stat().st_size for p in built_package.rglob('*') if p.is_file())
    injected = False
    def receive(session):
        nonlocal injected
        kind, body = original_receive(session)
        if kind == VERIFY_PROGRESS and not injected:
            injected = True
            if fault == 'bad-length':
                return kind, b'invalid'
            if fault == 'wrong-total':
                return kind, struct.pack('!QQ', 1, total + 1)
            if fault == 'over-total':
                return kind, struct.pack('!QQ', total + 1, total)
            return kind, struct.pack('!QQ', 1, total)
        if fault == 'backwards' and kind == ACK and body == b'verified':
            return VERIFY_PROGRESS, struct.pack('!QQ', 0, total)
        return kind, body
    before = snapshot(built_package)
    with receiver('--diagnostics') as (sandbox, port, token):
        with patch.object(Session, 'receive', receive), pytest.raises(NetworkTransferError) as failure:
            NetworkRequest(built_package, '127.0.0.1', token, port=port).run()
        if fault != 'bad-length':
            assert 'receiver_total=' in str(failure.value)
            assert f'sender_total={total}' in str(failure.value)
            assert 'previous_verified=' in str(failure.value)
        assert token not in str(failure.value)
        wait_clean(sandbox)
        assert not (sandbox / 'internal' / built_package.name).exists()
    assert snapshot(built_package) == before


def set_instance_name(package, name):
    path = package / 'metadata/instance-name.json'
    path.write_text(json.dumps({'schema': 1, 'name': name}), encoding='utf-8')
    inventory = json.loads((package / 'package.json').read_text())
    inventory['files']['metadata/instance-name.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (package / 'package.json').write_text(json.dumps(inventory))


@pytest.mark.parametrize('override,expected', [(None, 'My Fabric World'), ('Custom Name', 'Custom Name')])
def test_default_instance_name_survives_output_rename_and_override_is_optional(receiver, built_package, override, expected):
    set_instance_name(built_package, 'My Fabric World')
    renamed = built_package.with_name('Generic Export Folder')
    built_package.rename(renamed)
    before = snapshot(renamed)
    with receiver() as (sandbox, port, token):
        result = NetworkRequest(renamed, '127.0.0.1', token, port=port, package_name=override).run()
        assert result.destination == '/data/playden-import/' + expected
        assert snapshot(sandbox / 'internal' / expected) == before
        with pytest.raises(NetworkTransferError, match='already exists'):
            NetworkRequest(renamed, '127.0.0.1', token, port=port, package_name=override).run()
    assert snapshot(renamed) == before


@pytest.mark.parametrize('name', ['', '../escape', 'CON', '.hidden', 'x' * 121, 123])
@pytest.mark.parametrize('override', [False, True])
def test_invalid_instance_names_or_overrides_fail_before_network(built_package, name, override):
    if not override:
        set_instance_name(built_package, name)
    with patch('socket.socket') as socket_factory, pytest.raises(ValueError):
        NetworkRequest(built_package, '127.0.0.1', 'ab' * 32,
                       package_name=name if override else None).run()
    socket_factory.assert_not_called()


def test_legacy_package_name_falls_back_to_folder(receiver, built_package):
    path = built_package / 'metadata/instance-name.json'
    path.unlink()
    document = json.loads((built_package / 'package.json').read_text())
    del document['files']['metadata/instance-name.json']
    (built_package / 'package.json').write_text(json.dumps(document))
    with receiver() as (sandbox, port, token):
        result = NetworkRequest(built_package, '127.0.0.1', token, port=port).run()
        assert result.destination.endswith('/' + built_package.name)


def test_cli_uses_recorded_instance_name_without_name_argument(receiver, built_package, monkeypatch, capsys):
    set_instance_name(built_package, 'CLI Instance')
    with receiver() as (sandbox, port, token):
        monkeypatch.setenv('PLAYDEN_PS5_TOKEN', token)
        monkeypatch.setattr('sys.argv', ['packager', 'transfer-ps5', str(built_package), '--host', '127.0.0.1', '--port', str(port)])
        assert main() == 0
        assert (sandbox / 'internal/CLI Instance').is_dir()
    assert '/data/playden-import/CLI Instance' in capsys.readouterr().out


def test_receiver_metrics_arrive_on_authenticated_connection(receiver, built_package):
    logs = []
    with receiver('--diagnostics') as (sandbox, port, token):
        result = NetworkRequest(built_package, '127.0.0.1', token, port=port).run(diagnostics=logs.append)
        values = result.receiver_metrics
        assert values is not None and values['published'] == 1
        assert values['bytes'] == result.bytes_copied and values['files'] == result.files
        assert values['permissions_s'] >= 0 and values['verify_s'] >= 0
        assert logs[0]['receiver_bytes'] == result.bytes_copied
        assert token not in json.dumps(values) and str(built_package) not in json.dumps(values)


@pytest.mark.parametrize('fault', ['disconnect', 'length', 'schema', 'counters', 'authentication'])
def test_lost_or_invalid_optional_metrics_never_revoke_confirmed_publication(receiver, built_package, fault):
    import struct
    from minecraft_playden.network_protocol import GET_METRICS
    original_send, original_ack = Session.send, Session.ack
    metrics_requested = False
    def send(session, kind, body=b''):
        nonlocal metrics_requested
        original_send(session, kind, body)
        metrics_requested |= kind == GET_METRICS
    def ack(session):
        if metrics_requested:
            if fault in ('disconnect', 'authentication'):
                raise NetworkTransferError(f'synthetic {fault}')
            if fault == 'length':
                return b'invalid'
            return struct.pack('!B19Q', 2 if fault == 'schema' else 1, *([0] * 19))
        return original_ack(session)
    with receiver('--diagnostics') as (sandbox, port, token):
        with patch.object(Session, 'send', send), patch.object(Session, 'ack', ack):
            result = NetworkRequest(built_package, '127.0.0.1', token, port=port).run()
        assert metrics_requested and result.receiver_metrics is None
        assert (sandbox / 'internal' / built_package.name / 'recipe.json').is_file()


def test_custom_name_can_override_invalid_recorded_instance_name(receiver, built_package):
    set_instance_name(built_package, '../not-a-package-name')
    before = snapshot(built_package)
    with receiver() as (sandbox, port, token):
        result = NetworkRequest(built_package, '127.0.0.1', token, port=port, package_name='Valid Override').run()
        assert result.destination == '/data/playden-import/Valid Override'
    assert snapshot(built_package) == before


@pytest.mark.parametrize('buffered', [False, True])
@pytest.mark.parametrize('pipeline', [False, True])
def test_profile_accounts_for_wall_time_and_preserves_all_published_bytes(receiver, built_package, buffered, pipeline):
    from minecraft_playden.network_metrics import PROFILE_PHASES
    original = snapshot(built_package)
    options = ['--diagnostics'] + ([] if buffered else ['--unbuffered-frames'])
    with receiver(*options) as (sandbox, port, token):
        result = NetworkRequest(built_package, '127.0.0.1', token, port=port, pipeline_files=pipeline).run()
        profile = result.receiver_metrics
        assert profile['profile_version'] == 2 and profile['buffered_reads'] == int(buffered)
        assert abs(sum(profile[name] for name in PROFILE_PHASES) + profile['unattributed_s'] - profile['wall_s']) < .001
        assert profile['entry_acks'] == profile['entry_frames']
        assert profile['end_acks'] == result.files
        assert profile['clock_probe_valid'] == 1 and profile['clock_calls'] > profile['frames']
        assert profile['parent_walk_calls'] >= 3 * result.files
        assert profile['dir_open_calls'] > 0 and profile['write_calls'] > 0
        assert snapshot(sandbox / 'internal' / built_package.name) == original
    assert snapshot(built_package) == original


@pytest.mark.parametrize('fault', ['cancel', 'space', 'permission'])
def test_pipelined_failure_or_cancellation_never_publishes(receiver, built_package, fault):
    import errno
    token = CancellationToken()
    options = (['--test-write-limit', '0'] if fault == 'space' else
               ['--test-permission-errno', str(errno.EPERM)] if fault == 'permission' else [])
    def progress(event):
        if fault == 'cancel' and event.stage == 'network-verify': token.cancel()
    before = snapshot(built_package)
    with receiver(*options) as (sandbox, port, pairing):
        with pytest.raises((NetworkTransferError, OperationCancelled)):
            NetworkRequest(built_package, '127.0.0.1', pairing, port=port, pipeline_files=True).run(progress, token)
        wait_clean(sandbox)
        assert not (sandbox / 'internal' / built_package.name).exists()
    assert snapshot(built_package) == before


def test_profile_extension_failure_retains_valid_schema_one_metrics(receiver, built_package):
    from minecraft_playden.network_protocol import GET_PROFILE
    original = Session.send
    def send(session, kind, body=b''):
        if kind == GET_PROFILE:
            raise NetworkTransferError('Original receiver closes after schema 1')
        return original(session, kind, body)
    with receiver('--diagnostics') as (sandbox, port, token), patch.object(Session, 'send', send):
        result = NetworkRequest(built_package, '127.0.0.1', token, port=port).run()
        assert result.receiver_metrics['bytes'] == result.bytes_copied
        assert 'profile_version' not in result.receiver_metrics
        assert (sandbox / 'internal' / built_package.name).is_dir()


@pytest.mark.parametrize('fault', ['length', 'schema', 'counter', 'ledger', 'authentication'])
def test_invalid_profile_is_discarded_without_losing_schema_one_metrics(receiver, built_package, fault):
    from minecraft_playden.network_protocol import GET_PROFILE
    import struct
    original_send, original_ack = Session.send, Session.ack
    last = None
    def send(session, kind, body=b''):
        nonlocal last
        last = kind
        return original_send(session, kind, body)
    def ack(session):
        body = original_ack(session)
        if last == GET_PROFILE:
            if fault == 'authentication':
                raise NetworkTransferError('Receiver message authentication failed')
            if fault == 'length': return body[:-1]
            if fault == 'schema': return b'\x03' + body[1:]
            values = list(struct.unpack('!B61Q', body))
            if fault == 'counter': values[2] += 1  # bytes, after published
            if fault == 'ledger': values[8] += 2000000  # wall microseconds
            return struct.pack('!B61Q', *values)
        return body
    with receiver('--diagnostics') as (sandbox, port, token):
        with patch.object(Session, 'send', send), patch.object(Session, 'ack', ack):
            result = NetworkRequest(built_package, '127.0.0.1', token, port=port).run()
        assert result.receiver_metrics['published'] == 1
        assert 'profile_version' not in result.receiver_metrics
        assert (sandbox / 'internal' / built_package.name).is_dir()
