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
/* One bounded record per connection; no names, paths, keys or payload bytes. */
static int diagnostics_enabled = RECEIVER_DIAGNOSTICS;
static int socket_buffer = RECEIVER_SOCKET_BUFFER;
static struct {
    double socket_read_s, socket_write_s, frame_auth_s, file_write_s, file_hash_s;
    double fsync_s, verify_s, verify_read_s, verify_hash_s, permissions_s, commit_s;
    uint64_t bytes, data_frames, files, fsyncs, send_calls, recv_calls;
    int published;
} metrics;
static double metric_clock(void) {
    if (!diagnostics_enabled)
        return 0;
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
