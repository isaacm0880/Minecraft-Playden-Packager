"""Lifecycle and PAKE security against the actual C receiver. Loopback only."""
import contextlib
from dataclasses import dataclass, field, replace
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import threading
import time
from unittest.mock import patch

import pytest

from minecraft_playden.operations import CancellationToken
from minecraft_playden.network_protocol import Session, pairing_key, NetworkTransferError
from minecraft_playden.receiver_manager import (
    MANAGER_MAGIC, VERSION, STATUS, STOP, PairedReceiver, ReceiverInfo, ReceiverError,
    UnsupportedReceiver, identify, pair_receiver, receiver_status, stop_receiver,
    _client_secret, _info, _control_key, N, prepare_elf, decode_bootstrap, Deployment,
)
from test_network_receiver import receiver_binary, begin, entry, wait_clean


@dataclass
class Managed:
    process: subprocess.Popen
    sandbox: Path
    port: int
    control_port: int
    master: bytes = field(repr=False)
    code: str = field(repr=False)
    identity: bytes = field(repr=False)
    lines: list[str] = field(repr=False)

    @property
    def owner(self):
        return PairedReceiver('127.0.0.1', ReceiverInfo(VERSION, self.identity, self.port, self.control_port), self.master, self.code)


@pytest.fixture
def managed(receiver_binary, tmp_path):
    @contextlib.contextmanager
    def start(*options, binary=None):
        sandbox = tmp_path / f'managed-{len(list(tmp_path.glob("managed-*")))}'
        sandbox.mkdir()
        read_fd, write_fd = os.pipe()
        process = subprocess.Popen([str(binary or receiver_binary), '--test-root', str(sandbox),
            '--bind', '127.0.0.1', '--port', '0', '--control-port', '0',
            '--test-credentials-fd', str(write_fd), *options], pass_fds=(write_fd,),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        os.close(write_fd)
        ready, lines = queue.Queue(), []
        def drain():
            first = True
            for line in process.stdout:
                if first:
                    ready.put(line); first = False
                lines.append(line)
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            banner = ready.get(timeout=10)
            match = re.fullmatch(r'PDLAN/2 address=127.0.0.1 port=(\d+) control_port=(\d+) version=v0.4-manager-2\n', banner)
            assert match, 'Managed receiver did not start'
            secret = os.read(read_fd, 70)
            assert len(secret) == 70
            yield Managed(process, sandbox, int(match[1]), int(match[2]), secret[:32], secret[32:38].decode(), secret[38:], lines)
        finally:
            os.close(read_fd)
            if process.poll() is None:
                process.terminate()
            try:
                _, error = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); _, error = process.communicate(timeout=5)
            reader.join(1)
            assert process.returncode == 0, error
    return start


def test_pair_uses_strong_derived_credential_then_authenticated_stop_returns_normally(managed):
    with managed() as running:
        public = identify('127.0.0.1', running.control_port)
        assert public.version == VERSION and public.port == running.port
        paired = pair_receiver('127.0.0.1', running.code, running.control_port)
        assert len(paired.key) == 32 and paired.key != running.master
        assert running.code not in repr(paired) and running.master.hex() not in repr(paired)
        assert paired.info.session_id == running.identity
        assert receiver_status(paired).port == running.port
        with pytest.raises(ReceiverError, match='already used'):
            pair_receiver('127.0.0.1', running.code, running.control_port)
        stop_receiver(paired)
        assert running.process.wait(timeout=5) == 0
        assert not (running.sandbox / 'internal').exists()
        assert running.master.hex() not in ''.join(running.lines)


def test_wrong_codes_exhaust_global_five_attempt_budget_without_writes(managed):
    with managed() as running:
        wrong = '123456' if running.code != '123456' else '654321'
        for _ in range(5):
            with pytest.raises(ReceiverError, match='not confirmed'):
                pair_receiver('127.0.0.1', wrong, running.control_port)
        with pytest.raises(ReceiverError, match='locked'):
            pair_receiver('127.0.0.1', running.code, running.control_port)
        assert receiver_status(running.owner).version == VERSION
        assert not list(running.sandbox.iterdir())


