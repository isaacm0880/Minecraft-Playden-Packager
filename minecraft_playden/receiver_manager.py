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
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from .network_protocol import MAGIC, PORT, Session, NetworkTransferError, lan_address
from .operations import CancellationToken
from .progress import Progress, ProgressCallback
from .process_environment import desktop_environment

STARTUP_TIMEOUT = 20.0
CONTROL_PORT = 9026
LOADER_PORT = 9021
RECEIVER_PREFLIGHT_TIMEOUT = 5.0
STOP_CONFIRM_TIMEOUT = 12.0
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


@dataclass(frozen=True)
class ReceiverConfiguration:
    sdk: Path
    elf: Path
    loader_port: int = LOADER_PORT
    port: int = PORT
    control_port: int = CONTROL_PORT

    @classmethod
    def load(cls, settings: Any = None) -> 'ReceiverConfiguration':
        # Core CLI remains usable without Qt; when installed, read the exact GUI
        # settings store without creating a window or persisting credentials.
        if settings is None:
            try:
                from PySide6.QtCore import QSettings
                settings = QSettings('MinecraftPlayden', 'Packager')
            except ImportError:
                pass
        def value(key: str, default: str) -> str:
            result = str(settings.value('receiver/' + key, default)) if settings is not None else default
            return result if result.strip() else default
        candidates = [Path('/opt/ps5-payload-sdk/ps5-payload-sdk'), Path('/opt/ps5-payload-sdk')]
        deployer = shutil.which('prospero-deploy')
        if deployer:
            candidates.insert(0, Path(deployer).resolve().parent.parent)
        default_sdk = os.environ.get('PS5_PAYLOAD_SDK') or str(next((p for p in candidates if p.is_dir()), candidates[-1]))
        frozen = getattr(sys, '_MEIPASS', None)
        default_elf = ((Path(frozen) / 'receiver/minecraft_playden_receiver.elf') if frozen
                       else Path(__file__).resolve().parent.parent / 'ps5/receiver/minecraft_playden_receiver.elf')
        def port(key: str, default: int) -> int:
            try:
                result = int(value(key, str(default)))
                return result if 1 <= result <= 65535 else default
            except ValueError:
                return default
        return cls(Path(value('sdk', default_sdk)).expanduser(), Path(value('elf', str(default_elf))).expanduser(),
                   port('loaderPort', LOADER_PORT), port('dataPort', PORT), port('controlPort', CONTROL_PORT))


def _stop_local_process(process: subprocess.Popen[bytes]) -> None:
    """Reap a local build/deploy stream; never send a remote STOP command."""
    if process.poll() is None:
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
    try:
        process.wait(timeout=.5)
    except subprocess.TimeoutExpired:
        pass


