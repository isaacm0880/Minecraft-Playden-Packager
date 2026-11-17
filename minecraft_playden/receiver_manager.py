"""Receiver-specific control, SRP-6a pairing and encrypted SDK startup credentials.

Management v2 is separate from the unchanged PDLAN/1 transfer protocol.
Secrets live only in memory; neither payload output nor SDK diagnostics are logged.
"""
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from .network_protocol import MAGIC, PORT, Session, NetworkTransferError, lan_address
from .operations import CancellationToken, OperationCancelled
from .process_environment import desktop_environment

STARTUP_TIMEOUT = 20.0
CONTROL_PORT = 9026
LOADER_PORT = 9021
MANAGER_MAGIC = b'PDMGR2\r\n'
VERSION = 'v0.4-manager-2'
BOOT_MARKER = b'PDLAN-BOOTSTRAP-RSA-3072-V2:'
BOOT_LABEL = b'Playden receiver bootstrap v2'
STATUS, STOP = 9, 10
# RFC 5054 Appendix A, 2048-bit group. Fixed suite: SRP-6a/SHA-256, g=2.
N_HEX = 'AC6BDB41324A9A9BF166DE5E1389582FAF72B6651987EE07FC3192943DB56050A37329CBB4A099ED8193E0757767A13DD52312AB4B03310DCD7F48A9DA04FD50E8083969EDB767B0CF6095179A163AB3661A05FBD5FAAAE82918A9962F0B93B855F97993EC975EEAA80D740ADBF4FF747359D041D5C33EA71D281E446B14773BCA97B43A23FB801676BD207A436C6481F1D2B9078717461A5B9D32E688F87748544523B524B0D57D5EA77A2775D2ECFA032CFBDBF52FB3786160279004E57AE6AF874E7303CE53299CCC041C7BC308D82A5698F3A8D0C38271AE35F8E9DBFBB694B5C803D89F7AE435DE236D525F54759B65E372FCD68EF20FA7111F9E4AFF73'
N = int(N_HEX, 16)
G = 2
PAD_G = G.to_bytes(256, 'big')
MULTIPLIER = int.from_bytes(hashlib.sha256(N.to_bytes(256, 'big') + PAD_G).digest(), 'big')


@dataclass(frozen=True)
class ReceiverInfo:
    version: str
    session_id: bytes
    port: int
    control_port: int
    busy: bool = False
    stop_lease: bytes = field(default=b'', repr=False)


@dataclass(frozen=True)
class PairedReceiver:
    host: str
    info: ReceiverInfo
    key: bytes = field(repr=False)
    pairing_code: str | None = field(default=None, repr=False)


class ReceiverError(NetworkTransferError):
    """Actionable lifecycle error; never includes captured payload output."""


class UnsupportedReceiver(ReceiverError):
    pass


def _connect(host: str, port: int, token: CancellationToken) -> socket.socket:
    token.check()
    if not 1 <= port <= 65535:
        raise ValueError('Choose a TCP port between 1 and 65535.')
    return socket.create_connection((lan_address(host), port), timeout=2)


def _hello(stream: socket.socket, token: CancellationToken) -> tuple[Session, bytes]:
    session = Session(stream, token, timeout=3)
    hello = session.read(40)
    if hello[:8] != MANAGER_MAGIC:
        raise UnsupportedReceiver('This receiver does not support management v2. Start the updated ELF; legacy receivers remain available through the CLI.')
    return session, hello[8:]


def _info(body: bytes, identity: bytes, control_port: int) -> ReceiverInfo:
    try:
        value = json.loads(body)
        if not isinstance(value, dict) or type(value.get('protocol')) is not int or value['protocol'] != 2 or value.get('version') != VERSION:
            raise UnsupportedReceiver('Unsupported receiver version. Use the matching receiver ELF for this packager.')
        sid = bytes.fromhex(value['session_id'])
        lease = bytes.fromhex(value.get('stop_lease', ''))
        port, control = value['port'], value['control_port']
        if (len(sid) != 32 or not hmac.compare_digest(sid, identity) or len(lease) not in (0, 16)
                or type(port) is not int or not 1 <= port <= 65535 or type(control) is not int
                or control != control_port or type(value['busy']) is not bool):
            raise ValueError()
        return ReceiverInfo(value['version'], sid, port, control, value['busy'], lease)
    except UnsupportedReceiver:
        raise
    except (KeyError, ValueError, TypeError, UnicodeError) as exc:
        raise ReceiverError('Receiver returned invalid identity or status information.') from exc