def test_pairing_expiry_does_not_revoke_high_entropy_owner_credentials(managed):
    with managed('--test-pairing-ttl', '0') as running:
        with pytest.raises(ReceiverError, match='expired'):
            pair_receiver('127.0.0.1', running.code, running.control_port)
        assert receiver_status(running.owner).session_id == running.identity
        stop_receiver(running.owner)


@pytest.mark.parametrize('bad', [bytes(256), N.to_bytes(256, 'big'), b'\xff' * 256])
def test_invalid_srp_public_values_cannot_pair_or_write(managed, bad):
    with managed() as running, socket.create_connection(('127.0.0.1', running.control_port)) as stream:
        session = Session(stream, CancellationToken(), timeout=3)
        assert session.read(40)[:8] == MANAGER_MAGIC
        session.write(b'P'); assert session.read(1) == b'\x01'
        session.write(bad)
        with pytest.raises(OSError):
            session.read(16)
        assert receiver_status(running.owner).version == VERSION
        assert not list(running.sandbox.iterdir())


def test_wrong_authentication_and_changed_session_cannot_stop(managed):
    with managed() as running:
        wrong = replace(running.owner, key=b'\x00' * 32)
        with pytest.raises(OSError):
            stop_receiver(wrong)
        changed = replace(running.owner, info=replace(running.owner.info, session_id=b'\x00' * 32))
        with pytest.raises(ReceiverError, match='session changed'):
            stop_receiver(changed)
        assert receiver_status(running.owner).version == VERSION


def test_stop_during_active_transfer_is_refused_without_interrupting_it(managed):
    with managed() as running, socket.create_connection(('127.0.0.1', running.port)) as stream:
        session = Session(stream, CancellationToken(), timeout=3)
        session.authenticate(running.master)
        begin(session, 1, 0)
        assert receiver_status(running.owner).busy
        with pytest.raises(ReceiverError, match='busy'):
            stop_receiver(running.owner)
        entry(session, 'extras')
        assert running.process.poll() is None
        stream.close()
        wait_clean(running.sandbox)


def test_stop_rechecks_busy_after_issuing_an_idle_lease(managed):
    with managed() as running, socket.create_connection(('127.0.0.1', running.control_port)) as control:
        session = Session(control, CancellationToken(), timeout=3)
        hello = session.read(40); session.write(b'A'); session.authenticate(_control_key(running.owner))
        session.send(STATUS); info = _info(session.ack(), hello[8:], running.control_port)
        with socket.create_connection(('127.0.0.1', running.port)) as data:
            transfer = Session(data, CancellationToken(), timeout=3)
            transfer.authenticate(running.master)
            begin(transfer, 1, 0)
            session.send(STOP, info.session_id + info.stop_lease)
            with pytest.raises(NetworkTransferError, match='busy'):
                session.ack()
            entry(transfer, 'extras')
        assert running.process.poll() is None


def test_stale_stop_lease_cannot_stop_a_later_transfer(managed):
    with managed() as running, socket.create_connection(('127.0.0.1', running.control_port)) as control:
        session = Session(control, CancellationToken(), timeout=3)
        hello = session.read(40); session.write(b'A'); session.authenticate(_control_key(running.owner))
        session.send(STATUS); info = _info(session.ack(), hello[8:], running.control_port)
        time.sleep(2.1)
        session.send(STOP, info.session_id + info.stop_lease)
        with pytest.raises(OSError):
            session.ack()
        assert receiver_status(running.owner).version == VERSION