def ensure_receiver_elf(sdk: Path, elf: Path, token: CancellationToken,
                        progress: ProgressCallback | None = None) -> None:
    """Build missing/stale source-tree receivers locally; never install tools."""
    frozen = getattr(sys, '_MEIPASS', None)
    if frozen and elf.absolute() == (Path(frozen) / 'receiver/minecraft_playden_receiver.elf').absolute():
        _verify_frozen_receiver(Path(frozen), elf)
        return
    manifest_path = elf.parent / 'receiver-manifest.json'
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if not elf.is_file() or hashlib.sha256(elf.read_bytes()).hexdigest() != manifest.get('elf_sha256'):
            raise ReceiverError('Bundled receiver ELF checksum does not match its build manifest')
        frozen = getattr(sys, '_MEIPASS', None)
        if frozen:
            build_info = json.loads((Path(frozen) / 'build-metadata.json').read_text(encoding='utf-8'))
            if manifest.get('source_commit') != build_info.get('source_commit'):
                raise ReceiverError('Bundled receiver ELF is from a different application source commit')
        return
    source = elf.parent
    makefile = source / 'Makefile'
    inputs = [makefile, *source.rglob('*.c'), *source.rglob('*.h'), source.parent / 'diagnostic/recipe_check.h'] if makefile.is_file() else []
    if elf.is_file() and not any(p.is_file() and p.stat().st_mtime_ns > elf.stat().st_mtime_ns for p in inputs):
        return
    if not makefile.is_file() or not (source / 'main.c').is_file():
        raise ReceiverError('Receiver ELF was not found or is stale, and its source Makefile is unavailable. Select a built receiver ELF or the source-tree ps5/receiver path.')
    if not (sdk / 'toolchain/prospero.mk').is_file():
        raise ReceiverError('Building the receiver requires an installed PS5 Payload SDK with toolchain/prospero.mk. No SDK files were downloaded.')
    make = shutil.which('make')
    if make is None:
        raise ReceiverError('Building the receiver requires make on PATH. Install the local build tools or select a built receiver ELF.')
    token.check()
    if progress:
        progress(Progress('receiver-build', 'Building the receiver ELF using the installed PS5 Payload SDK…'))
    with tempfile.TemporaryDirectory(prefix='playden-receiver-build-', dir=source) as temporary, tempfile.TemporaryFile() as log:
        output = Path(temporary) / elf.name
        env = desktop_environment()
        env['PS5_PAYLOAD_SDK'] = str(sdk.resolve())
        process = subprocess.Popen([make, '-C', str(source.resolve()), 'ELF=' + str(output.resolve())],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   env=env, start_new_session=os.name != 'nt')
        try:
            while process.poll() is None:
                token.wait(.05)
            token.check()
            if process.returncode or not output.is_file():
                raise ReceiverError('Receiver build failed. Check the installed SDK compiler/toolchain with make -C ps5/receiver. No deployment was attempted.')
            # The real per-launch RSA key is applied by prepare_elf below. Here
            # validate the format/slot before replacing a previous local ELF.
            prepare_elf(output, b'\x80' + bytes(382) + b'\x01', PORT, CONTROL_PORT)
            output.replace(elf)
        finally:
            _stop_local_process(process)


def verified_bundled_receiver_elf() -> Path | None:
    """Return the frozen receiver only after its ELF and build provenance pass verification."""
    frozen = getattr(sys, '_MEIPASS', None)
    if frozen is None:
        return None
    elf = Path(frozen) / 'receiver/minecraft_playden_receiver.elf'
    try:
        _verify_frozen_receiver(Path(frozen), elf)
    except ReceiverError:
        return None
    return elf


def _verify_frozen_receiver(root: Path, elf: Path) -> None:
    """Fail closed on missing or replaced resources in a frozen desktop build."""
    manifest_path = elf.parent / 'receiver-manifest.json'
    metadata_path = root / 'build-metadata.json'
    if not manifest_path.is_file():
        raise ReceiverError(f'Bundled receiver verification failed: {manifest_path} is missing. '
                            'Reinstall the complete desktop package; no payload was sent.')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ReceiverError(f'Bundled receiver verification failed: cannot read a valid manifest or '
                            f'build metadata ({exc}). Reinstall the complete desktop package; no payload was sent.') from exc
    commit = metadata.get('source_commit') if isinstance(metadata, dict) else None
    if (not isinstance(manifest, dict) or manifest.get('schema') != 1
            or not isinstance(commit, str) or re.fullmatch(r'[0-9a-f]{40}', commit) is None
            or manifest.get('source_commit') != commit
            or manifest.get('source_dirty') is not False
            or not isinstance(manifest.get('elf_sha256'), str)
            or re.fullmatch(r'[0-9a-f]{64}', manifest['elf_sha256']) is None
            or not isinstance(manifest.get('elf_size'), int) or manifest['elf_size'] <= 0):
        raise ReceiverError('Bundled receiver verification failed: invalid manifest or application source '
                            'provenance. Reinstall the complete desktop package; no payload was sent.')
    try:
        if elf.is_symlink() or not elf.is_file() or elf.stat().st_size != manifest['elf_size']:
            raise ReceiverError('Bundled receiver verification failed: receiver ELF is missing or its size '
                                'differs from the manifest. Reinstall the complete desktop package; no payload was sent.')
        if hashlib.sha256(elf.read_bytes()).hexdigest() != manifest['elf_sha256']:
            raise ReceiverError('Bundled receiver verification failed: receiver ELF checksum differs from '
                                'the manifest. Reinstall the complete desktop package; no payload was sent.')
    except (OSError, ValueError) as exc:
        if isinstance(exc, ReceiverError):
            raise
        raise ReceiverError(f'Bundled receiver verification failed: invalid receiver ELF ({exc}). '
                            'Reinstall the complete desktop package; no payload was sent.') from exc
    try:
        prepare_elf(elf, b'\x80' + bytes(382) + b'\x01', PORT, CONTROL_PORT)
    except (OSError, ValueError) as exc:
        raise ReceiverError(f'Bundled receiver verification failed: invalid receiver ELF ({exc}). '
                            'Reinstall the complete desktop package; no payload was sent.') from exc


