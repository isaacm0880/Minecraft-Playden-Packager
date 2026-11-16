import hashlib
import hmac
import socket
import threading

import pytest

from minecraft_playden.network_protocol import (
    ACK, HEADER, MAGIC, MAX_FRAME, NetworkTransferError, Session,
    lan_address, pairing_key, safe_relative,
)
from minecraft_playden.operations import CancellationToken, OperationCancelled


@pytest.mark.parametrize('name', ['../x', '/x', 'a/../b', 'a//b', 'x\\y', 'a:stream', 'CON', 'x.', 'a\x00b'])
def test_paths_reject_traversal_and_nonportable_names(name):
    with pytest.raises(ValueError):
        safe_relative(name)


def test_unicode_paths_and_lan_only_addresses():
    assert safe_relative('instances/default/minecraft/saves/世界/level.dat')
    assert lan_address('192.168.1.10') == '192.168.1.10'
    for host in ('8.8.8.8', '0.0.0.0', 'example.com', '::1'):
        with pytest.raises(ValueError):
            lan_address(host)
    with pytest.raises(ValueError):
        pairing_key('short')


def test_mutual_authentication_and_authenticated_reply():
    left, right = socket.socketpair()
    key, server_nonce = bytes(range(32)), bytes(range(32, 64))
    def receiver():
        with right:
            right.sendall(MAGIC + server_nonce)
            client, tag = right.recv(32), right.recv(32)
            assert tag == hmac.digest(key, b'client' + server_nonce + client, 'sha256')
            right.sendall(hmac.digest(key, b'server' + server_nonce + client, 'sha256'))
            session_key = hmac.digest(key, b'session' + server_nonce + client, 'sha256')
            header = HEADER.pack(ACK, 2, 0)
            right.sendall(header + b'ok' + hmac.digest(session_key, b'S' + header + b'ok', 'sha256'))
    worker = threading.Thread(target=receiver)
    worker.start()
    with left:
        session = Session(left, CancellationToken())
        session.authenticate(key)
        assert session.ack() == b'ok'
    worker.join(2)
    assert not worker.is_alive()


@pytest.mark.parametrize('mode', ['mac', 'sequence', 'length'])
def test_frames_reject_tampering_replay_and_oversized_input(mode):
    left, right = socket.socketpair()
    with left, right:
        session = Session(left, CancellationToken())
        session.key = bytes(32)
        header = HEADER.pack(ACK, MAX_FRAME + 1 if mode == 'length' else 2, int(mode == 'sequence'))
        right.sendall(header + b'ok' + bytes(32))
        with pytest.raises(NetworkTransferError):
            session.ack()


def test_wait_is_cancellable_and_has_a_deadline():
    left, right = socket.socketpair()
    with left, right:
        token = CancellationToken()
        session = Session(left, token, timeout=.01)
        with pytest.raises(NetworkTransferError, match='timed out'):
            session.read(1)
        token.cancel()
        with pytest.raises(OperationCancelled):
            session.read(1)


def test_client_frame_vector():
    left, right = socket.socketpair()
    with left, right:
        session = Session(left, CancellationToken())
        session.key = bytes(range(32))
        session.send(1, b'payload')
        expected = HEADER.pack(1, 7, 0) + b'payload'
        assert right.recv(1024) == expected + hmac.digest(session.key, b'C' + expected, hashlib.sha256)
