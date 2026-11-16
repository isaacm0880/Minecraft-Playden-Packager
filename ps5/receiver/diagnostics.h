#ifndef PDLAN_DIAGNOSTICS_H
#define PDLAN_DIAGNOSTICS_H
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>
#ifndef RECEIVER_DIAGNOSTICS
#define RECEIVER_DIAGNOSTICS 0
#endif
#ifndef RECEIVER_SOCKET_BUFFER
#define RECEIVER_SOCKET_BUFFER 0
#endif
enum {
    PH_PAIR,
    PH_SETUP,
    PH_FRAME,
    PH_ENTRY,
    PH_DATA,
    PH_END,
    PH_VERIFY,
    PH_COMMIT,
    PH_METRICS,
    PH_COUNT
};
static int phase_current = -1;
static double phase_started;
static uint64_t clock_probe_ns, clock_probe_valid;
/* One bounded record per connection; no names, paths, keys or payload bytes. */
static int diagnostics_enabled = RECEIVER_DIAGNOSTICS;
static int socket_buffer = RECEIVER_SOCKET_BUFFER;
static struct {
    double socket_read_s, socket_write_s, frame_auth_s, file_write_s, file_hash_s;
    double fsync_s, verify_s, verify_read_s, verify_hash_s, permissions_s, commit_s;
    uint64_t bytes, data_frames, files, fsyncs, send_calls, recv_calls;
    double frame_read_s, frame_parse_s, frame_socket_read_s, frame_receive_auth_s, reply_s,
        entry_parse_s, entry_create_s, parent_walk_s;
    double file_close_s, hash_finalize_s, hash_walk_s, namespace_s, recipe_s, inventory_s;
    double phase_s[PH_COUNT];
    uint64_t frames, entry_frames, end_frames, reply_frames, entry_acks, end_acks;
    uint64_t recv_wire_bytes, recv_short_calls, frame_recv_calls, rx_cache_hits;
    uint64_t parent_walk_calls, dir_open_calls, clock_calls, write_calls;
    int published;
} metrics;
static double metric_clock(void) {
    if (!diagnostics_enabled)
        return 0;
    ++metrics.clock_calls;
    int saved = errno;
    struct timespec now;
    int ok = clock_gettime(CLOCK_MONOTONIC, &now) == 0;
    errno = saved;
    return ok ? now.tv_sec + now.tv_nsec / 1e9 : 0;
}
static void metric_add(double *field, double start) {
    if (diagnostics_enabled)
        *field += metric_clock() - start;
}
/* One coarse phase owns every interval, including dispatch and instrumentation.
   Fine timers below overlap these phases and must never be summed with them. */
static void metric_phase(int next) {
    if (!diagnostics_enabled)
        return;
    double now = metric_clock();
    if (phase_current >= 0)
        metrics.phase_s[phase_current] += now - phase_started;
    phase_started = now;
    phase_current = next;
}
static void metric_probe_clock(void) {
    if (!diagnostics_enabled)
        return;
    /* Measure the complete timer path, including errno preservation and counter
       bookkeeping. This is a calibration estimate, not an additive work phase. */
    double first = metric_clock(), last = first;
    int ok = first > 0;
    for (int i = 0; i < 256; ++i) {
        double sample = metric_clock();
        if (sample < last)
            ok = 0;
        last = sample;
    }
    last = metric_clock();
    if (ok && last >= first) {
        clock_probe_ns = (uint64_t)((last - first) * 1e9 / 257);
        clock_probe_valid = 1;
    }
}
static void metric_report(double start) {
    if (diagnostics_enabled) {
        printf("receiver_metrics wall_s=%.6f published=%d bytes=%llu data_frames=%llu files=%llu "
               "fsyncs=%llu send_calls=%llu recv_calls=%llu socket_read_s=%.6f "
               "socket_write_s=%.6f frame_auth_s=%.6f file_write_s=%.6f file_hash_s=%.6f "
               "fsync_s=%.6f verify_s=%.6f verify_read_s=%.6f verify_hash_s=%.6f "
               "permissions_s=%.6f commit_s=%.6f\n",
               metric_clock() - start, metrics.published, (unsigned long long)metrics.bytes,
               (unsigned long long)metrics.data_frames, (unsigned long long)metrics.files,
               (unsigned long long)metrics.fsyncs, (unsigned long long)metrics.send_calls,
               (unsigned long long)metrics.recv_calls, metrics.socket_read_s,
               metrics.socket_write_s, metrics.frame_auth_s, metrics.file_write_s,
               metrics.file_hash_s, metrics.fsync_s, metrics.verify_s, metrics.verify_read_s,
               metrics.verify_hash_s, metrics.permissions_s, metrics.commit_s);
        fflush(stdout);
    }
}
#endif
