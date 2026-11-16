#ifndef PDLAN_WIRE_H
#define PDLAN_WIRE_H
#include "diagnostics.h"
#include "vendor/sha-256.h"
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>

#define FRAME_MAX 65536
#define RX_CAPACITY (FRAME_MAX + 45)
#ifndef RECEIVER_BUFFERED_READS
#define RECEIVER_BUFFERED_READS 1
#endif
static int buffered_reads = RECEIVER_BUFFERED_READS;
#define PATH_MAX_WIRE 768
#define ENTRY_MAX 100000
#define FILE_MAX_BYTES (UINT64_C(64) * 1024 * 1024 * 1024)
#define TOTAL_MAX_BYTES (UINT64_C(256) * 1024 * 1024 * 1024)
#define MAGIC "PDLAN1\r\n"
enum {
    BEGIN = 1,
    ENTRY = 2,
    DATA = 3,
    END_FILE = 4,
    VERIFY = 5,
    COMMIT = 6,
    GET_METRICS = 7,
    GET_PROFILE = 8,
    ACK = 128,
    ERROR_REPLY = 129,
    VERIFY_PROGRESS = 130
};

static uint64_t getnum(const uint8_t *p, int n) {
    uint64_t v = 0;
    for (int i = 0; i < n; i++)
        v = (v << 8) | p[i];
    return v;
}
static void putnum(uint8_t *p, uint64_t v, int n) {
    for (int i = n - 1; i >= 0; i--) {
        p[i] = (uint8_t)v;
        v >>= 8;
    }
}
static int same(const uint8_t *a, const uint8_t *b, size_t n) {
    volatile uint8_t difference = 0;
    for (size_t i = 0; i < n; i++)
        difference |= a[i] ^ b[i];
    return difference == 0;
}
/* RFC 2104, fixed 32-byte keys; SHA-256 comes from the pinned 0BSD vendor. */
static void mac(const uint8_t key[32], const void *a, size_t an, const void *b, size_t bn,
                const void *c, size_t cn, uint8_t out[32]) {
    uint8_t pad[64], inner[32];
    struct Sha_256 hash;
    memset(pad, 0x36, sizeof(pad));
    for (int i = 0; i < 32; i++)
        pad[i] ^= key[i];
    sha_256_init(&hash, inner);
    sha_256_write(&hash, pad, 64);
    sha_256_write(&hash, a, an);
    sha_256_write(&hash, b, bn);
    sha_256_write(&hash, c, cn);
    sha_256_close(&hash);
    memset(pad, 0x5c, sizeof(pad));
    for (int i = 0; i < 32; i++)
        pad[i] ^= key[i];
    sha_256_init(&hash, out);
    sha_256_write(&hash, pad, 64);
    sha_256_write(&hash, inner, 32);
    sha_256_close(&hash);
    memset(pad, 0, sizeof(pad));
    memset(inner, 0, sizeof(inner));
}
static ssize_t socket_io(int fd, void *buffer, size_t n, int writing) {
    double started = metric_clock();
    int flags = 0;
#ifdef MSG_NOSIGNAL
    if (writing)
        flags = MSG_NOSIGNAL;
#endif
    ssize_t got = writing ? send(fd, buffer, n, flags) : recv(fd, buffer, n, 0);
    if (diagnostics_enabled) {
        if (writing)
            ++metrics.send_calls;
        else {
            ++metrics.recv_calls;
            if (got > 0 && (size_t)got <= n) {
                metrics.recv_wire_bytes += (uint64_t)got;
                if ((size_t)got < n)
                    ++metrics.recv_short_calls;
            }
        }
        metric_add(writing ? &metrics.socket_write_s : &metrics.socket_read_s, started);
    }
    return got;
}
static int io_all(int fd, void *buffer, size_t n, int writing) {
    uint8_t *p = buffer;
    while (n) {
        ssize_t got = socket_io(fd, p, n, writing);
        if (got < 0 && errno == EINTR)
            continue;
        if (got <= 0)
            return 0;
        if ((size_t)got > n) {
            errno = EIO;
            return 0;
        }
        p += got;
        n -= (size_t)got;
    }
    return 1;
}
typedef struct {
    int fd;
    uint8_t key[32];
    uint64_t sent, received;
    uint8_t *rx;
    size_t rx_position, rx_size;
} Wire;
/* Read ahead only into bounded private storage. Each frame is still completely
   authenticated before any command is interpreted or any file byte is written. */