def export_receiver_elf(sdk: Path, elf: Path, destination: Path, token: CancellationToken) -> Path:
    """Export a verified receiver without a per-launch credential or overwrite."""
    ensure_receiver_elf(sdk, elf, token)
    token.check()
    # Check the public-key slot and configured ports before presenting an ELF
    # as deployable; prepare_elf does not change the source file.
    prepare_elf(elf, b'\x80' + bytes(382) + b'\x01', PORT, CONTROL_PORT)
    if destination.is_symlink():
        raise ReceiverError('Refusing to replace a linked receiver export path.')
    identity = None
    try:
        with elf.open('rb') as source, destination.open('xb') as output:
            info = os.fstat(output.fileno())
            identity = info.st_dev, info.st_ino
            while block := source.read(65536):
                token.check()
                output.write(block)
    except BaseException:
        # Only remove an output created by this call; never touch an existing file.
        if identity is not None:
            try:
                current = destination.lstat()
                if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == identity:
                    destination.unlink()
            except FileNotFoundError:
                pass
        raise
    return destination


def _connect(host: str, port: int, token: CancellationToken, *, timeout: float = 2) -> socket.socket:
    token.check()
    if not 1 <= port <= 65535:
        raise ValueError('Choose a TCP port between 1 and 65535.')
    return socket.create_connection((lan_address(host), port), timeout=timeout)


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
             cancellation: CancellationToken | None = None, *, connect_timeout: float = 2) -> ReceiverInfo:
    """Public discovery is a hint only; it never authorizes Send or Stop."""
    token = cancellation or CancellationToken()
    with _connect(host, control_port, token, timeout=connect_timeout) as stream:
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
    deadline = time.monotonic() + STOP_CONFIRM_TIMEOUT
    while time.monotonic() < deadline:
        token.check()
        try:
            identify(paired.host, paired.info.control_port, token,
                     connect_timeout=RECEIVER_PREFLIGHT_TIMEOUT)
        except ConnectionRefusedError:
            try:
                with _connect(paired.host, info.port, token,
                              timeout=RECEIVER_PREFLIGHT_TIMEOUT):
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
    raise ReceiverError(f'Stop was acknowledged, but listener closure on TCP {paired.info.control_port} and '
                        f'{info.port} was not confirmed within {STOP_CONFIRM_TIMEOUT:g} seconds. '
                        'Receiver status is Unknown; no force-stop was attempted.')


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
    """Own the local loader stream independently of the remote receiver lifecycle."""
    def __init__(self) -> None:
        self.attempted = False
        self.process: subprocess.Popen[bytes] | None = None
        self.stream: socket.socket | None = None
        self.temporary: tempfile.TemporaryDirectory[str] | None = None
        self.reader: threading.Thread | None = None

    def start(self, host: str, sdk: Path, elf: Path, loader_port: int = LOADER_PORT,
              port: int = PORT, control_port: int = CONTROL_PORT,
              cancellation: CancellationToken | None = None, *, existing_key: bytes | None = None,
              progress: ProgressCallback | None = None) -> PairedReceiver:
        token = cancellation or CancellationToken()
        host = lan_address(host)
        token.check()
        def report(stage: str, message: str) -> None:
            if progress:
                progress(Progress(stage, message))
        report('receiver-check', f'Checking receiver ports TCP {control_port} and {port} before deployment…')
        if not 1 <= loader_port <= 65535 or loader_port in (port, control_port):
            raise ValueError('The ELF loader needs a distinct valid TCP port (normally 9021).')
        if not all(1 <= value <= 65535 for value in (port, control_port)) or port == control_port:
            raise ValueError('Choose distinct valid receiver data and control ports.')
        # Close the probe before management authentication: the receiver handles
        # control clients serially. Failure to authenticate never permits deploy.
        for target in (control_port, port):
            try:
                with _connect(host, target, token, timeout=RECEIVER_PREFLIGHT_TIMEOUT):
                    pass
            except ConnectionRefusedError:
                continue
            except TimeoutError as exc:
                raise ReceiverError(f'Receiver preflight at {host}:{target} timed out after '
                                    f'{RECEIVER_PREFLIGHT_TIMEOUT:g} seconds ({exc}). Cannot establish whether a '
                                    'receiver is running; deployment was not attempted and no payload was sent.') from exc
            except OSError as exc:
                raise ReceiverError(f'Receiver preflight at {host}:{target} failed: {exc}. Cannot establish whether a '
                                    'receiver is running; deployment was not attempted and no payload was sent.') from exc
            if target == control_port and existing_key is not None:
                report('receiver-authenticate', 'Authenticating the existing receiver…')
                try:
                    info = identify(host, control_port, token)
                    paired = PairedReceiver(host, info, existing_key)
                    info = receiver_status(paired, token)
                except UnsupportedReceiver as exc:
                    raise ReceiverError('The listening receiver is incompatible with automatic startup. Stop it using its own controls before starting the matching receiver ELF. No service was replaced.') from exc
                except OSError as exc:
                    raise ReceiverError('The existing receiver could not be authenticated. Use --pair for an externally launched receiver or its authenticated GUI owner to stop it. No service was replaced.') from exc
                if info.busy:
                    raise ReceiverError('The authenticated receiver is busy; finish its active transfer before retrying.')
                report('receiver-ready', 'Existing receiver authenticated; no ELF deployed.')
                return PairedReceiver(host, info, existing_key)
            raise ReceiverError(f'A service is listening at {host}:{target}. Use Pair for an externally launched '
                                'receiver, or authenticated Stop Receiver before retrying. No service was replaced.')
        ensure_receiver_elf(sdk, elf, token, progress)
        direct = (elf.parent / 'receiver-manifest.json').is_file()
        deployer = sdk / ('win/prospero-deploy.cmd' if os.name == 'nt' else 'bin/prospero-deploy')
        if not direct:
            if not deployer.is_file():
                raise ReceiverError('PS5 Payload SDK deployer was not found. Select the SDK root containing bin/prospero-deploy (or win/prospero-deploy.cmd).')
            if os.name != 'nt' and not os.access(deployer, os.X_OK):
                raise ReceiverError('The SDK deployer is not executable. Restore its executable permission, or start the receiver externally and Pair.')
            utility = 'plink.exe' if os.name == 'nt' else 'socat'
            if shutil.which(utility) is None:
                raise ReceiverError(f'The SDK deployer requires {utility} on PATH. Install it, or start the ELF externally and pair with its six-digit code.')
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
        try:
            self.temporary = tempfile.TemporaryDirectory(prefix='playden-receiver-', ignore_cleanup_errors=True)
            path = Path(self.temporary.name) / 'receiver.elf'
            path.write_bytes(payload)  # Public key only; no session credential is stored.
        except OSError as exc:
            self.close()
            raise ReceiverError(f'Preparing the receiver payload in the temporary folder failed: {exc}. '
                                'No payload was sent.') from exc
        if not direct and os.name == 'nt' and (re.search(r'[\s%&|<>^!()"]', str(path)) or re.search(r'[\s%&|<>^!()"]', str(deployer))):
            self.close()
            raise ReceiverError('The SDK Windows batch deployer does not safely support these SDK/temp paths. Use paths without spaces or shell characters, or start externally and pair.')
        ready: queue.Queue[bytes] = queue.Queue(maxsize=1)
        try:
            report('receiver-connect', f'Connecting to the PS5 ELF loader at {host}:{loader_port}…')
            if direct:
                # The SDK deployer sends raw ELF bytes to the loader socket and
                # forwards its output. Reproduce that transport without bundling
                # socat/plink or copying SDK scripts into the application.
                try:
                    self.stream = socket.create_connection((host, loader_port), timeout=2)
                except OSError as exc:
                    raise ReceiverError(f'Connecting to the PS5 ELF loader at {host}:{loader_port} failed: {exc}. '
                                        'No payload was uploaded.') from exc
                self.attempted = True
                self.stream.settimeout(2)
            else:
                try:
                    self.process = subprocess.Popen([str(deployer), '-h', host, '-p', str(loader_port), str(path)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        start_new_session=os.name != 'nt', env=desktop_environment())
                except OSError as exc:
                    raise ReceiverError(f'Launching the SDK deployer for {host}:{loader_port} failed: {exc}. '
                                        'No payload was uploaded.') from exc
                self.attempted = True
            process, stream = self.process, self.stream
            reader_error: OSError | None = None
            def read() -> None:
                if stream is None:
                    assert process is not None and process.stdout is not None
                    stdout = process.stdout
                    lines = iter(lambda: stdout.readline(4097), b'')
                else:
                    def socket_lines():
                        nonlocal reader_error
                        pending = bytearray()
                        overflow = False
                        while True:
                            try:
                                block = stream.recv(4096)
                            except socket.timeout:
                                continue
                            except OSError as exc:
                                reader_error = exc
                                return
                            if not block:
                                return
                            for char in block:
                                if char == 10:
                                    if not overflow:
                                        yield bytes(pending) + b'\n'
                                    pending.clear()
                                    overflow = False
                                elif len(pending) < 4096 and not overflow:
                                    pending.append(char)
                                else:
                                    overflow = True
                    lines = socket_lines()
                for line in lines:
                    # Never retain or display untrusted loader output, including legacy tokens.
                    if re.fullmatch(rb'PDLAN2_BOOTSTRAP [0-9a-f]{768}\r?\n', line):
                        try:
                            ready.put_nowait(bytes.fromhex(line.split()[1].decode('ascii')))
                        except queue.Full:
                            pass
            self.reader = threading.Thread(target=read, daemon=True)
            self.reader.start()
            report('receiver-upload', f'Uploading receiver payload to {host}:{loader_port}…')
            if stream is not None:
                try:
                    with path.open('rb') as source:
                        while block := source.read(65536):
                            token.check()
                            stream.sendall(block)
                    stream.shutdown(socket.SHUT_WR)
                except OSError as exc:
                    raise ReceiverError(f'Uploading receiver payload to {host}:{loader_port} failed: {exc}. '
                                        'Remote startup is unconfirmed; check receiver status before retrying.') from exc
            report('receiver-startup', f'Waiting for receiver startup and encrypted credentials on {host}:{loader_port}…')
            deadline = time.monotonic() + STARTUP_TIMEOUT
            while time.monotonic() < deadline:
                token.check()
                try:
                    encrypted = ready.get(timeout=.05)
                except queue.Empty:
                    if (process is not None and process.poll() is not None) or (stream is not None and self.reader is not None and not self.reader.is_alive()):
                        if reader_error is not None:
                            raise ReceiverError(f'Waiting for receiver startup output from {host}:{loader_port} '
                                                f'failed: {reader_error}. The ELF may be running; Pair if its '
                                                'notification appeared.') from reader_error
                        exit_status = (f' SDK deployer exited with status {process.returncode}.'
                                       if process is not None else '')
                        raise ReceiverError(f'ELF loader stream at {host}:{loader_port} ended without encrypted '
                                            f'receiver credentials.{exit_status} Some loaders do not forward '
                                            'payload output; Pair if the receiver notification appeared.')
                    continue
                paired = decode_bootstrap(private, encrypted, host, port, control_port)
                report('receiver-authenticate', 'Encrypted startup credentials received; authenticating the receiver…')
                try:
                    info = receiver_status(paired, token)
                except OSError as exc:
                    raise ReceiverError(f'Authenticating the receiver at {host}:{control_port} failed: {exc}. '
                                        'The ELF may be running; inspect its notification before retrying.') from exc
                report('receiver-ready', 'Receiver authenticated and ready for transfer.')
                return PairedReceiver(host, info, paired.key, paired.pairing_code)
            raise ReceiverError(f'Receiver startup was not confirmed from {host}:{loader_port} within '
                                '20 seconds. Check the PS5 notification; Pair with its six-digit code if '
                                'the loader does not forward payload output. The ELF may still be running.')
        except BaseException:
            self.close()  # This closes only the local SDK stream, never claims remote exit.
            raise

    def close(self) -> None:
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            stream.close()
        process, self.process = self.process, None
        if process is not None:
            _stop_local_process(process)
        if self.reader is not None:
            self.reader.join(timeout=.5)
            if not self.reader.is_alive() and process is not None and process.stdout is not None:
                process.stdout.close()
            self.reader = None
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None
