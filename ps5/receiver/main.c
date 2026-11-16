/* Authenticated import-only receiver. Never reads/writes Playden managed games. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <ifaddrs.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/statvfs.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>
#ifdef PS5_RECEIVER
#include <sys/mount.h>
#include <sys/param.h>
#endif
#include "package.h"
#include "paths.h"
#include "wire.h"

static volatile sig_atomic_t stopping;
static const char *roots[] = {"/data/playden-import", "/mnt/usb0/playden/import"};
static const char *temp_roots[] = {"/data/playden-packager-temp",
                                   "/mnt/usb0/playden-packager-temp"};
#define RECEIVER_BUILD "v0.4-import-access-1"
#ifdef HOST_TEST
static char test_root[1024];
static uint64_t test_free = UINT64_MAX;
static uint64_t test_write_limit = UINT64_MAX, test_written;
static int test_stage_device_mismatch, test_rename_errno, test_pause_cleanup, test_permission_errno;
#endif
static void stop_receiver(int sig) {
    (void)sig;
    stopping = 1;
}
static int private_ip(uint32_t ip) {
    ip = ntohl(ip);
    return (ip >> 24) == 10 || (ip >> 20) == 0xac1 || (ip >> 16) == 0xc0a8 || (ip >> 24) == 127;
}
static int choose_address(char out[INET_ADDRSTRLEN]) {
    struct ifaddrs *all = NULL;
    if (getifaddrs(&all) < 0)
        return 0;
    int ok = 0;
    for (struct ifaddrs *i = all; i; i = i->ifa_next) {
        if (!i->ifa_addr || i->ifa_addr->sa_family != AF_INET)
            continue;
        struct sockaddr_in *address = (struct sockaddr_in *)i->ifa_addr;
        if (private_ip(address->sin_addr.s_addr) &&
            (ntohl(address->sin_addr.s_addr) >> 24) != 127) {
            if (inet_ntop(AF_INET, &address->sin_addr, out, INET_ADDRSTRLEN)) {
                ok = 1;
                break;
            }
        }
    }
    freeifaddrs(all);
    return ok;
}
static void hex(const uint8_t *bytes, size_t n, char *out) {
    const char *digits = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) {
        out[i * 2] = digits[bytes[i] >> 4];
        out[i * 2 + 1] = digits[bytes[i] & 15];
    }
    out[n * 2] = 0;
}
static void show_pairing(const char *address, unsigned port, const char *token) {
    printf("PDLAN/1 address=%s port=%u token=%s\n", address, port, token);
    printf("receiver_build=%s stat_backend=%s diagnostics=%d stdout=unbuffered\n", RECEIVER_BUILD,
#ifdef PS5_RECEIVER
           "freebsd-carry",
#else
           "posix-host",
#endif
           diagnostics_enabled);
#ifdef PS5_RECEIVER
    /* Same ABI used by upstream ps5-payload-dev/elfldr's notification helper. */
    typedef struct {
        char reserved[45];
        char message[3075];
    } Notification;
    extern int sceKernelSendNotificationRequest(int, Notification *, size_t, int);
    Notification request;
    memset(&request, 0, sizeof(request));
    snprintf(request.message, sizeof(request.message),
             "Playden LAN receiver %s:%u\nPairing token:\n%s", address, port, token);
    sceKernelSendNotificationRequest(0, &request, sizeof(request), 0);