static int wire_read(Wire *w, void *buffer, size_t n) {
    if (!buffered_reads)
        return io_all(w->fd, buffer, n, 0);
    uint8_t *out = buffer;
    if (diagnostics_enabled && n && w->rx_position < w->rx_size)
        ++metrics.rx_cache_hits;
    while (n) {
        if (w->rx_position < w->rx_size) {
            size_t count = w->rx_size - w->rx_position;
            if (count > n)
                count = n;
            memcpy(out, w->rx + w->rx_position, count);
            out += count;
            n -= count;
            w->rx_position += count;
            continue;
        }
        if (!w->rx && !(w->rx = malloc(RX_CAPACITY)))
            return 0;
        ssize_t got = socket_io(w->fd, w->rx, RX_CAPACITY, 0);
        if (got < 0 && errno == EINTR)
            continue;
        if (got <= 0)
            return 0;
        if ((size_t)got > RX_CAPACITY) {
            errno = EIO;
            return 0;
        }
        w->rx_position = 0;
        w->rx_size = (size_t)got;
    }
    return 1;
}
static int frame_send_packet(Wire *w, uint8_t type, const void *body, size_t size) {
    uint8_t header[13], tag[32];
    header[0] = type;
    putnum(header + 1, size, 4);
    putnum(header + 5, w->sent++, 8);
    /* Prefix direction to the header so only three HMAC segments are needed. */
    uint8_t prefix[14];
    prefix[0] = 'S';
    memcpy(prefix + 1, header, 13);
    double started = metric_clock();
    mac(w->key, prefix, 14, body, size, "", 0, tag);
    metric_add(&metrics.frame_auth_s, started);
    /* All receiver replies are bounded metadata. Coalesce header/body/tag to
       avoid three tiny TCP writes with NODELAY, especially over Wi-Fi. */
    uint8_t packet[13 + 1024 + 32];
    if (size > 1024) {
        errno = EMSGSIZE;
        return 0;
    }
    memcpy(packet, header, 13);
    if (size)
        memcpy(packet + 13, body, size);
    memcpy(packet + 13 + size, tag, 32);
    return io_all(w->fd, packet, 13 + size + 32, 1);
}
static int frame_send(Wire *w, uint8_t type, const void *body, size_t size) {
    double started = metric_clock();
    int ok = frame_send_packet(w, type, body, size);
    metric_add(&metrics.reply_s, started);
    if (diagnostics_enabled && ok)
        ++metrics.reply_frames;
    return ok;
}
static int frame_read_packet(Wire *w, uint8_t *type, uint8_t *body, size_t *size) {
    uint8_t header[13], prefix[14], tag[32], expected[32];
    if (!wire_read(w, header, 13))
        return 0;
    double parsing_started = metric_clock();
    *size = (size_t)getnum(header + 1, 4);
    *type = header[0];
    if (*size > FRAME_MAX || getnum(header + 5, 8) != w->received)
        return 0;
    metric_add(&metrics.frame_parse_s, parsing_started);
    if (!wire_read(w, body, *size) || !wire_read(w, tag, 32))
        return 0;
    prefix[0] = 'C';
    memcpy(prefix + 1, header, 13);
    double started = metric_clock();
    mac(w->key, prefix, 14, body, *size, "", 0, expected);
    if (diagnostics_enabled) {
        double elapsed = metric_clock() - started;
        metrics.frame_auth_s += elapsed;
        metrics.frame_receive_auth_s += elapsed;
    }
    if (!same(tag, expected, 32))
        return 0;
    ++w->received;
    return 1;
}
static int frame_read(Wire *w, uint8_t *type, uint8_t *body, size_t *size) {
    double started = metric_clock();
    uint64_t calls = metrics.recv_calls;
    double socket_before = metrics.socket_read_s;
    int ok = frame_read_packet(w, type, body, size);
    metric_add(&metrics.frame_read_s, started);
    if (diagnostics_enabled) {
        metrics.frame_socket_read_s += metrics.socket_read_s - socket_before;
        metrics.frame_recv_calls += metrics.recv_calls - calls;
        if (ok) {
            ++metrics.frames;
            if (*type == ENTRY)
                ++metrics.entry_frames;
            if (*type == END_FILE)
                ++metrics.end_frames;
        }
    }
    return ok;
}
#endif
