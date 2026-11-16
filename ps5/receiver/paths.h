#ifndef PDLAN_PATHS_H
#define PDLAN_PATHS_H
#include "wire.h"
#include <ctype.h>
#include <dirent.h>
#include <fcntl.h>
#include <stdlib.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>

static int utf8_valid(const unsigned char *s, size_t n) {
    for (size_t i = 0; i < n;) {
        unsigned c = s[i++];
        if (c < 128)
            continue;
        unsigned v;
        int need;
        if (c >= 0xc2 && c <= 0xdf) {
            v = c & 31;
            need = 1;
        } else if (c >= 0xe0 && c <= 0xef) {
            v = c & 15;
            need = 2;
        } else if (c >= 0xf0 && c <= 0xf4) {
            v = c & 7;
            need = 3;
        } else
            return 0;
        if (i + (size_t)need > n)
            return 0;
        for (int k = 0; k < need; k++) {
            c = s[i++];
            if ((c & 0xc0) != 0x80)
                return 0;
            v = (v << 6) | (c & 63);
        }
        if ((need == 2 && v < 0x800) || (need == 3 && v < 0x10000) || v > 0x10ffff ||
            (v >= 0xd800 && v <= 0xdfff))
            return 0;
    }
    return 1;
}
static int safe_path(const char *path, size_t n, int package) {
    if (!n || n > (package ? 120 : PATH_MAX_WIRE) || memchr(path, 0, n) ||
        !utf8_valid((const unsigned char *)path, n))
        return 0;
    if (package && (path[0] == '.' || memchr(path, '/', n)))
        return 0;
    size_t start = 0, components = 0;
    for (size_t i = 0; i <= n; i++) {
        if (i < n) {
            unsigned char c = (unsigned char)path[i];
            if (c < 32 || c == 127 || strchr("<>:\"\\|?*", c))
                return 0;
        }
        if (i == n || path[i] == '/') {
            if (++components > 32)
                return 0;
            size_t length = i - start;
            if (!length || length > 255 || path[i - 1] == ' ' || path[i - 1] == '.')
                return 0;
            char stem[5] = {0};
            size_t k = 0;
            while (k < length && k < 4 && path[start + k] != '.') {
                stem[k] = (char)toupper((unsigned char)path[start + k]);
                ++k;
            }
            if ((k == 3 && (!strcmp(stem, "CON") || !strcmp(stem, "PRN") || !strcmp(stem, "AUX") ||
                            !strcmp(stem, "NUL"))) ||
                (k == 4 && (!strncmp(stem, "COM", 3) || !strncmp(stem, "LPT", 3)) &&
                 stem[3] >= '1' && stem[3] <= '9'))
                return 0;
            start = i + 1;
        }
    }
    return 1;
}
/* Every path component is opened relative to a pinned descriptor, never followed. */
static int open_dirs(int base, const char *path, int create) {
    int fd = dup(base);
    if (fd < 0)
        return -1;
    char copy[2048];
    if (strlen(path) >= sizeof(copy)) {
        close(fd);
        return -1;
    }
    strcpy(copy, path);
    char *save = NULL, *component = strtok_r(copy, "/", &save);
    while (component) {
        if (!strcmp(component, ".") || !strcmp(component, "..")) {
            close(fd);
            return -1;
        }
        if (create && mkdirat(fd, component, 0700) < 0 && errno != EEXIST) {
            close(fd);
            return -1;
        }
        int next = openat(fd, component, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        close(fd);
        if (next < 0)
            return -1;
        fd = next;
        component = strtok_r(NULL, "/", &save);
    }
    return fd;
}
static int open_absolute(const char *path, int create) {
    if (path[0] != '/')
        return -1;
    int root = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (root < 0)
        return -1;
    int fd = open_dirs(root, path, create);
    close(root);
    return fd;
}
static int parent_fd(int root, const char *path, char leaf[256]) {
    char parent[PATH_MAX_WIRE + 1];
    strcpy(parent, path);
    char *slash = strrchr(parent, '/');
    const char *name = slash ? slash + 1 : parent;
    if (strlen(name) > 255)
        return -1;
    strcpy(leaf, name);
    if (slash) {
        *slash = 0;
        return open_dirs(root, parent, 0);
    }
    return dup(root);
}
static int read_file(int root, const char *path, size_t max, char **out, size_t *length) {
    char leaf[256];
    int parent = parent_fd(root, path, leaf);
    if (parent < 0)
        return 0;
    int fd = openat(parent, leaf, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    close(parent);
    if (fd < 0)
        return 0;
    struct stat st;
    if (fstat(fd, &st) < 0 || !S_ISREG(st.st_mode) || st.st_size <= 0 ||
        (uint64_t)st.st_size > max) {
        close(fd);
        return 0;
    }
    *length = (size_t)st.st_size;
    *out = malloc(*length + 1);
    if (!*out) {
        close(fd);
        return 0;
    }
    size_t offset = 0;
    while (offset < *length) {
        ssize_t n = read(fd, *out + offset, *length - offset);
        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0) {
            free(*out);
            *out = NULL;
            close(fd);
            return 0;
        }
        offset += (size_t)n;
    }
    (*out)[*length] = 0;
    close(fd);
    return 1;
}
/* Called only on the random stage created and held open by this connection. */
static int remove_contents(int root) {
    DIR *dir = fdopendir(dup(root));
    if (!dir)
        return 0;
    struct dirent *e;
    int ok = 1;
    while ((e = readdir(dir))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, ".."))
            continue;
        struct stat st;
        if (fstatat(root, e->d_name, &st, AT_SYMLINK_NOFOLLOW) < 0) {
            ok = 0;
            continue;
        }
        if (S_ISDIR(st.st_mode)) {
            int child = openat(root, e->d_name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            if (child < 0) {
                ok = 0;
                continue;
            }
            if (!remove_contents(child))
                ok = 0;
            close(child);
            if (unlinkat(root, e->d_name, AT_REMOVEDIR) < 0)
                ok = 0;
        } else if (unlinkat(root, e->d_name, 0) < 0)
            ok = 0;
    }
    closedir(dir);
    return ok;
}
static int same_inode(int fd, int parent, const char *name) {
    struct stat a, b;
    return fstat(fd, &a) == 0 && fstatat(parent, name, &b, AT_SYMLINK_NOFOLLOW) == 0 &&
           S_ISDIR(b.st_mode) && a.st_dev == b.st_dev && a.st_ino == b.st_ino;
}
#endif