#endif
}
static int handshake(Wire *w, const uint8_t key[32]) {
    uint8_t greeting[40], reply[64], expected[32], proof[32];
    memcpy(greeting, MAGIC, 8);
    arc4random_buf(greeting + 8, 32);
    if (!io_all(w->fd, greeting, 40, 1) || !io_all(w->fd, reply, 64, 0))
        return 0;
    mac(key, "client", 6, greeting + 8, 32, reply, 32, expected);
    if (!same(reply + 32, expected, 32))
        return 0;
    mac(key, "server", 6, greeting + 8, 32, reply, 32, proof);
    if (!io_all(w->fd, proof, 32, 1))
        return 0;
    mac(key, "session", 7, greeting + 8, 32, reply, 32, w->key);
    return 1;
}
static int storage_root(int destination, int temporary, char path[2048]) {
#ifdef HOST_TEST
    if (test_root[0]) {
        const char *relative =
            destination ? (temporary ? "usb0/playden-packager-temp" : "usb0/playden/import")
                        : (temporary ? "playden-packager-temp" : "internal");
        snprintf(path, 2048, "%s/%s", test_root, relative);
        if (destination) {
            char usb[1200];
            snprintf(usb, sizeof(usb), "%s/usb0", test_root);
            int fd = open_absolute(usb, 0);
            if (fd < 0)
                return -1;
            close(fd);
        }
        return open_absolute(path, 1);
    }
#endif
    strcpy(path, temporary ? temp_roots[destination] : roots[destination]);
    /* Only create the leaf internal import root, never /data or a USB mount. */
    if (!destination) {
        int data = open_absolute("/data", 0);
        if (data < 0)
            return -1;
        int fd = open_dirs(data, temporary ? "playden-packager-temp" : "playden-import", 1);
        int saved_error = errno;
        close(data);
        if (fd < 0)
            errno = saved_error;
        return fd;
    }
    int usb = open_absolute("/mnt/usb0", 0);
    if (usb < 0)
        return -1;
#ifdef PS5_RECEIVER
    struct statfs fs;
    if (fstatfs(usb, &fs) < 0) {
        int saved_error = errno;
        close(usb);
        errno = saved_error;
        return -1;
    }
    if (strcmp(fs.f_mntonname, "/mnt/usb0")) {
        close(usb);
        errno = ENODEV;
        return -1;
    }
#endif
    int fd = open_dirs(usb, temporary ? "playden-packager-temp" : "playden/import", 1);
    int saved_error = errno;
    close(usb);
    if (fd < 0)
        errno = saved_error;
    return fd;
}
static int enough_space(int fd, uint64_t bytes, uint32_t entries) {
    struct statvfs fs;
    if (fstatvfs(fd, &fs) < 0)
        return 0;
    uint64_t unit = fs.f_frsize ? fs.f_frsize : fs.f_bsize;
    if (unit < 4096)
        unit = 4096;
    if (unit > (UINT64_MAX - bytes - 16 * 1024 * 1024) / ((uint64_t)entries + 8))
        return 0;
    uint64_t required = bytes + unit * ((uint64_t)entries + 8) + 16 * 1024 * 1024;
    uint64_t free_bytes = fs.f_bavail > UINT64_MAX / unit ? UINT64_MAX : fs.f_bavail * unit;
#ifdef HOST_TEST
    if (test_free != UINT64_MAX)
        free_bytes = test_free;
#endif
    return free_bytes >= required;
}
static int sync_dir(int fd) {
    double started = metric_clock();
    int result = fsync(fd);
    metric_add(&metrics.fsync_s, started);
    if (diagnostics_enabled)
        ++metrics.fsyncs;
    if (result == 0)
        return 1;
    /* Some USB filesystems cannot fsync directories. File flushes still must
       succeed; atomic rename controls visibility. Physical durability needs
       on-console verification for each firmware/filesystem combination. */
    return errno == EINVAL || errno == EOPNOTSUPP;
}
static int write_all(int fd, const uint8_t *data, size_t n) {
#ifdef HOST_TEST
    if (n > test_write_limit - test_written) {
        errno = ENOSPC;
        return 0;
    }
    test_written += n;
#endif
    while (n) {
        ssize_t got = write(fd, data, n);
        if (got < 0 && errno == EINTR)
            continue;
        if (got <= 0)
            return 0;
        data += got;
        n -= (size_t)got;
    }
    return 1;
}
static int root_identity(int fd, const char *absolute_root) {
    int fresh = open_absolute(absolute_root, 0);
    struct stat held, current;
    int same_root = fresh >= 0 && fstat(fd, &held) == 0 && fstat(fresh, &current) == 0 &&
                    held.st_dev == current.st_dev && held.st_ino == current.st_ino;
    if (fresh >= 0)
        close(fresh);
    if (!same_root)
        errno = ENODEV;
    return same_root;
}
static int storage_pair(int root, int temporary) {
    struct stat a, b;
    if (fstat(root, &a) < 0 || fstat(temporary, &b) < 0)
        return 0;
    if (!S_ISDIR(a.st_mode) || !S_ISDIR(b.st_mode) ||
        (a.st_dev == b.st_dev && a.st_ino == b.st_ino)) {
        errno = EINVAL;
        return 0;
    }
    if (a.st_dev != b.st_dev) {
        errno = EXDEV;
        return 0;
    }
#ifdef HOST_TEST
    if (test_stage_device_mismatch) {
        errno = EXDEV;
        return 0;
    }
#endif
    return 1;
}
/* Playden runs as another user and opens imported games in place. Match its
   import repair policy: writable directories/files, without executable data.
   The enclosing session stage remains 0700 until atomic publication. */