def identify(host: str, control_port: int = CONTROL_PORT,
             cancellation: CancellationToken | None = None) -> ReceiverInfo:
    """Public discovery is a hint only; it never authorizes Send or Stop."""
    token = cancellation or CancellationToken()
    with _connect(host, control_port, token) as stream:
        session, identity = _hello(stream, token)
        session.write(b'I')
        length = int.from_bytes(session.read(2), 'big')
        if not 1 <= length <= 768:
            raise ReceiverError('Invalid receiver discovery response.')
        return _info(session.read(length), identity, control_port)


def _control_key(paired: PairedReceiver) -> bytes:
    return hmac.digest(paired.key, b'control-v2' + paired.info.session_id, 'sha256')


def _authenticated(stream: socket.socket, paired: PairedReceiver, token: CancellationToken) -> Session:
    session, identity = _hello(stream, token)
    if not hmac.compare_digest(identity, paired.info.session_id):
        raise ReceiverError('Receiver session changed. Enter the new six-digit code to pair again.')
    session.write(b'A')
    session.authenticate(_control_key(paired))
    session.send(STATUS)
    return session


def receiver_status(paired: PairedReceiver, cancellation: CancellationToken | None = None) -> ReceiverInfo:
    token = cancellation or CancellationToken()
    with _connect(paired.host, paired.info.control_port, token) as stream:
        session = _authenticated(stream, paired, token)
        return _info(session.ack(), paired.info.session_id, paired.info.control_port)


def _client_secret(code: str, salt: bytes, public_a: bytes, public_b: bytes, private_a: int) -> bytes:
    b = int.from_bytes(public_b, 'big')
    if len(public_b) != 256 or b == 0 or b >= N:
        raise ReceiverError('Invalid SRP server public value; pairing refused.')
    u = int.from_bytes(hashlib.sha256(public_a + public_b).digest(), 'big')
    if not u:
        raise ReceiverError('Invalid SRP scrambling parameter; pairing refused.')
    x = int.from_bytes(hashlib.sha256(salt + hashlib.sha256(b'playden:' + code.encode('ascii')).digest()).digest(), 'big')
    secret = pow((b - MULTIPLIER * pow(G, x, N)) % N, private_a + u * x, N)
    if not secret:
        raise ReceiverError('Invalid SRP shared secret; pairing refused.')
    return hashlib.sha256(secret.to_bytes(256, 'big')).digest()


def pair_receiver(host: str, code: str, control_port: int = CONTROL_PORT,
                  cancellation: CancellationToken | None = None) -> PairedReceiver:
    if re.fullmatch(r'[0-9]{6}', code) is None:
        raise ValueError('Enter exactly six digits from the PS5 receiver notification.')
    host = lan_address(host)
    token = cancellation or CancellationToken()
    private_a = secrets.randbits(256) | (1 << 255)
    public_a = pow(G, private_a, N).to_bytes(256, 'big')
    with _connect(host, control_port, token) as stream:
        session, identity = _hello(stream, token)
        session.write(b'P')
        if session.read(1) != b'\x01':
            raise ReceiverError('Pairing code expired, already used, or locked after five attempts. Restart the receiver manually or use the controller that already authenticated it.')
        session.write(public_a)
        salt, public_b = session.read(16), session.read(256)
        key = _client_secret(code, salt, public_a, public_b, private_a)
        transcript = hashlib.sha256(identity + salt + public_a + public_b).digest()
        candidate = hmac.digest(key, b'credential-v2' + transcript, 'sha256')
        placeholder = ReceiverInfo(VERSION, identity, PORT, control_port)
        paired = PairedReceiver(host, placeholder, candidate, code)
        session.write(hmac.digest(key, b'client-pair-v2' + transcript, 'sha256'))
        try:
            proof = session.read(32)
        except OSError:
            # A lost PAKE receipt is recoverable only if the derived credential
            # authenticates this exact receiver session. Never retry the code.
            try:
                info = receiver_status(paired, token)
            except OSError as exc:
                raise ReceiverError('Pairing was not confirmed. Check the code and receiver; no transfer was started.') from exc
            return PairedReceiver(host, info, candidate, code)
        if not hmac.compare_digest(proof, hmac.digest(key, b'server-pair-v2' + transcript, 'sha256')):
            raise ReceiverError('Receiver pairing proof failed. No credentials were accepted.')
    info = receiver_status(paired, token)
    return PairedReceiver(host, info, candidate, code)


