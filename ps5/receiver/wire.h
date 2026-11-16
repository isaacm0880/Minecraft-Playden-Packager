#ifndef PDLAN_WIRE_H
#define PDLAN_WIRE_H
#include "diagnostics.h"
#include "vendor/sha-256.h"
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>

#define FRAME_MAX 65536
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
static int io_all(int fd, void *buffer, size_t n, int writing) {
    uint8_t *p = buffer;
    while (n) {
        double started = metric_clock();
        int flags = 0;
#ifdef MSG_NOSIGNAL
        if (writing)
            flags = MSG_NOSIGNAL;
#endif
        ssize_t got = writing ? send(fd, p, n, flags) : recv(fd, p, n, 0);
        if (diagnostics_enabled) {
            if (writing)
                ++metrics.send_calls;
            else
                ++metrics.recv_calls;
            metric_add(writing ? &metrics.socket_write_s : &metrics.socket_read_s, started);
        }
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
} Wire;
static int frame_send(Wire *w, uint8_t type, const void *body, size_t size) {
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
static int frame_read(Wire *w, uint8_t *type, uint8_t *body, size_t *size) {
    uint8_t header[13], prefix[14], tag[32], expected[32];
    if (!io_all(w->fd, header, 13, 0))
        return 0;
    *size = (size_t)getnum(header + 1, 4);
    *type = header[0];
    if (*size > FRAME_MAX || getnum(header + 5, 8) != w->received)
        return 0;
    if (!io_all(w->fd, body, *size, 0) || !io_all(w->fd, tag, 32, 0))
        return 0;
    prefix[0] = 'C';
    memcpy(prefix + 1, header, 13);
    double started = metric_clock();
    mac(w->key, prefix, 14, body, *size, "", 0, expected);
    metric_add(&metrics.frame_auth_s, started);
    if (!same(tag, expected, 32))
        return 0;
    ++w->received;
    return 1;
}
#endif
