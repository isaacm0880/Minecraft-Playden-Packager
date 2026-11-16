"""PDLAN/1 framing: mutually authenticated sessions and ordered HMAC frames.

No encryption: use a trusted private LAN. Pairing keys never travel on the wire.
See ps5/receiver/PROTOCOL.md for the shared Python/C contract.
"""
import hashlib
import hmac
import ipaddress
import secrets
import socket
import struct
import time

from .operations import CancellationToken

MAGIC = b'PDLAN1\r\n'
HEADER = struct.Struct('!BIQ')
BEGIN_INFO = struct.Struct('!BHIQ')
ENTRY_INFO = struct.Struct('!BHQ')
BEGIN, ENTRY, DATA, END_FILE, VERIFY, COMMIT = range(1, 7)
ACK, ERROR, VERIFY_PROGRESS = 128, 129, 130
MAX_FRAME = 65536
MAX_ENTRIES = 100000
MAX_FILE = 64 * 1024**3
MAX_TOTAL = 256 * 1024**3
MAX_PATH = 768
PORT = 9025
ROOTS = {'internal': '/data/playden-import', 'usb0': '/mnt/usb0/playden/import'}


class NetworkTransferError(OSError):
    """A transfer has no confirmed publication receipt."""


def lan_address(host: str) -> str:
    address = ipaddress.IPv4Address(host)
    networks = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8')
    if not any(address in ipaddress.IPv4Network(net) for net in networks):
        raise ValueError('Use a private LAN IPv4 address (or loopback for host tests).')
    return str(address)


def pairing_key(text: str) -> bytes:
    try:
        key = bytes.fromhex(text.strip())
    except ValueError:
        key = b''
    if len(key) != 32:
        raise ValueError('Enter the receiver’s 64 hexadecimal character pairing token.')
    return key


def safe_relative(name: str, *, package: bool = False) -> bytes:
    encoded = name.encode('utf-8')
    if not encoded or len(encoded) > (120 if package else MAX_PATH):
        raise ValueError('Package name or path exceeds the transfer limit.')
    parts = name.split('/')
    if len(parts) > 32:
        raise ValueError('Package paths may have at most 32 components.')
    if package and (len(parts) != 1 or name.startswith('.')):
        raise ValueError('Choose a plain package name, without a path or leading dot.')
    for part in parts:
        stem = part.split('.')[0].upper()
        if (part in ('', '.', '..') or len(part.encode('utf-8')) > 255 or part.endswith((' ', '.'))
                or any(c in '<>:"\\|?*' or ord(c) < 32 or ord(c) == 127 for c in part)
                or stem in {'CON', 'PRN', 'AUX', 'NUL'}
                or stem in {f'{prefix}{n}' for prefix in ('COM', 'LPT') for n in range(1, 10)}):
            raise ValueError('Unsafe or non-portable package path.')
    return encoded


class Session:
    def __init__(self, stream: socket.socket, token: CancellationToken, *, timeout: float = 15):
        self.stream = stream
        self.token = token
        self.timeout = timeout
        self.key = b''
        self.sent = self.received = 0
        stream.settimeout(.2)
        if stream.family in (socket.AF_INET, socket.AF_INET6):
            stream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def read(self, size: int) -> bytes:
        result = bytearray()
        deadline = time.monotonic() + self.timeout
        while len(result) < size:
            self.token.check()
            if time.monotonic() >= deadline:
                raise NetworkTransferError('PS5 receiver timed out. Check its payload and LAN connection.')
            try:
                block = self.stream.recv(size - len(result))
            except socket.timeout:
                continue
            except OSError as exc:
                raise NetworkTransferError('PS5 disconnected or rejected the transfer.') from exc
            if not block:
                raise NetworkTransferError('PS5 disconnected. No completion receipt; local files were retained.')
            result.extend(block)
        return bytes(result)

    def write(self, data: bytes) -> None:
        pending = memoryview(data)
        deadline = time.monotonic() + self.timeout
        while pending:
            self.token.check()
            if time.monotonic() >= deadline:
                raise NetworkTransferError('PS5 stopped receiving. Transfer timed out; local files were retained.')
            try:
                count = self.stream.send(pending)
            except socket.timeout:
                continue
            except OSError as exc:
                raise NetworkTransferError('PS5 connection failed while sending.') from exc
            if count <= 0:
                raise NetworkTransferError('PS5 connection closed during upload.')
            pending = pending[count:]

    def authenticate(self, key: bytes) -> None:
        greeting = self.read(len(MAGIC) + 32)
        if greeting[:len(MAGIC)] != MAGIC:
            raise NetworkTransferError('This is not a compatible PDLAN/1 receiver. Check the IP and port.')
        server = greeting[len(MAGIC):]
        client = secrets.token_bytes(32)
        def mac(label: bytes) -> bytes:
            return hmac.digest(key, label + server + client, 'sha256')
        self.write(client + mac(b'client'))
        if not hmac.compare_digest(self.read(32), mac(b'server')):
            raise NetworkTransferError('Receiver authentication failed. Check the pairing token.')
        self.key = mac(b'session')

    def send(self, kind: int, payload: bytes = b'') -> None:
        if len(payload) > MAX_FRAME:
            raise ValueError('Transfer frame too large.')
        header = HEADER.pack(kind, len(payload), self.sent)
        self.write(header + payload + hmac.digest(self.key, b'C' + header + payload, hashlib.sha256))
        self.sent += 1

    def receive(self) -> tuple[int, bytes]:
        header = self.read(HEADER.size)
        kind, size, sequence = HEADER.unpack(header)
        if size > MAX_FRAME or sequence != self.received:
            raise NetworkTransferError('Invalid receiver frame length or sequence.')
        payload, tag = self.read(size), self.read(32)
        if not hmac.compare_digest(tag, hmac.digest(self.key, b'S' + header + payload, 'sha256')):
            raise NetworkTransferError('Receiver message authentication failed; no completion confirmed.')
        self.received += 1
        if kind == ERROR:
            raise NetworkTransferError(payload.decode('utf-8', errors='replace'))
        return kind, payload

    def ack(self) -> bytes:
        kind, payload = self.receive()
        if kind != ACK:
            raise NetworkTransferError('Unexpected receiver response.')
        return payload