def test_replayed_authentication_is_rejected_on_a_new_control_connection(managed):
    with managed() as running:
        writes = []
        original = Session.write
        def capture(session, data):
            writes.append(bytes(data)); return original(session, data)
        with patch.object(Session, 'write', capture):
            assert receiver_status(running.owner).version == VERSION
        # 'A' is followed by the client nonce+proof from the first session.
        with socket.create_connection(('127.0.0.1', running.control_port)) as stream:
            session = Session(stream, CancellationToken(), timeout=3)
            session.read(40); session.write(writes[0]); session.read(40); session.write(writes[1])
            with pytest.raises(OSError):
                session.read(32)
        assert receiver_status(running.owner).version == VERSION


def test_pairing_credential_can_transfer_and_preserve_import_verification(managed, tmp_path):
    from lan_fixtures import build_package
    from minecraft_playden.network_transfer import NetworkRequest
    package = build_package(tmp_path / 'export')
    with managed() as running:
        paired = pair_receiver('127.0.0.1', running.code, running.control_port)
        result = NetworkRequest(package, '127.0.0.1', paired.key.hex(), port=running.port).run()
        assert (running.sandbox / 'internal' / result.destination.split('/')[-1] / 'playden/recipe.json').is_file()
        stop_receiver(paired)
        assert package.is_dir()


def test_rsa_startup_envelope_is_only_decryptable_by_the_launching_gui(managed, receiver_binary, tmp_path):
    pytest.importorskip('cryptography')
    from cryptography.hazmat.primitives.asymmetric import rsa
    private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    public = private.public_key().public_numbers().n.to_bytes(384, 'big')
    patched = tmp_path / 'receiver.elf'
    patched.write_bytes(prepare_elf(receiver_binary, public, 9025, 9026)); patched.chmod(0o700)
    with managed(binary=patched) as running:
        deadline = time.monotonic() + 5
        while not any(line.startswith('PDLAN2_BOOTSTRAP ') for line in running.lines) and time.monotonic() < deadline:
            time.sleep(.01)
        line = next(line for line in running.lines if line.startswith('PDLAN2_BOOTSTRAP '))
        encrypted = bytes.fromhex(line.split()[1])
        paired = decode_bootstrap(private, encrypted, '127.0.0.1', running.port, running.control_port)
        assert paired.key == running.master and paired.pairing_code == running.code
        assert receiver_status(paired).session_id == running.identity
        another = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        with pytest.raises(ReceiverError, match='could not be verified'):
            decode_bootstrap(another, encrypted, '127.0.0.1', running.port, running.control_port)
        with pytest.raises(ReceiverError):
            decode_bootstrap(private, encrypted[:-1] + bytes([encrypted[-1] ^ 1]), '127.0.0.1', running.port, running.control_port)


def test_srp_client_and_c_server_match_independent_pysrp_vectors(receiver_binary, tmp_path):
    import json
    root = Path(__file__).resolve().parents[1]
    vectors = json.loads((root / 'tests/fixtures/pairing/srp-vectors.json').read_text())['vectors']
    source = tmp_path / 'vectors.c'
    source.write_text(r'''
#define main receiver_main
#include "main.c"
#undef main
static void decode(const char *text, uint8_t *out, size_t n) {
    for (size_t i=0; i<n; ++i) { unsigned v; sscanf(text+i*2, "%2x", &v); out[i]=(uint8_t)v; }
}
int main(int argc, char **argv) {
    if (argc != 5) return 2;
    uint8_t salt[16], secret[32], A[256], B[256], v[256], key[32]; char output[513];
    decode(argv[2],salt,16); decode(argv[3],secret,32); decode(argv[4],A,256);
    if (!srp_public(argv[1],salt,secret,B,v) || !srp_shared(A,B,v,secret,key)) return 1;
    hex(B,256,output); puts(output); hex(key,32,output); puts(output);
    return 0;
}
''')
    binary = tmp_path / 'vectors'
    subprocess.run(['cc', '-std=c11', '-D_GNU_SOURCE', '-DHOST_TEST', '-O2', '-I', str(root / 'ps5/receiver'),
                    str(source), str(root / 'ps5/receiver/vendor/sha-256.c'), '-pthread', '-o', str(binary)],
                   check=True, capture_output=True)
    for vector in vectors:
        actual = _client_secret(vector['code'], bytes.fromhex(vector['salt']), bytes.fromhex(vector['A']),
                                bytes.fromhex(vector['B']), int(vector['a'], 16))
        assert actual.hex() == vector['key']
        # The executable prints only public, synthetic regression-vector results.
        result = subprocess.check_output([str(binary), vector['code'], vector['salt'], vector['b'], vector['A']], text=True).splitlines()
        assert result == [vector['B'], vector['key']]


