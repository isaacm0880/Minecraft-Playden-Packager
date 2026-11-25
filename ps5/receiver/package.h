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
    const char *required[] = {"playden.args",
                              "java/bin/java.exe", "java/bin/server/jvm.dll",
                              "java/release",      "metadata/version.json"};
    for (size_t k = 0; k < sizeof(required) / sizeof(required[0]); k++) {
        Item *item = lookup(v, required[k], 0);
        if (!item || !item->listed || !item->size)
            goto done;
    }
    Item *current_recipe = lookup(v, "playden/recipe.json", 0);
    Item *legacy_recipe = lookup(v, "recipe.json", 0);
    if ((current_recipe && legacy_recipe) || (!current_recipe && !legacy_recipe)) goto done;
    Item *selected_recipe = current_recipe ? current_recipe : legacy_recipe;
    if (!selected_recipe->listed || !selected_recipe->size) goto done;
    {
        const char *art[] = {"playden/icon.png", "playden/background.png"};
        for (size_t k = 0; k < 2; k++) {
            Item *item = lookup(v, art[k], 0);
            if ((current_recipe || item) && (!item || !item->listed || !item->size)) goto done;
        }
    }
    if (lookup(v, "playden.java8-command", 0)) {
        const char *adapter[] = {"playden.java8-command", "playden-java8.jar"};
        for (size_t k = 0; k < 2; k++) {
            Item *item = lookup(v, adapter[k], 0);
            if (!item || !item->listed || !item->size) goto done;
        }
    }
    ok = 1;
done:
    free(doc);
    return ok;
}
/* Bounded PNG structure/CRC checks. The Python packager also fully decodes
   pixels; the receiver never interprets executable image codec code. */
