"""Opt-in, bounded numeric transfer diagnostics. Never record secrets or paths."""
import time
from collections.abc import Callable

MetricsCallback = Callable[[dict[str, int | float]], None]


class NetworkMetrics:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.values: dict[str, int | float] = {}

    def tick(self) -> float:
        return time.perf_counter() if self.enabled else 0.

    def add(self, name: str, value: int | float = 1) -> None:
        if self.enabled:
            self.values[name] = self.values.get(name, 0) + value

    def elapsed(self, name: str, start: float) -> None:
        if self.enabled:
            self.add(name, time.perf_counter() - start)


# Fixed numeric schema; no receiver-supplied field names, strings or paths.
RECEIVER_COUNTERS = ('published', 'bytes', 'data_frames', 'files', 'fsyncs', 'send_calls', 'recv_calls')
RECEIVER_TIMINGS = ('wall_s', 'socket_read_s', 'socket_write_s', 'frame_auth_s', 'file_write_s',
                    'file_hash_s', 'fsync_s', 'verify_s', 'verify_read_s', 'verify_hash_s',
                    'permissions_s', 'commit_s')


def decode_receiver_metrics(payload: bytes, expected_bytes: int, expected_files: int) -> dict[str, int | float]:
    import struct
    layout = struct.Struct('!B19Q')
    if len(payload) != layout.size:
        raise ValueError('Invalid receiver metrics length')
    version, *values = layout.unpack(payload)
    if version != 1:
        raise ValueError('Unsupported receiver metrics schema')
    result: dict[str, int | float] = dict(zip(RECEIVER_COUNTERS, values[:7]))
    result.update(zip(RECEIVER_TIMINGS, (value / 1000000 for value in values[7:])))
    if result['published'] != 1 or result['bytes'] != expected_bytes or result['files'] != expected_files:
        raise ValueError('Receiver metrics disagree with the confirmed transfer')
    return result