def test_replaying_pair_proof_against_a_fresh_exchange_fails(managed):
    import hashlib
    import hmac
    with managed() as running:
        private = 1 << 255
        A = pow(2, private, N).to_bytes(256, 'big')
        old = None
        for attempt in range(2):
            with socket.create_connection(('127.0.0.1', running.control_port)) as stream:
                session = Session(stream, CancellationToken(), timeout=3)
                hello = session.read(40); session.write(b'P'); assert session.read(1) == b'\x01'
                session.write(A); salt, B = session.read(16), session.read(256)
                key = _client_secret(running.code, salt, A, B, private)
                transcript = hashlib.sha256(hello[8:] + salt + A + B).digest()
                proof = hmac.digest(key, b'client-pair-v2' + transcript, 'sha256')
                if attempt == 0:
                    old = proof
                    continue  # Disconnect before confirming; code remains available.
                assert proof != old
                session.write(old)
                with pytest.raises(OSError):
                    session.read(32)
        paired = pair_receiver('127.0.0.1', running.code, running.control_port)
        assert receiver_status(paired).version == VERSION


def test_source_manifest_pins_bearssl_arithmetic_and_license():
    import hashlib
    import json
    root = Path(__file__).resolve().parents[1] / 'ps5/receiver/vendor/bearssl'
    manifest = json.loads((root / 'manifest.json').read_text())
    for path, digest in manifest['files'].items():
        assert hashlib.sha256((root / path).read_bytes()).hexdigest() == digest
    assert (root / 'LICENSE.txt').is_file()


def test_prepare_elf_rejects_legacy_ambiguous_and_truncated_slots(tmp_path):
    from minecraft_playden.receiver_manager import BOOT_MARKER
    public = bytes([128]) + bytes(382) + b'\x01'
    path = tmp_path / 'bad.elf'
    for data in (b'not ELF', b'\x7fELF\x02'+bytes(400), b'\x7fELF\x02'+BOOT_MARKER,
                 b'\x7fELF\x02'+BOOT_MARKER+bytes(388)+BOOT_MARKER+bytes(388)):
        path.write_bytes(data)
        with pytest.raises(UnsupportedReceiver):
            prepare_elf(path, public, 9025, 9026)


@pytest.mark.parametrize('code', ['12345', '1234567', '１２３４５６', ' 12345', 'abc123'])
def test_invalid_codes_never_open_a_connection(code):
    with patch('socket.create_connection') as connect, pytest.raises(ValueError, match='six digits'):
        pair_receiver('127.0.0.1', code)
    connect.assert_not_called()


def test_new_cli_pairs_without_exposing_code_or_key(managed, tmp_path, capsys):
    from minecraft_playden.cli import main
    from lan_fixtures import build_package
    package = build_package(tmp_path / 'cli-export')
    with managed() as running, patch('getpass.getpass', return_value=running.code) as prompt:
        with patch('sys.argv', ['minecraft-playden', 'transfer-ps5', str(package), '--host', '127.0.0.1', '--pair',
                                '--control-port', str(running.control_port)]):
            assert main() == 0
        assert 'six-digit' in prompt.call_args.args[0]
        output = capsys.readouterr()
        assert 'received and verified' in output.out
        assert running.code not in output.out + output.err
        assert running.master.hex() not in output.out + output.err