static uint32_t png_u32(const unsigned char *p) {
    return (uint32_t)p[0]<<24 | (uint32_t)p[1]<<16 | (uint32_t)p[2]<<8 | p[3];
}
static uint32_t png_crc(const unsigned char *p, size_t n) {
    uint32_t crc = UINT32_C(0xffffffff);
    for (size_t i = 0; i < n; i++) {
        crc ^= p[i];
        for (int bit = 0; bit < 8; bit++) crc = (crc >> 1) ^ (UINT32_C(0xedb88320) & (0u - (crc & 1u)));
    }
    return crc ^ UINT32_C(0xffffffff);
}
static int png_picture(const unsigned char *data, size_t n) {
    const unsigned char signature[] = {137,80,78,71,13,10,26,10};
    if (n < 33 || memcmp(data, signature, 8) || png_u32(data+8) != 13 || memcmp(data+12,"IHDR",4)) return 0;
    uint32_t width = png_u32(data+16), height = png_u32(data+20);
    if (!width || !height || width > 8192 || height > 8192 || (uint64_t)width*height > UINT64_C(3840)*2160) return 0;
    int idat = 0;
    for (size_t p = 8; p < n;) {
        if (n-p < 12) return 0;
        uint32_t count = png_u32(data+p);
        if (count > n-p-12 || png_crc(data+p+4, (size_t)count+4) != png_u32(data+p+8+count)) return 0;
        if (p != 8 && !memcmp(data+p+4,"IHDR",4)) return 0;
        if (!memcmp(data+p+4,"IDAT",4) && count) idat = 1;
        if (!memcmp(data+p+4,"IEND",4)) return idat && count == 0 && p+12 == n;
        p += (size_t)count+12;
    }
    return 0;
}
static int strict_artwork(int fd, int required) {
    const char *names[] = {"playden/icon.png", "playden/background.png"};
    for (size_t k = 0; k < 2; k++) {
        struct stat info;
        if (!required && fstatat(fd, names[k], &info, AT_SYMLINK_NOFOLLOW) < 0 && errno == ENOENT) continue;
        char *data = NULL; size_t n;
        if (!read_file(fd, names[k], 32u*1024u*1024u, &data, &n)) return 0;
        int valid = png_picture((const unsigned char *)data, n);
        free(data);
        if (!valid) return 0;
    }
    return 1;
}
static int strict_recipe(int fd, const char *folder) {
    char reason[128];
    if (!recipe_check(folder, reason, sizeof(reason)))
        return 0;
    char *doc = NULL;
    size_t n;
    int ok = 0;
    const char *const top[] = {"recipe_id",  "recipe_version", "game",     "launch_entries",
                               "executable", "architecture",   "graphics", "community", "defaults"};
    const char *const game[] = {"store", "key", "name"};
    const char *const community[] = {"format_version", "status"};
    const char *const entry[] = {"id", "label", "executable", "arguments"};
    const char *name = recipe_name(folder);
    if (!name || !strict_artwork(fd, !strcmp(name, "playden/recipe.json"))) return 0;
    if (!read_file(fd, name, RECIPE_MAX, &doc, &n))
        return 0;
    if (!json_document(doc, n))
        goto done;
    const char *s;
    size_t length;
    const int has_defaults = keyval(doc, n, "defaults", &s, &length);
    /* Optional defaults never make another required top-level field optional. */
    if (!keys(doc, n, top, 9, has_defaults ? 9 : 8))
        goto done;
    if (has_defaults) {
        const char *const defaults[] = {"controller", "controller_layout"};
        /* Exactly the packager's built-in layout; arbitrary settings remain refused. */
        static const char layout[] =
            "\"cross = key:space;circle = key:q;square = key:e;triangle = key:f;"
            "l1 = mouse:wheel_up;r1 = mouse:wheel_down;l2 = mouse:right;r2 = mouse:left;"
            "l3 = key:lctrl;r3 = key:lshift;up = key:l;down = key:f3;"
            "left = key:t;right = mouse:middle;options = key:escape;create = key:f2;"
            "touchpad_left = key:tab;touchpad_right = key:f5;left_stick = keys;left_stick_keys = w,s,a,d;"
            "right_stick = mouse\"";
        if (!keys(s, length, defaults, 2, 2) ||
            !field(s, length, "controller", "\"keyboard\"") ||
            !field(s, length, "controller_layout", layout))
            goto done;
    }
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

/* Both hash readback and the namespace walk use the identical !QQ payload.
   The namespace heartbeat reports total/total only after all file hashes pass. */
static int verification_progress(Wire *wire, uint64_t verified, uint64_t total) {
    if (verified > total) {
        errno = EOVERFLOW;
        return 0;
    }
    uint8_t progress[16];
    putnum(progress, verified, 8);
    putnum(progress + 8, total, 8);
    return frame_send(wire, VERIFY_PROGRESS, progress, sizeof(progress));
}

static int count_tree(int root, unsigned depth, size_t *count, Wire *wire, uint64_t total) {
    if (depth > 32)
        return 0;
    DIR *dir = directory_stream(root);
    if (!dir)
        return 0;
    int ok = 1;
    struct dirent *entry;
    for (;;) {
        errno = 0;
        entry = readdir(dir);
        if (!entry) {
            if (errno)
                ok = 0;
            break;
        }
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, ".."))
            continue;
        struct stat st;
        if (++*count > ENTRY_MAX || stat_nofollow_at(root, entry->d_name, &st, NULL) < 0) {
            ok = 0;
            break;
        }
        if (*count % 128 == 0) {
            if (!verification_progress(wire, total, total)) {
                ok = 0;
                break;
            }
        }
        if (S_ISDIR(st.st_mode)) {
            int child =
                openat(root, entry->d_name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            if (child < 0) {
                ok = 0;
                break;
            }
            ok = count_tree(child, depth + 1, count, wire, total);
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
    uint8_t block[65536], digest[32];
    double walk_started = metric_clock();
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
                double started = metric_clock();
                ssize_t n = read(fd, block, sizeof(block));
                metric_add(&metrics.verify_read_s, started);
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
                if (verified > total || (uint64_t)n > total - verified) {
                    close(fd);
                    errno = EOVERFLOW;
                    return 0;
                }
                verified += (uint64_t)n;
                started = metric_clock();
                sha_256_write(&hash, block, (size_t)n);
                metric_add(&metrics.verify_hash_s, started);
                if (verified - last_report >= 1024 * 1024) {
                    if (!verification_progress(wire, verified, total)) {
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
            if (!verification_progress(wire, verified, total))
                return 0;
            last_report = verified;
        }
    }
    metric_add(&metrics.hash_walk_s, walk_started);
    size_t count = 0;
    double namespace_started = metric_clock();
    int ok =
        verified == total && count_tree(root, 0, &count, wire, total) && count == inventory->count;
    metric_add(&metrics.namespace_s, namespace_started);
    return ok;
}
#endif
