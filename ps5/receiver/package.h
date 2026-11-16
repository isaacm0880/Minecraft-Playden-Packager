#ifndef PDLAN_PACKAGE_H
#define PDLAN_PACKAGE_H
#include "../diagnostic/recipe_check.h"
#include "paths.h"
typedef struct {
    char *path;
    uint64_t size;
    uint8_t hash[32];
    int kind, listed;
} Item;
typedef struct {
    Item *items;
    uint32_t *slots;
    size_t capacity, count;
} Inventory;

static uint32_t path_hash(const char *s) {
    uint32_t h = 2166136261u;
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c >= 'A' && c <= 'Z')
            c += 32;
        h = (h ^ c) * 16777619u;
    }
    return h;
}
static Item *lookup(Inventory *v, const char *path, int inserting) {
    size_t slot = path_hash(path) & (v->capacity - 1);
    while (v->slots[slot]) {
        Item *item = &v->items[v->slots[slot] - 1];
        if (!strcasecmp(item->path, path))
            return inserting ? item : (!strcmp(item->path, path) ? item : NULL);
        slot = (slot + 1) & (v->capacity - 1);
    }
    if (inserting)
        v->slots[slot] = (uint32_t)v->count + 1;
    return NULL;
}
/* Reject unknown and duplicate keys, including escaped spellings of keys. */
static int keys(const char *s, size_t n, const char *const *allowed, size_t count,
                size_t required) {
    J j = {s, n, 0, 0};
    uint64_t seen = 0;
    size_t found = 0;
    skipws(&j);
    if (j.p >= n || s[j.p++] != '{')
        return 0;
    skipws(&j);
    if (j.p < n && s[j.p] == '}')
        return required == 0;
    for (;;) {
        size_t a, b;
        if (!stringval(&j, &a, &b))
            return 0;
        size_t k;
        for (k = 0; k < count; k++)
            if (b - a == strlen(allowed[k]) && !memcmp(s + a, allowed[k], b - a))
                break;
        if (k == count || (seen & (UINT64_C(1) << k)))
            return 0;
        seen |= UINT64_C(1) << k;
        ++found;
        skipws(&j);
        if (j.p >= n || s[j.p++] != ':' || !value(&j))
            return 0;
        skipws(&j);
        if (j.p >= n)
            return 0;
        char c = s[j.p++];
        if (c == '}')
            break;
        if (c != ',')
            return 0;
    }
    skipws(&j);
    return j.p == n && found >= required;
}
static int json_document(const char *s, size_t n) {
    J j = {s, n, 0, 0};
    skipws(&j);
    if (j.p >= n || s[j.p] != '{' || !utf8_valid((const unsigned char *)s, n) || !value(&j))
        return 0;
    skipws(&j);
    return j.p == n;
}
static int hex4(const char *s, uint32_t *result) {
    uint32_t v = 0;
    for (int i = 0; i < 4; i++) {
        unsigned char c = (unsigned char)s[i];
        int k;
        if (c >= '0' && c <= '9')
            k = c - '0';
        else if (c >= 'a' && c <= 'f')
            k = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F')
            k = c - 'A' + 10;
        else
            return 0;
        v = (v << 4) | (unsigned)k;
    }
    *result = v;
    return 1;
}
static int json_path(const char *s, size_t n, char out[PATH_MAX_WIRE + 1]) {
    size_t p = 0;
    for (size_t i = 0; i < n;) {
        unsigned char c = (unsigned char)s[i++];
        uint32_t cp = c;
        if (c == '\\') {
            if (i >= n)
                return 0;
            c = (unsigned char)s[i++];
            if (c == '/' || c == '\\' || c == '"')
                cp = c;
            else if (c == 'u') {
                if (i + 4 > n || !hex4(s + i, &cp))
                    return 0;
                i += 4;
                if (cp >= 0xd800 && cp <= 0xdbff) {
                    uint32_t low;
                    if (i + 6 > n || s[i] != '\\' || s[i + 1] != 'u' || !hex4(s + i + 2, &low) ||
                        low < 0xdc00 || low > 0xdfff)
                        return 0;
                    i += 6;
                    cp = 0x10000 + ((cp - 0xd800) << 10) + (low - 0xdc00);
                } else if (cp >= 0xdc00 && cp <= 0xdfff)
                    return 0;
            } else
                return 0;
            uint8_t bytes[4];
            size_t z;
            if (cp < 128) {
                bytes[0] = (uint8_t)cp;
                z = 1;
            } else if (cp < 0x800) {
                bytes[0] = 0xc0 | (cp >> 6);
                bytes[1] = 0x80 | (cp & 63);
                z = 2;
            } else if (cp < 0x10000) {
                bytes[0] = 0xe0 | (cp >> 12);
                bytes[1] = 0x80 | ((cp >> 6) & 63);
                bytes[2] = 0x80 | (cp & 63);
                z = 3;
            } else {
                bytes[0] = 0xf0 | (cp >> 18);
                bytes[1] = 0x80 | ((cp >> 12) & 63);
                bytes[2] = 0x80 | ((cp >> 6) & 63);
                bytes[3] = 0x80 | (cp & 63);
                z = 4;
            }
            if (p + z > PATH_MAX_WIRE)
                return 0;
            memcpy(out + p, bytes, z);
            p += z;
        } else {
            if (p >= PATH_MAX_WIRE)
                return 0;
            out[p++] = (char)c;
        }
    }
    out[p] = 0;
    return safe_path(out, p, 0);
}
static int inventory_hashes(int fd, Inventory *v) {
    char *doc = NULL;
    size_t n;
    int ok = 0;
    const char *const allowed[] = {"schema",   "launch_profile", "source", "version",
                                   "username", "classpath",      "files",  "loader"};
    if (!read_file(fd, "package.json", 16 * 1024 * 1024, &doc, &n))
        return 0;
    if (!json_document(doc, n) || !keys(doc, n, allowed, 8, 7) || !field(doc, n, "schema", "2"))
        goto done;
    const char *files;
    size_t length;
    if (!keyval(doc, n, "files", &files, &length) || length < 2 || files[0] != '{')
        goto done;
    J j = {files, length, 1, 0};
    skipws(&j);
    if (j.p >= length || files[j.p] == '}')
        goto done;
    for (;;) {
        size_t a, b;
        char path[PATH_MAX_WIRE + 1];
        if (!stringval(&j, &a, &b) || !json_path(files + a, b - a, path))
            goto done;
        Item *item = lookup(v, path, 0);
        if (!item || !item->kind || item->listed || !strcmp(path, "package.json"))
            goto done;
        item->listed = 1;
        skipws(&j);
        if (j.p >= length || files[j.p++] != ':' || !stringval(&j, &a, &b) || b - a != 64)
            goto done;
        uint8_t expected[32];
        for (int k = 0; k < 32; k++) {
            char digits[5] = {'0', '0', files[a + 2 * k], files[a + 2 * k + 1], 0};
            uint32_t byte;
            if (!hex4(digits, &byte))
                goto done;
            expected[k] = (uint8_t)byte;
        }
        if (!same(expected, item->hash, 32))
            goto done;
        skipws(&j);
        if (j.p >= length)
            goto done;
        char c = files[j.p++];
        if (c == '}')
            break;
        if (c != ',')
            goto done;
    }
    const char *required[] = {"recipe.json",       "playden.args",
                              "java/bin/java.exe", "java/bin/server/jvm.dll",
                              "java/release",      "metadata/version.json"};
    for (size_t k = 0; k < sizeof(required) / sizeof(required[0]); k++) {
        Item *item = lookup(v, required[k], 0);
        if (!item || !item->listed || !item->size)
            goto done;
    }
    ok = 1;
done:
    free(doc);
    return ok;
}
static int strict_recipe(int fd, const char *folder) {
    char reason[128];
    if (!recipe_check(folder, reason, sizeof(reason)))
        return 0;
    char *doc = NULL;
    size_t n;
    int ok = 0;
    const char *const top[] = {"recipe_id",  "recipe_version", "game",     "launch_entries",
                               "executable", "architecture",   "graphics", "community"};
    const char *const game[] = {"store", "key", "name"};
    const char *const community[] = {"format_version", "status"};
    const char *const entry[] = {"id", "label", "executable", "arguments"};
    if (!read_file(fd, "recipe.json", RECIPE_MAX, &doc, &n))
        return 0;
    if (!json_document(doc, n) || !keys(doc, n, top, 8, 8))
        goto done;
    const char *s;
    size_t length;
    if (!keyval(doc, n, "game", &s, &length) || !keys(s, length, game, 3, 3))
        goto done;
    if (!keyval(doc, n, "community", &s, &length) || !keys(s, length, community, 2, 2))
        goto done;
    if (!keyval(doc, n, "launch_entries", &s, &length) || length < 2 || s[0] != '[')
        goto done;
    J j = {s, length, 1, 0};
    skipws(&j);
    size_t start = j.p;
    if (!value(&j) || !keys(s + start, j.p - start, entry, 4, 4))
        goto done;
    skipws(&j);
    if (j.p >= length || s[j.p++] != ']')
        goto done;
    skipws(&j);
    if (j.p != length)
        goto done;
    ok = 1;
done:
    free(doc);
    return ok;
}

