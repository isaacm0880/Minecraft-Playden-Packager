#ifndef PLAYDEN_MANAGEMENT_H
#define PLAYDEN_MANAGEMENT_H
#include <pthread.h>
#include "pairing_crypto.h"
#define MANAGER_MAGIC "PDMGR2\r\n"
#define MANAGER_VERSION "v0.4-manager-2"
#define CONTROL_STATUS 9
#define CONTROL_STOP 10
static struct {
    pthread_mutex_t mutex;
    uint8_t master[32], paired[32], id[32];
    char code[7];
    int has_pair, pair_used, busy, listener;
    unsigned attempts, data_port, control_port;
    double pair_deadline;
} manager = {.mutex = PTHREAD_MUTEX_INITIALIZER, .listener = -1};
static double manager_now(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t) < 0) return -1;
    return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}
static int control_io(int fd, void *data, size_t length, int writing) {
    uint8_t *p = data;
    double now = manager_now();
    if (now < 0) return 0;
    double deadline = now + 3;
    while (length) {
        now = manager_now();
        if (now < 0 || now >= deadline) return 0;
        ssize_t n = writing ? send(fd, p, length, 0) : recv(fd, p, length, 0);
        if (n < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (n <= 0 || (size_t)n > length) return 0;
        p += n; length -= (size_t)n;
    }
    return !length;
}
/* Copy keys under the lifecycle lock, then authenticate without holding it. */
static int receiver_auth(int fd, Wire *wire, int control) {
    uint8_t keys[2][32], greeting[40], reply[64], expected[32], proof[32], chosen[32];
    int has_pair;
    pthread_mutex_lock(&manager.mutex);
    memcpy(keys[0], manager.master, 32); memcpy(keys[1], manager.paired, 32);
    has_pair = manager.has_pair;
    pthread_mutex_unlock(&manager.mutex);
    if (control) {
        uint8_t derived[32];
        for (int i = 0; i < 2; ++i) {
            mac(keys[i], "control-v2", 10, manager.id, 32, "", 0, derived);
            memcpy(keys[i], derived, 32);
        }
        wipe(derived, sizeof(derived));
    }
    memcpy(greeting, MAGIC, 8); arc4random_buf(greeting + 8, 32);
    int ok = control ? control_io(fd, greeting, 40, 1) && control_io(fd, reply, 64, 0)
                     : io_all(fd, greeting, 40, 1) && io_all(fd, reply, 64, 0);
    int matched = 0;
    memset(chosen, 0, sizeof(chosen));
    if (ok) {
        for (int i = 0; i < 2; ++i) {
            mac(keys[i], "client", 6, greeting + 8, 32, reply, 32, expected);
            int valid = same(reply + 32, expected, 32) && (!i || has_pair);
            /* Constant-time selection; neither candidate key is sent. */
            uint8_t mask = (uint8_t)-(int)valid;
            for (int j = 0; j < 32; ++j) chosen[j] = (chosen[j] & ~mask) | (keys[i][j] & mask);
            matched |= valid;
        }
        ok = matched;
    }
    if (ok) {
        mac(chosen, "server", 6, greeting + 8, 32, reply, 32, proof);
        ok = control ? control_io(fd, proof, 32, 1) : io_all(fd, proof, 32, 1);
        if (ok) mac(chosen, "session", 7, greeting + 8, 32, reply, 32, wire->key);
    }
    wipe(keys, sizeof(keys)); wipe(chosen, sizeof(chosen)); wipe(expected, sizeof(expected));
    wipe(reply, sizeof(reply)); wipe(proof, sizeof(proof));
    return ok;
}
static int control_send(Wire *w, uint8_t type, const void *body, size_t length) {
    if (length > 768) return 0;
    uint8_t prefix[14], packet[13 + 768 + 32];
    packet[0] = type; putnum(packet + 1, length, 4); putnum(packet + 5, w->sent++, 8);
    prefix[0] = 'S'; memcpy(prefix + 1, packet, 13);
    if (length) memcpy(packet + 13, body, length);
    mac(w->key, prefix, 14, body, length, "", 0, packet + 13 + length);
    return control_io(w->fd, packet, 13 + length + 32, 1);
}
static int control_read(Wire *w, uint8_t *type, uint8_t body[512], size_t *length) {
    uint8_t header[13], tag[32], expected[32], prefix[14];
    if (!control_io(w->fd, header, 13, 0)) return 0;
    *length = (size_t)getnum(header + 1, 4); *type = header[0];
    if (*length > 512 || getnum(header + 5, 8) != w->received) return 0;
    if (!control_io(w->fd, body, *length, 0) || !control_io(w->fd, tag, 32, 0)) return 0;
    prefix[0] = 'C'; memcpy(prefix + 1, header, 13);
    mac(w->key, prefix, 14, body, *length, "", 0, expected);
    if (!same(tag, expected, 32)) return 0;
    ++w->received;
    return 1;
}
static size_t manager_info(char out[768], int busy, const uint8_t *lease) {
    char identity[65], lease_hex[33] = {0};
    hex(manager.id, 32, identity);
    if (lease) hex(lease, 16, lease_hex);
    int n = snprintf(out, 768,
        "{\"protocol\":2,\"version\":\"%s\",\"session_id\":\"%s\",\"port\":%u,"
        "\"control_port\":%u,\"busy\":%s,\"stop_lease\":\"%s\"}",
        MANAGER_VERSION, identity, manager.data_port, manager.control_port,
        busy ? "true" : "false", lease_hex);
    return n > 0 && n < 768 ? (size_t)n : 0;
}
static void control_pair(int fd) {
    uint8_t A[256], B[256], salt[16], secret[32], verifier[256], key[32], proof[32], expected[32];
    uint8_t transcript[32], credential[32], allowed = 0;
    char code[7] = {0};
    pthread_mutex_lock(&manager.mutex);
    double now = manager_now();
    if (!manager.pair_used && manager.attempts < 5 && now >= 0 && now < manager.pair_deadline) {
        ++manager.attempts; allowed = 1; memcpy(code, manager.code, 7);
    }
    pthread_mutex_unlock(&manager.mutex);
    if (!control_io(fd, &allowed, 1, 1) || !allowed) goto done;
    /* Count an attempt before reading any attacker-controlled public value. */
    if (!control_io(fd, A, 256, 0)) goto done;
    arc4random_buf(salt, 16); arc4random_buf(secret, 32); secret[0] |= 0x80;
    if (!srp_public(code, salt, secret, B, verifier) || !srp_shared(A, B, verifier, secret, key)) goto done;
    if (!control_io(fd, salt, 16, 1) || !control_io(fd, B, 256, 1)) goto done;
    struct Sha_256 hash;
    sha_256_init(&hash, transcript);
    sha_256_write(&hash, manager.id, 32); sha_256_write(&hash, salt, 16);
    sha_256_write(&hash, A, 256); sha_256_write(&hash, B, 256); sha_256_close(&hash);
    mac(key, "client-pair-v2", 14, transcript, 32, "", 0, expected);
    if (!control_io(fd, proof, 32, 0) || !same(proof, expected, 32)) goto done;
    mac(key, "credential-v2", 13, transcript, 32, "", 0, credential);
    pthread_mutex_lock(&manager.mutex);
    now = manager_now();
    int valid = !manager.pair_used && now >= 0 && now < manager.pair_deadline;
    if (valid) {
        memcpy(manager.paired, credential, 32); manager.has_pair = 1; manager.pair_used = 1;
        wipe(manager.code, sizeof(manager.code));
    }
    pthread_mutex_unlock(&manager.mutex);
    if (!valid) goto done;
    mac(key, "server-pair-v2", 14, transcript, 32, "", 0, proof);
    (void)control_io(fd, proof, 32, 1);
done:
    wipe(code, sizeof(code)); wipe(secret, sizeof(secret)); wipe(verifier, sizeof(verifier));
    wipe(key, sizeof(key)); wipe(proof, sizeof(proof)); wipe(expected, sizeof(expected));
    wipe(credential, sizeof(credential));
}
static void control_connection(int fd, int busy_at_accept) {
    uint8_t hello[40], operation;
    memcpy(hello, MANAGER_MAGIC, 8); memcpy(hello + 8, manager.id, 32);
    if (!control_io(fd, hello, 40, 1) || !control_io(fd, &operation, 1, 0)) return;
    if (operation == 'P') { control_pair(fd); return; }
    if (operation == 'I') {
        char info[768]; uint8_t length[2];
        size_t n = manager_info(info, busy_at_accept, NULL);
        putnum(length, n, 2);
        (void)(control_io(fd, length, 2, 1) && control_io(fd, info, n, 1));
        return;
    }
    if (operation != 'A') return;
    Wire wire = {.fd = fd};
    if (!receiver_auth(fd, &wire, 1)) { wipe(wire.key, sizeof(wire.key)); return; }
    uint8_t body[512], type, lease[16]; size_t length;
    if (!control_read(&wire, &type, body, &length) || type != CONTROL_STATUS || length) goto done;
    pthread_mutex_lock(&manager.mutex);
    int busy = busy_at_accept || manager.busy;
    pthread_mutex_unlock(&manager.mutex);
    char info[768]; arc4random_buf(lease, sizeof(lease));
    size_t n = manager_info(info, busy, busy ? NULL : lease);
    if (!control_send(&wire, ACK, info, n)) goto done;
    double issued = manager_now();
    if (!control_read(&wire, &type, body, &length)) goto done;
    double now = manager_now();
    if (now < 0 || issued < 0 || type != CONTROL_STOP || length != 48 || !same(body, manager.id, 32) ||
        !same(body + 32, lease, 16) || now - issued > 2) goto done;
    pthread_mutex_lock(&manager.mutex);
    int stop_ok = !busy && !manager.busy && !stopping;
    if (stop_ok) stopping = 1;
    pthread_mutex_unlock(&manager.mutex);
    if (stop_ok) control_send(&wire, ACK, "stopping", 8);
    else { const char *error = "Receiver busy; finish or cancel the transfer before stopping.";
        control_send(&wire, ERROR_REPLY, error, strlen(error)); }
done:
    wipe(wire.key, sizeof(wire.key)); wipe(lease, sizeof(lease));
}
static void *control_loop(void *unused) {
    (void)unused;
    while (!stopping) {
        pthread_mutex_lock(&manager.mutex);
        double now = manager_now();
        if (now < 0 || now >= manager.pair_deadline || manager.attempts >= 5)
            wipe(manager.code, sizeof(manager.code));
        pthread_mutex_unlock(&manager.mutex);
        fd_set fds; FD_ZERO(&fds); FD_SET(manager.listener, &fds);
        struct timeval wait = {0, 200000};
        int ready = select(manager.listener + 1, &fds, NULL, NULL, &wait);
        if (ready <= 0) continue;
        struct sockaddr_in peer; socklen_t size = sizeof(peer);
        int fd = accept(manager.listener, (struct sockaddr *)&peer, &size);
        if (fd < 0) continue;
        struct timeval timeout = {0, 200000};
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
        int yes = 1; setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &yes, sizeof(yes));
        pthread_mutex_lock(&manager.mutex); int busy = manager.busy; pthread_mutex_unlock(&manager.mutex);
        if (private_ip(peer.sin_addr.s_addr) && !stopping) control_connection(fd, busy);
        close(fd);
    }
    close(manager.listener);
    return NULL;
}
#endif