static int import_mode(int fd, mode_t mode) {
#ifdef HOST_TEST
    if (test_permission_errno) {
        errno = test_permission_errno;
        return 0;
    }
#endif
    struct stat info;
    if (fstat(fd, &info) < 0)
        return 0;
    if ((info.st_mode & 07777) == mode)
        return 1;
    if (fchmod(fd, mode) < 0)
        return 0;
    if (fstat(fd, &info) < 0)
        return 0;
    /* FAT-like mounts may expose extra x bits rather than per-file modes. */
    if ((info.st_mode & mode) != mode || (info.st_mode & 07000 & ~mode)) {
        errno = EACCES;
        return 0;
    }
    return 1;
}
static int package_permissions(int package, Inventory *inventory, Wire *wire, uint64_t total) {
    for (size_t i = 0; i < inventory->count; ++i) {
        Item *item = &inventory->items[i];
        char leaf[256];
        int parent = parent_fd(package, item->path, leaf);
        if (parent < 0)
            return 0;
        int fd = openat(parent, leaf,
                        O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC |
                            (item->kind ? O_RDWR : O_RDONLY | O_DIRECTORY));
        struct stat info, named;
        int ok = fd >= 0 && fstat(fd, &info) == 0 &&
                 (item->kind ? (S_ISREG(info.st_mode) && info.st_nlink == 1 &&
                                (uint64_t)info.st_size == item->size)
                             : S_ISDIR(info.st_mode)) &&
                 import_mode(fd, item->kind ? 0666 : 0777) &&
                 stat_nofollow_at(parent, leaf, &named, NULL) == 0 && named.st_dev == info.st_dev &&
                 named.st_ino == info.st_ino;
        if (ok) {
            if (item->kind) {
                double started = metric_clock();
                ok = fsync(fd) == 0;
                metric_add(&metrics.fsync_s, started);
                if (diagnostics_enabled)
                    ++metrics.fsyncs;
            } else
                ok = sync_dir(fd);
        }
        int saved = errno;
        if (fd >= 0)
            close(fd);
        close(parent);
        if (!ok) {
            errno = saved ? saved : EIO;
            return 0;
        }
        if (i % 128 == 0 && !verification_progress(wire, total, total))
            return 0;
    }
    return import_mode(package, 0777) && sync_dir(package);
}
static int import_root_permissions(int destination, int root, const char *path) {
    if (!root_identity(root, path))
        return 0;
    if (destination) {
        /* Only this known container, never /mnt/usb0 itself or managed games. */
        char parent_path[2048];
        snprintf(parent_path, sizeof(parent_path), "%s", path);
        char *slash = strrchr(parent_path, '/');
        if (!slash) {
            errno = EINVAL;
            return 0;
        }
        *slash = 0;
        int parent = open_absolute(parent_path, 0);
        struct stat info;
        int ok = parent >= 0 && fstat(parent, &info) == 0 &&
                 import_mode(parent, (info.st_mode & 07777) | 0555) && sync_dir(parent);
        int saved = errno;
        if (parent >= 0)
            close(parent);
        if (!ok) {
            errno = saved;
            return 0;
        }
    }
    return import_mode(root, 0777) && sync_dir(root);
}
static int publish(int root, int temporary, int stage, const char *stage_name, const char *name,
                   const char *absolute_root, const char *absolute_temporary) {
    if (!root_identity(root, absolute_root) || !root_identity(temporary, absolute_temporary) ||
        !storage_pair(root, temporary) || !same_inode(stage, temporary, stage_name))
        return 0;
    if (mkdirat(root, name, 0700) < 0)
        return 0;
    int reservation = openat(root, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (reservation < 0)
        return 0;
    int ok = sync_dir(stage) && same_inode(reservation, root, name);
    if (ok) {
#ifdef HOST_TEST
        if (test_rename_errno) {
            errno = test_rename_errno;
            ok = 0;
        } else
#endif
            ok = renameat(stage, "package", root, name) == 0;
    }
    int saved_error = errno;
    if (!ok && same_inode(reservation, root, name))
        unlinkat(root, name, AT_REMOVEDIR);
    close(reservation);
    if (!ok)
        errno = saved_error;
    return ok && sync_dir(stage) && sync_dir(root) && sync_dir(temporary);
}

static void log_stat(const char *name, const StatDiagnostic *diagnostic) {
    printf("receiver_stat operation=fstatat flags=AT_SYMLINK_NOFOLLOW(0x%x) name=%s "
           "raw_result=%d carry=%d raw_errno=%d errno=%d (%s)\n",
           AT_SYMLINK_NOFOLLOW, name, diagnostic->result, diagnostic->carry, diagnostic->raw_errno,
           diagnostic->error, strerror(diagnostic->error));
}

/* Read-only alternative-API probe. Its result is diagnostic only: never use an
   absolute-path lookup to override a failed descriptor-relative security check. */
static void log_lstat_comparison(const char *root, const char *name) {
    char path[2300];
    snprintf(path, sizeof(path), "%s/%s", root, name);
    struct stat info;
    errno = 0;
    int result = lstat(path, &info);
    int error = errno;
    printf("receiver_stat comparison=lstat name=%s raw_result=%d errno=%d (%s)\n", name, result,
           error, strerror(error));
}

/* Optional post-receipt extension, schema 1: 7 uint64 counters followed by
   12 uint64 microsecond durations. No printf, JSON, names or paths on this route. */
static int send_metrics(Wire *wire, double connection_started) {
    uint8_t payload[1 + 19 * 8];
    payload[0] = 1;
    uint64_t counters[] = {metrics.published, metrics.bytes,  metrics.data_frames,
                           metrics.files,     metrics.fsyncs, metrics.send_calls,
                           metrics.recv_calls};
    double timings[] = {metric_clock() - connection_started,
                        metrics.socket_read_s,
                        metrics.socket_write_s,
                        metrics.frame_auth_s,
                        metrics.file_write_s,
                        metrics.file_hash_s,
                        metrics.fsync_s,
                        metrics.verify_s,
                        metrics.verify_read_s,
                        metrics.verify_hash_s,
                        metrics.permissions_s,
                        metrics.commit_s};
    for (size_t i = 0; i < 7; ++i)
        putnum(payload + 1 + i * 8, counters[i], 8);
    for (size_t i = 0; i < 12; ++i) {
        if (!(timings[i] >= 0 && timings[i] < (double)UINT64_MAX / 1000000))
            return 0;
        putnum(payload + 1 + (7 + i) * 8, (uint64_t)(timings[i] * 1000000), 8);
    }
    return frame_send(wire, ACK, payload, sizeof(payload));
}

static void connection(int client, const uint8_t key[32]) {
    memset(&metrics, 0, sizeof(metrics));
    double connection_started = metric_clock();
    Wire wire = {.fd = client, .sent = 0, .received = 0};
    if (!handshake(&wire, key)) {
        sleep(1);
        return;
    }
    uint8_t *body = malloc(FRAME_MAX);
    int root = -1, temporary = -1, stage = -1, package = -1, file = -1;
    Inventory inventory = {0};
    char stage_name[64] = {0}, package_name[121] = {0}, root_path[2048] = {0};
    char temporary_path[2048] = {0};
    char package_path[2300];
    char diagnostic_error[512];
    const char *error = "Malformed or out-of-order transfer command.";
    uint64_t total = 0, planned = 0, completed = 0, remaining = 0;
    uint32_t expected_entries = 0;
    int destination = 0, verified = 0;
    struct Sha_256 file_hash;
    uint8_t actual_hash[32];
    Item *active = NULL;
    size_t length;
    uint8_t type;
    if (!body) {
        error = "Receiver out of memory.";
        goto fail;
    }
    if (!frame_read(&wire, &type, body, &length) || type != BEGIN || length < 15)
        goto fail;
    destination = body[0];
    size_t name_length = (size_t)getnum(body + 1, 2);
    expected_entries = (uint32_t)getnum(body + 3, 4);
    total = getnum(body + 7, 8);
    if (destination > 1 || length != 15 + name_length ||
        !safe_path((char *)body + 15, name_length, 1) || !expected_entries ||
        expected_entries > ENTRY_MAX || total > TOTAL_MAX_BYTES)
        goto fail;
    memcpy(package_name, body + 15, name_length);
    package_name[name_length] = 0;
    root = storage_root(destination, 0, root_path);
    if (root < 0) {
        int saved_error = errno;
        snprintf(diagnostic_error, sizeof(diagnostic_error), "%s errno=%d (%s).",
                 destination ? "USB0 is unavailable or unsafe; no internal fallback."
                             : "Internal import directory is unavailable or unsafe.",
                 saved_error, strerror(saved_error));
        error = diagnostic_error;
        goto fail;
    }
    struct stat held, probe;
    StatDiagnostic diagnostic;
    int probe_result = stat_nofollow_at(root, ".", &probe, &diagnostic);
    log_stat(".", &diagnostic);
    if (probe_result != 0) {
        snprintf(diagnostic_error, sizeof(diagnostic_error),
                 "Import directory fstatat self-check failed: errno=%d (%s), raw_result=%d, "
                 "raw_errno=%d. No destination assumed available.",
                 diagnostic.error, strerror(diagnostic.error), diagnostic.result,
                 diagnostic.raw_errno);
        error = diagnostic_error;
        goto fail;
    }
    errno = 0;
    int held_result = fstat(root, &held);
    int held_error = errno;
    if (held_result != 0 || !S_ISDIR(probe.st_mode) || !S_ISDIR(held.st_mode) ||
        probe.st_dev != held.st_dev || probe.st_ino != held.st_ino) {
        snprintf(diagnostic_error, sizeof(diagnostic_error),
                 "Import directory stat identity check failed: fstat result=%d errno=%d (%s). "
                 "No destination assumed available.",
                 held_result, held_error, strerror(held_error));
        error = diagnostic_error;
        goto fail;
    }
    struct stat existing;
    int exists_result = stat_nofollow_at(root, package_name, &existing, &diagnostic);
    log_stat(package_name, &diagnostic);
    log_lstat_comparison(root_path, package_name);
    if (exists_result == 0) {
        error = "Package name already exists. Choose a new name; overwrite is disabled.";
        goto fail;
    }
    if (diagnostic.error != ENOENT) {
        snprintf(diagnostic_error, sizeof(diagnostic_error),
                 "Cannot check destination: fstatat errno=%d (%s), raw_result=%d, raw_errno=%d. "
                 "No destination assumed available; transfer stopped.",
                 diagnostic.error, strerror(diagnostic.error), diagnostic.result,
                 diagnostic.raw_errno);
        error = diagnostic_error;
        goto fail;
    }
    temporary = storage_root(destination, 1, temporary_path);
    if (temporary < 0 || !storage_pair(root, temporary) || !root_identity(root, root_path) ||
        !root_identity(temporary, temporary_path)) {
        int saved_error = errno;
        snprintf(diagnostic_error, sizeof(diagnostic_error),
                 "Unsafe or unavailable staging/import roots: errno=%d (%s). Same filesystem "
                 "required; no copy fallback.",
                 saved_error, strerror(saved_error));
        error = diagnostic_error;
        goto fail;
    }
    if (!enough_space(temporary, total, expected_entries)) {
        error = "Insufficient storage for the complete package and staging overhead.";
        goto fail;
    }
    uint8_t random[16];
    char random_hex[33];
    arc4random_buf(random, 16);
    hex(random, 16, random_hex);
    snprintf(stage_name, sizeof(stage_name), ".pdlan-%s", random_hex);
    if (mkdirat(temporary, stage_name, 0700) < 0 ||
        (stage = openat(temporary, stage_name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)) <
            0 ||
        mkdirat(stage, "package", 0700) < 0 ||
        (package = openat(stage, "package", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)) < 0) {
        error = "Cannot create a private import stage. Check storage and permissions.";
        goto fail;
    }
    snprintf(package_path, sizeof(package_path), "%s/%s/package", temporary_path, stage_name);
    inventory.capacity = 1;
    while (inventory.capacity < (size_t)expected_entries * 2)
        inventory.capacity *= 2;
    inventory.items = calloc(expected_entries, sizeof(Item));
    inventory.slots = calloc(inventory.capacity, sizeof(uint32_t));
    if (!inventory.items || !inventory.slots) {
        error = "Receiver out of memory.";
        goto fail;
    }
    const char *ready = diagnostics_enabled ? "ready;metrics=1" : "ready";
    if (!frame_send(&wire, ACK, ready, strlen(ready)))
        goto cleanup;
    while (!stopping && frame_read(&wire, &type, body, &length)) {
        if (verified && type != COMMIT)
            goto fail;
        if (type == ENTRY) {
            if (file >= 0 || inventory.count >= expected_entries || length < 43)
                goto fail;
            int kind = body[0];
            size_t path_length = (size_t)getnum(body + 1, 2);
            uint64_t size = getnum(body + 3, 8);
            if (kind > 1 || length != 43 + path_length ||
                !safe_path((char *)body + 43, path_length, 0) || size > FILE_MAX_BYTES ||
                size > total - planned ||
                (!kind && (size || !same(body + 11, (const uint8_t[32]){0}, 32))))
                goto fail;
            char path[PATH_MAX_WIRE + 1];
            memcpy(path, body + 43, path_length);
            path[path_length] = 0;
            if (lookup(&inventory, path, 1)) {
                error = "Duplicate or case-colliding package path.";
                goto fail;
            }
            Item *item = &inventory.items[inventory.count];
            item->path = strdup(path);
            item->kind = kind;
            item->size = size;
            memcpy(item->hash, body + 11, 32);
            if (!item->path) {
                error = "Receiver out of memory.";
                goto fail;
            }
            ++inventory.count;
            planned += size;
            char leaf[256];
            int parent = parent_fd(package, path, leaf);
            if (parent < 0) {
                error = "Entry parent is missing, linked or unsafe.";
                goto fail;
            }
            if (kind) {
                file = openat(parent, leaf, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC,
                              0600);
                close(parent);
                if (file < 0) {
                    error = "Cannot create package file; path exists or storage is unavailable.";
                    goto fail;
                }
                active = item;
                remaining = size;
                sha_256_init(&file_hash, actual_hash);
            } else {
                int result = mkdirat(parent, leaf, 0700);
                close(parent);
                if (result < 0) {
                    error =
                        "Cannot create package directory; path exists or storage is unavailable.";
                    goto fail;
                }
            }
            if (!frame_send(&wire, ACK, "entry", 5))
                goto cleanup;
        } else if (type == DATA) {
            if (file < 0 || !length || length > remaining)
                goto fail;
            double started = metric_clock();
            int written = write_all(file, body, length);
            metric_add(&metrics.file_write_s, started);
            if (!written) {
                error = errno == ENOSPC ? "Insufficient storage while writing package."
                                        : "Package write failed; staging discarded.";
                goto fail;
            }
            started = metric_clock();
            sha_256_write(&file_hash, body, length);
            metric_add(&metrics.file_hash_s, started);
            if (diagnostics_enabled) {
                metrics.bytes += length;
                ++metrics.data_frames;
            }
            remaining -= length;
        } else if (type == END_FILE) {
            if (file < 0 || length || remaining)
                goto fail;
            sha_256_close(&file_hash);
            if (!same(actual_hash, active->hash, 32)) {
                error = "File SHA-256 mismatch; staging discarded.";
                goto fail;
            }
            double started = metric_clock();
            int flushed = fsync(file);
            metric_add(&metrics.fsync_s, started);
            if (diagnostics_enabled) {
                ++metrics.files;
                ++metrics.fsyncs;
            }
            if (flushed < 0) {
                error = "File flush failed; package was not published.";
                goto fail;
            }
            if (close(file) < 0) {
                file = -1;
                error = "File close failed; package was not published.";
                goto fail;
            }
            file = -1;
            completed += active->size;
            active = NULL;
            if (!frame_send(&wire, ACK, "stored", 6))
                goto cleanup;
        } else if (type == VERIFY) {
            if (file >= 0 || length || inventory.count != expected_entries || completed != total)
                goto fail;
            double started = metric_clock();
            int valid = same_inode(package, stage, "package") &&
                        verify_staging(package, &inventory, &wire, total) &&
                        strict_recipe(package, package_path) &&
                        inventory_hashes(package, &inventory);
            metric_add(&metrics.verify_s, started);
            if (!valid) {
                error = "Invalid recipe, required package structure or immutable inventory.";
                goto fail;
            }
            double permissions_started = metric_clock();
            int permissions_ok = package_permissions(package, &inventory, &wire, total);
            metric_add(&metrics.permissions_s, permissions_started);
            if (!permissions_ok) {
                snprintf(diagnostic_error, sizeof(diagnostic_error),
                         "Cannot prepare Playden import permissions: errno=%d (%s).", errno,
                         strerror(errno));
                error = diagnostic_error;
                goto fail;
            }
            verified = 1;
            if (!frame_send(&wire, ACK, "verified", 8))
                goto cleanup;
        } else if (type == COMMIT) {
            if (!verified || file >= 0 || length)
                goto fail;
            double started = metric_clock();
            if (!same_inode(package, stage, "package") || !sync_dir(package) ||
                !import_root_permissions(destination, root, root_path) ||
                !publish(root, temporary, stage, stage_name, package_name, root_path,
                         temporary_path)) {
                int saved_error = errno;
                snprintf(diagnostic_error, sizeof(diagnostic_error),
                         "Publication failed or its durability is uncertain: errno=%d (%s); "
                         "inspect the import folder before retrying.",
                         saved_error, strerror(saved_error));
                error = diagnostic_error;
                goto fail;
            }
            metric_add(&metrics.commit_s, started);
            metrics.published = 1;
            char receipt[256];
            snprintf(receipt, sizeof(receipt), "%s/%s", roots[destination], package_name);
            if (!frame_send(&wire, ACK, receipt, strlen(receipt)))
                goto cleanup;
            /* Legacy clients close after the exact original publication receipt.
               Only a capable client requests this authenticated, bounded record. */
            if (diagnostics_enabled && frame_read(&wire, &type, body, &length) &&
                type == GET_METRICS && length == 0)
                send_metrics(&wire, connection_started);
            goto cleanup;
        } else
            goto fail;
    }
    goto cleanup;
fail:
    printf("receiver_error %s\n", error);
    frame_send(&wire, ERROR_REPLY, error, strlen(error));
cleanup:
    /* Report the transfer before filesystem deletion/close: slow storage, a
       forced unload or a cleanup fault must not hide the transfer timings. */
    metric_report(connection_started);
#ifdef HOST_TEST
    /* Deterministic test gate: prove metrics escape even when cleanup is blocked. */
    if (test_pause_cleanup && stage >= 0)
        (void)getchar();
#endif
    double cleanup_started = metric_clock();
    int cleanup_error = 0;
    if (file >= 0)
        close(file);
    if (package >= 0)
        close(package);
    if (stage >= 0) {
        if (temporary >= 0 && same_inode(stage, temporary, stage_name)) {
            if (!remove_contents(stage))
                cleanup_error = errno ? errno : EIO;
            if (unlinkat(temporary, stage_name, AT_REMOVEDIR) < 0 && !cleanup_error)
                cleanup_error = errno ? errno : EIO;
        } else {
            cleanup_error = ESTALE;
        }
        close(stage);
    }
    if (root >= 0)
        close(root);
    if (temporary >= 0)
        close(temporary);
    if (inventory.items)
        for (size_t i = 0; i < inventory.count; i++)
            free(inventory.items[i].path);
    free(inventory.items);
    free(inventory.slots);
    free(body);
    memset(wire.key, 0, sizeof(wire.key));
    if (diagnostics_enabled) {
        printf("receiver_cleanup wall_s=%.6f complete=%d errno=%d (%s)\n",
               metric_clock() - cleanup_started, !cleanup_error, cleanup_error,
               strerror(cleanup_error));
        fflush(stdout);
    }
}

/* Optional experiment: set buffers before listen/connect for window negotiation.
   Unsupported requests never disable authentication or filesystem checks. */
static void tune_socket(int fd, const char *side) {
    int options[] = {SO_SNDBUF, SO_RCVBUF};
    const char *names[] = {"send", "receive"};
    for (int i = 0; i < 2; ++i) {
        int saved = errno, error = 0, effective = 0;
        if (socket_buffer &&
            setsockopt(fd, SOL_SOCKET, options[i], &socket_buffer, sizeof(socket_buffer)) < 0)
            error = errno;
        socklen_t size = sizeof(effective);
        if (getsockopt(fd, SOL_SOCKET, options[i], &effective, &size) < 0 && !error)
            error = errno;
        if (diagnostics_enabled)
            printf("receiver_socket side=%s direction=%s requested=%d effective=%d errno=%d (%s)\n",
                   side, names[i], socket_buffer, effective, error, strerror(error));
        errno = saved;
    }
}
int main(int argc, char **argv) {
    char bind_address[INET_ADDRSTRLEN] = {0};
    unsigned port = 9025;
    int once = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--bind") && i + 1 < argc) {
            if (strlen(argv[++i]) >= sizeof(bind_address))
                return 2;
            strcpy(bind_address, argv[i]);
        } else if (!strcmp(argv[i], "--port") && i + 1 < argc) {
            char *end;
            unsigned long value = strtoul(argv[++i], &end, 10);
            if (*end || value > 65535)
                return 2;
            port = (unsigned)value;
        } else if (!strcmp(argv[i], "--diagnostics")) {
            diagnostics_enabled = 1;
        } else if (!strcmp(argv[i], "--socket-buffer") && i + 1 < argc) {
            char *end;
            long value = strtol(argv[++i], &end, 10);
            if (*end || value < 4096 || value > 4 * 1024 * 1024)
                return 2;
            socket_buffer = (int)value;
        }
#ifdef HOST_TEST
        else if (!strcmp(argv[i], "--test-root") && i + 1 < argc) {
            if (strlen(argv[++i]) >= sizeof(test_root))
                return 2;
            strcpy(test_root, argv[i]);
        } else if (!strcmp(argv[i], "--test-free-bytes") && i + 1 < argc) {
            char *end;
            test_free = strtoull(argv[++i], &end, 10);
            if (*end)
                return 2;
        } else if (!strcmp(argv[i], "--once"))
            once = 1;
        else if (!strcmp(argv[i], "--test-write-limit") && i + 1 < argc) {
            char *end;
            test_write_limit = strtoull(argv[++i], &end, 10);
            if (*end)
                return 2;
        } else if (!strcmp(argv[i], "--test-stat-result") && i + 1 < argc) {
            char *end;
            long value = strtol(argv[++i], &end, 10);
            if (*end || value == 0 || value < -4095 || value > 4095)
                return 2;
            test_stat_result = (int)value;
            test_stat_pending = 1;
        } else if (!strcmp(argv[i], "--test-stat-errno") && i + 1 < argc) {
            char *end;
            long value = strtol(argv[++i], &end, 10);
            if (*end || value < 0 || value > 4095)
                return 2;
            test_stat_errno = (int)value;
        } else if (!strcmp(argv[i], "--test-stat-dot")) {
            test_stat_dot = 1;
        } else if (!strcmp(argv[i], "--test-permission-errno") && i + 1 < argc) {
            char *end;
            long value = strtol(argv[++i], &end, 10);
            if (*end || value <= 0 || value > 4095)
                return 2;
            test_permission_errno = (int)value;
        } else if (!strcmp(argv[i], "--test-pause-cleanup")) {
            test_pause_cleanup = 1;
        } else if (!strcmp(argv[i], "--test-stage-device-mismatch")) {
            test_stage_device_mismatch = 1;
        } else if (!strcmp(argv[i], "--test-rename-errno") && i + 1 < argc) {
            char *end;
            long value = strtol(argv[++i], &end, 10);
            if (*end || value <= 0 || value > 4095)
                return 2;
            test_rename_errno = (int)value;
        }
#endif
        else {
            fprintf(stderr, "Invalid receiver option.\n");
            return 2;
        }
    }