def test_management_authentication_does_not_accept_the_transfer_key_directly(managed):
    with managed() as running, socket.create_connection(('127.0.0.1', running.control_port)) as stream:
        session = Session(stream, CancellationToken(), timeout=3)
        session.read(40); session.write(b'A')
        with pytest.raises(OSError):
            session.authenticate(running.master)
        assert receiver_status(running.owner).version == VERSION


def test_manager_clocks_fail_closed(receiver_binary, tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / 'clock.c'
    source.write_text(r'''
#define clock_gettime test_clock_failure
#define main receiver_main
#include "main.c"
#undef main
#undef clock_gettime
int test_clock_failure(clockid_t clock, struct timespec *value) {
    (void)clock; (void)value; errno=EINVAL; return -1;
}
int main(void) {
    char byte=0;
    if (manager_now() >= 0) return 1;
    if (control_io(-1,&byte,1,0)) return 2;
    return 0;
}
''')
    binary = tmp_path / 'clock'
    subprocess.run(['cc', '-std=c11', '-D_GNU_SOURCE', '-DHOST_TEST', '-O2', '-I', str(root/'ps5/receiver'),
                    str(source), str(root/'ps5/receiver/vendor/sha-256.c'), '-pthread', '-o', str(binary)],
                   check=True, capture_output=True)
    subprocess.run([str(binary)], check=True, timeout=5)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX fake SDK executable')
def test_start_refuses_existing_receiver_before_invoking_loader(managed, tmp_path):
    with managed() as running:
        sdk = tmp_path / 'sdk'; (sdk/'bin').mkdir(parents=True)
        deployer=sdk/'bin/prospero-deploy'; deployer.write_text('#!/bin/sh\nexit 99\n'); deployer.chmod(0o700)
        elf = tmp_path / 'receiver.elf'; elf.write_bytes(b'irrelevant; already-running check comes first')
        with patch('minecraft_playden.receiver_manager.shutil.which', return_value='/fake/socat'), \
             patch('minecraft_playden.receiver_manager.subprocess.Popen') as launch:
            with pytest.raises(ReceiverError, match='already listening'):
                Deployment().start('127.0.0.1', sdk, elf, port=running.port, control_port=running.control_port)
            launch.assert_not_called()
        assert receiver_status(running.owner).version == VERSION


@pytest.mark.skipif(os.name != 'posix', reason='POSIX fake SDK executable')
@pytest.mark.parametrize('slow', [False, True])
def test_loader_without_startup_output_never_claims_success_or_reveals_secrets(receiver_binary, tmp_path, slow):
    pytest.importorskip('cryptography')
    sdk = tmp_path/'sdk'; (sdk/'bin').mkdir(parents=True)
    deployer=sdk/'bin/prospero-deploy'
    # Simulate unsupported stdout forwarding, including legacy secret output.
    secret='0123456789abcdef'*4
    deployer.write_text('#!/bin/sh\nprintf "PDLAN/1 token='+secret+'\\n"\n' + ('sleep 5\n' if slow else 'exit 1\n'))
    deployer.chmod(0o700)
    deployment = Deployment()
    with patch('minecraft_playden.receiver_manager.shutil.which', return_value='/fake/socat'), \
         patch('minecraft_playden.receiver_manager._connect', side_effect=ConnectionRefusedError()), \
         patch('minecraft_playden.receiver_manager.STARTUP_TIMEOUT', .15):
        with pytest.raises(ReceiverError) as error:
            deployment.start('127.0.0.1', sdk, receiver_binary)
    assert 'loader' in str(error.value) or 'Loader' in str(error.value)
    assert secret not in str(error.value)
    assert deployment.attempted and deployment.process is None and deployment.temporary is None