static int count_tree(int root, unsigned depth, size_t *count) {
    if (depth > 32)
        return 0;
    DIR *dir = fdopendir(dup(root));
    if (!dir)
        return 0;
    int ok = 1;
    struct dirent *entry;
    while ((entry = readdir(dir))) {
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, ".."))
            continue;
        struct stat st;
        if (++*count > ENTRY_MAX || fstatat(root, entry->d_name, &st, AT_SYMLINK_NOFOLLOW) < 0) {
            ok = 0;
            break;
        }
        if (S_ISDIR(st.st_mode)) {
            int child =
                openat(root, entry->d_name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            if (child < 0) {
                ok = 0;
                break;
            }
            ok = count_tree(child, depth + 1, count);
            close(child);
            if (!ok)
                break;
        } else if (!S_ISREG(st.st_mode) || st.st_nlink != 1) {
            ok = 0;
            break;
        }
    }
    closedir(dir);
    return ok;
}

/* Verify bytes read back from staging, not merely the incoming network stream. */
static int verify_staging(int root, Inventory *inventory, Wire *wire, uint64_t total) {
    uint8_t block[65536], digest[32], progress[16];
    uint64_t verified = 0, last_report = 0;
    for (size_t i = 0; i < inventory->count; i++) {
        Item *item = &inventory->items[i];
        char leaf[256];
        int parent = parent_fd(root, item->path, leaf);
        if (parent < 0)
            return 0;
        int fd =
            openat(parent, leaf,
                   O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC | (item->kind ? 0 : O_DIRECTORY));
        close(parent);
        if (fd < 0)
            return 0;
        struct stat st;
        int valid = fstat(fd, &st) == 0;
        if (item->kind)
            valid = valid && S_ISREG(st.st_mode) && st.st_nlink == 1 &&
                    (uint64_t)st.st_size == item->size;
        else
            valid = valid && S_ISDIR(st.st_mode);
        if (!valid) {
            close(fd);
            return 0;
        }
        if (item->kind) {
            struct Sha_256 hash;
            sha_256_init(&hash, digest);
            uint64_t read_bytes = 0;
            for (;;) {
                ssize_t n = read(fd, block, sizeof(block));
                if (n < 0 && errno == EINTR)
                    continue;
                if (n < 0) {
                    close(fd);
                    return 0;
                }
                if (!n)
                    break;
                read_bytes += (uint64_t)n;
                if (read_bytes > item->size) {
                    close(fd);
                    return 0;
                }
                verified += (uint64_t)n;
                sha_256_write(&hash, block, (size_t)n);
                if (verified - last_report >= 1024 * 1024) {
                    putnum(progress, verified, 8);
                    putnum(progress + 8, total, 8);
                    if (!frame_send(wire, VERIFY_PROGRESS, progress, 16)) {
                        close(fd);
                        return 0;
                    }
                    last_report = verified;
                }
            }
            sha_256_close(&hash);
            if (read_bytes != item->size || !same(digest, item->hash, 32)) {
                close(fd);
                return 0;
            }
        }
        close(fd);
        if (i % 128 == 0) {
            putnum(progress, verified, 8);
            putnum(progress + 8, total, 8);
            if (!frame_send(wire, VERIFY_PROGRESS, progress, 16))
                return 0;
            last_report = verified;
        }
    }
    size_t count = 0;
    return verified == total && count_tree(root, 0, &count) && count == inventory->count;
}
#endif