def stop_receiver(paired: PairedReceiver, cancellation: CancellationToken | None = None) -> None:
    """Stop only an identified idle session; never kill payloads or power the PS5."""
    token = cancellation or CancellationToken()
    with _connect(paired.host, paired.info.control_port, token) as stream:
        session = _authenticated(stream, paired, token)
        info = _info(session.ack(), paired.info.session_id, paired.info.control_port)
        if info.busy or len(info.stop_lease) != 16:
            raise ReceiverError('Receiver is busy. Finish or cancel the transfer before stopping it.')
        session.send(STOP, info.session_id + info.stop_lease)
        if session.ack() != b'stopping':
            raise ReceiverError('Receiver did not acknowledge stopping; its state is unknown.')
    # Receipt confirms the request; listener closure confirms the stopped service.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        token.check()
        try:
            identify(paired.host, paired.info.control_port, token)
        except ConnectionRefusedError:
            try:
                with _connect(paired.host, info.port, token):
                    pass
            except ConnectionRefusedError:
                return
            except OSError:
                pass
        except UnsupportedReceiver:
            break
        except OSError:
            pass
        token.wait(.1)
    raise ReceiverError('Stop was requested but listener closure was not confirmed. Receiver status is Unknown; no force-stop was attempted.')


def decode_bootstrap(private_key: Any, encrypted: bytes, host: str,
                     expected_port: int, expected_control_port: int) -> PairedReceiver:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    try:
        plain = private_key.decrypt(encrypted, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=BOOT_LABEL))
        if len(plain) != 82 or plain[:8] != b'PDBOOT2\0':
            raise ValueError()
        port, control = struct.unpack('!HH', plain[78:])
        code = plain[72:78].decode('ascii')
        if re.fullmatch(r'[0-9]{6}', code) is None or (port, control) != (expected_port, expected_control_port):
            raise ValueError()
        info = ReceiverInfo(VERSION, plain[8:40], port, control)
        return PairedReceiver(host, info, plain[40:72], code)
    except (ValueError, UnicodeError) as exc:
        raise ReceiverError('Encrypted receiver startup credentials could not be verified. Pair using the code shown on the PS5.') from exc


def prepare_elf(elf: Path, public_modulus: bytes, port: int, control_port: int) -> bytes:
    if len(public_modulus) != 384 or not public_modulus[0] & 0x80 or not public_modulus[-1] & 1:
        raise ValueError('A 3072-bit RSA public key is required for encrypted startup.')
    if not 1 <= port <= 65535 or not 1 <= control_port <= 65535 or port == control_port:
        raise ValueError('Choose distinct, valid receiver and control ports.')
    if elf.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Receiver ELF is unexpectedly large.')
    data = elf.read_bytes()
    if data[:5] != b'\x7fELF\x02' or data.count(BOOT_MARKER) != 1:
        raise UnsupportedReceiver('Select the management-v2 receiver ELF built from this branch. This ELF has no unique encrypted-startup slot.')
    start = data.index(BOOT_MARKER) + len(BOOT_MARKER)
    end = start + 388
    if end > len(data):
        raise UnsupportedReceiver('Receiver ELF startup slot is truncated.')
    return data[:start] + public_modulus + struct.pack('!HH', port, control_port) + data[end:]