#ifdef HOST_TEST
    if (!test_root[0]) {
        fprintf(stderr, "Host tests require --test-root.\n");
        return 2;
    }
#endif
    if (!bind_address[0] && !choose_address(bind_address)) {
        fprintf(stderr, "No private LAN interface; receiver not started.\n");
        return 1;
    }
    struct sockaddr_in address;
    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_port = htons((uint16_t)port);
    if (inet_pton(AF_INET, bind_address, &address.sin_addr) != 1 ||
        !private_ip(address.sin_addr.s_addr)) {
        fprintf(stderr,
                "Bind to a private LAN IPv4 address; wildcard/public binds are disabled.\n");
        return 2;
    }
    signal(SIGPIPE, SIG_IGN);
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = stop_receiver;
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGINT, &action, NULL);
    umask(0077);
    setbuf(stdout, NULL);
    int listener = socket(AF_INET, SOCK_STREAM, 0);
    if (listener < 0)
        return 1;
    int yes = 1;
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    /* Configure silently until the pairing banner (host harness contract). */
    int diagnostic_mode = diagnostics_enabled;
    diagnostics_enabled = 0;
    tune_socket(listener, "listener");
    diagnostics_enabled = diagnostic_mode;
    if (bind(listener, (struct sockaddr *)&address, sizeof(address)) < 0 ||
        listen(listener, 2) < 0) {
        perror("receiver bind/listen");
        close(listener);
        return 1;
    }
    socklen_t size = sizeof(address);
    getsockname(listener, (struct sockaddr *)&address, &size);
    uint8_t key[32];
    char token[65];
    arc4random_buf(key, 32);
    hex(key, 32, token);
    show_pairing(bind_address, ntohs(address.sin_port), token);
    memset(token, 0, sizeof(token));
    tune_socket(listener, "listener");
    time_t expiry = time(NULL) + 3600;
    while (!stopping && time(NULL) < expiry) {
        fd_set readfds;
        FD_ZERO(&readfds);
        FD_SET(listener, &readfds);
        struct timeval wait = {1, 0};
        int ready = select(listener + 1, &readfds, NULL, NULL, &wait);
        if (ready < 0 && errno != EINTR)
            break;
        if (ready <= 0)
            continue;
        struct sockaddr_in peer;
        socklen_t peer_size = sizeof(peer);
        int client = accept(listener, (struct sockaddr *)&peer, &peer_size);
        if (client < 0)
            continue;
        tune_socket(client, "accepted");
        setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &yes, sizeof(yes));
        struct timeval timeout = {15, 0};
        setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
        setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
        if (private_ip(peer.sin_addr.s_addr))
            connection(client, key);
        close(client);
        if (once)
            break;
    }
    close(listener);
    memset(key, 0, sizeof(key));
    return 0;
}
