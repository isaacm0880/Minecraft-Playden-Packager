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


PROFILE_COUNTERS = ('frames', 'entry_frames', 'end_frames', 'reply_frames', 'entry_acks', 'end_acks',
                    'recv_wire_bytes', 'recv_short_calls', 'frame_recv_calls', 'rx_cache_hits',
                    'parent_walk_calls', 'dir_open_calls', 'clock_calls', 'write_calls',
                    'clock_probe_ns', 'clock_probe_valid', 'buffered_reads')
PROFILE_PHASES = ('phase_pairing_s', 'phase_setup_s', 'phase_frames_s', 'phase_entries_s',
                  'phase_data_s', 'phase_end_files_s', 'phase_verify_s', 'phase_commit_s', 'phase_metrics_s')
PROFILE_TIMINGS = ('frame_read_s', 'frame_parse_s', 'frame_socket_read_s', 'frame_receive_auth_s', 'reply_s', 'entry_parse_s', 'entry_create_s',
                   'parent_walk_s', 'file_close_s', 'hash_finalize_s', 'hash_walk_s', 'namespace_s',
                   'recipe_s', 'inventory_s', *PROFILE_PHASES, 'unattributed_s', 'clock_overhead_estimate_s')


def decode_receiver_profile(payload: bytes, expected_bytes: int, expected_files: int) -> dict[str, int | float]:
    import struct
    layout = struct.Struct('!B61Q')
    if len(payload) != layout.size or payload[0] != 2:
        raise ValueError('Invalid receiver profile schema or length')
    # Schema 1 is an unchanged prefix apart from the version byte.
    result = decode_receiver_metrics(bytes([1]) + payload[1:153], expected_bytes, expected_files)
    values = struct.unpack('!42Q', payload[153:])
    result.update(zip(PROFILE_COUNTERS, values[:17]))
    result.update(zip(PROFILE_TIMINGS, (value / 1000000 for value in values[17:])))
    if (result['frames'] < result['data_frames'] + result['entry_frames'] + result['end_frames']
            or result['entry_frames'] < expected_files or result['end_frames'] != expected_files
            or result['entry_acks'] != result['entry_frames'] or result['end_acks'] != expected_files
            or result['frame_recv_calls'] > result['recv_calls'] or result['recv_wire_bytes'] < expected_bytes
            or result['buffered_reads'] not in (0, 1) or result['clock_probe_valid'] not in (0, 1)):
        raise ValueError('Inconsistent receiver profile counters')
    accounted = sum(result[key] for key in PROFILE_PHASES) + result['unattributed_s']
    if abs(accounted - result['wall_s']) > .001:
        raise ValueError('Receiver phase ledger does not match wall time')
    result['profile_version'] = 2
    return result