class Deployment:
    """Own the local SDK stream independently of the remote receiver lifecycle."""
    def __init__(self) -> None:
        self.attempted = False
        self.process: subprocess.Popen[bytes] | None = None
        self.temporary: tempfile.TemporaryDirectory[str] | None = None
        self.reader: threading.Thread | None = None

    def start(self, host: str, sdk: Path, elf: Path, loader_port: int = LOADER_PORT,
              port: int = PORT, control_port: int = CONTROL_PORT,
              cancellation: CancellationToken | None = None) -> PairedReceiver:
        token = cancellation or CancellationToken()
        host = lan_address(host)
        if not 1 <= loader_port <= 65535 or loader_port in (port, control_port):
            raise ValueError('The ELF loader needs a distinct valid TCP port (normally 9021).')
        deployer = sdk / ('win/prospero-deploy.cmd' if os.name == 'nt' else 'bin/prospero-deploy')
        if not deployer.is_file():
            raise ReceiverError('PS5 Payload SDK deployer was not found. Select the SDK root containing bin/prospero-deploy (or win/prospero-deploy.cmd).')
        if os.name != 'nt' and not os.access(deployer, os.X_OK):
            raise ReceiverError('The SDK deployer is not executable. Restore its executable permission, or start the receiver externally and Pair.')
        if not elf.is_file():
            raise ReceiverError('Receiver ELF was not found. Build ps5/receiver with the PS5 Payload SDK, then select minecraft_playden_receiver.elf.')
        utility = 'plink.exe' if os.name == 'nt' else 'socat'
        if shutil.which(utility) is None:
            raise ReceiverError(f'The SDK deployer requires {utility} on PATH. Install it, or start the ELF externally and pair with its six-digit code.')
        # Never deploy a duplicate or replace a receiver the manager cannot identify.
        for target in (control_port, port):
            try:
                with _connect(host, target, token):
                    raise ReceiverError('A service is already listening on the receiver ports. Refresh and pair with the existing receiver; stop legacy receivers manually before starting a new ELF.')
            except ConnectionRefusedError:
                pass
            except ReceiverError:
                raise
            except OSError as exc:
                raise ReceiverError('Cannot establish whether a receiver is already running. Check the PS5 IP and LAN; deployment was not attempted.') from exc
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
        except ImportError as exc:
            raise ReceiverError('Encrypted startup requires the GUI dependencies. Install with: python -m pip install -e ".[gui]"') from exc
        token.check()
        private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        try:
            payload = prepare_elf(elf, private.public_key().public_numbers().n.to_bytes(384, 'big'), port, control_port)
        except OSError as exc:
            raise ReceiverError('Cannot read the selected receiver ELF. Check its file permissions and rebuild it if necessary.') from exc
        self.temporary = tempfile.TemporaryDirectory(prefix='playden-receiver-', ignore_cleanup_errors=True)
        path = Path(self.temporary.name) / 'receiver.elf'
        path.write_bytes(payload)  # Public key only; no session credential is stored.
        if os.name == 'nt' and (re.search(r'[\s%&|<>^!()"]', str(path)) or re.search(r'[\s%&|<>^!()"]', str(deployer))):
            self.close()
            raise ReceiverError('The SDK Windows batch deployer does not safely support these SDK/temp paths. Use paths without spaces or shell characters, or start externally and pair.')
        ready: queue.Queue[bytes] = queue.Queue(maxsize=1)
        try:
            self.attempted = True
            self.process = subprocess.Popen([str(deployer), '-h', host, '-p', str(loader_port), str(path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=os.name != 'nt', env=desktop_environment())
            process = self.process
            def read() -> None:
                assert process.stdout is not None
                while True:
                    line = process.stdout.readline(4097)
                    if not line:
                        return
                    # Never retain or display untrusted loader output, including legacy tokens.
                    if re.fullmatch(rb'PDLAN2_BOOTSTRAP [0-9a-f]{768}\r?\n', line):
                        try:
                            ready.put_nowait(bytes.fromhex(line.split()[1].decode('ascii')))
                        except queue.Full:
                            pass
            self.reader = threading.Thread(target=read, daemon=True)
            self.reader.start()
            deadline = time.monotonic() + STARTUP_TIMEOUT
            while time.monotonic() < deadline:
                token.check()
                try:
                    encrypted = ready.get(timeout=.05)
                except queue.Empty:
                    if process.poll() is not None:
                        raise ReceiverError('SDK deployment ended without encrypted receiver credentials. Check that an ELF loader is running on the selected port; some loaders do not forward payload output. Pair manually if the receiver notification appeared.')
                    continue
                paired = decode_bootstrap(private, encrypted, host, port, control_port)
                info = receiver_status(paired, token)
                return PairedReceiver(host, info, paired.key, paired.pairing_code)
            raise ReceiverError('Receiver startup was not confirmed within 20 seconds. Check the loader and the PS5 notification. If the loader does not forward stdout, use the six-digit code to pair; the ELF may still be running.')
        except (OSError, ValueError, OperationCancelled):
            self.close()  # This closes only the local SDK stream, never claims remote exit.
            raise

    def close(self) -> None:
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            try:
                if sys.platform != 'win32':
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
        if process is not None:
            try:
                process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                pass
        if self.reader is not None:
            self.reader.join(timeout=.5)
            if not self.reader.is_alive() and process is not None and process.stdout is not None:
                process.stdout.close()
            self.reader = None
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None
