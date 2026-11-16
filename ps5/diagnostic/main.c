/* Read-only PS5 diagnostic and Minecraft import discovery. */
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <unistd.h>
#include "recipe_check.h"

#ifndef PS5_INTERNAL_IMPORT
#define PS5_INTERNAL_IMPORT "/data/playden-import"
#endif
#ifndef PS5_USB_IMPORT
#define PS5_USB_IMPORT "/mnt/usb0/playden/import"
#endif

static void probe(const char *path) {
    struct stat info;
    if (lstat(path, &info) != 0) {
        printf("path=%s status=unavailable errno=%d (%s)\n",
               path, errno, strerror(errno));
        return;
    }
    const char *kind = S_ISDIR(info.st_mode) ? "directory" :
                       S_ISREG(info.st_mode) ? "file" :
                       S_ISLNK(info.st_mode) ? "symlink" : "other";
    printf("path=%s status=present type=%s\n", path, kind);
}

static int regular_file(const char *directory, const char *relative) {
    char path[1024];
    struct stat info;
    int n = snprintf(path, sizeof(path), "%s/%s", directory, relative);
    if (n < 0 || (size_t)n >= sizeof(path)) return 0;
    /* lstat avoids following the final component when it is a symlink. */
    return lstat(path, &info) == 0 && S_ISREG(info.st_mode);
}

static void scan_imports(const char *root, const char *label) {
    DIR *dir = opendir(root);
    if (!dir) {
        printf("import_root=%s status=unavailable errno=%d\n", label, errno);
        return;
    }
    printf("import_root=%s status=present\n", label);
    unsigned int packages = 0;
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        const char *name = entry->d_name;
        if (!strcmp(name, ".") || !strcmp(name, "..")) continue;
        /* Avoid control characters in console output and oversized paths. */
        size_t len = strlen(name);
        if (len == 0 || len > 200) continue;
        int printable = 1;
        for (size_t i = 0; i < len; ++i) {
            if ((unsigned char)name[i] < 32 || (unsigned char)name[i] == 127) {
                printable = 0;
                break;
            }
        }
        if (!printable) continue;
        char folder[1024];
        int n = snprintf(folder, sizeof(folder), "%s/%s", root, name);
        if (n < 0 || (size_t)n >= sizeof(folder)) continue;
        struct stat info;
        /* Never traverse a symlinked package directory. */
        if (lstat(folder, &info) != 0 || !S_ISDIR(info.st_mode)) continue;
        int recipe = regular_file(folder, "recipe.json");
        int args = regular_file(folder, "playden.args");
        int java = regular_file(folder, "java/bin/java.exe");
        printf("package_root=%s package=%s recipe=%s args=%s java=%s status=%s\n",
               label, name, recipe ? "present" : "missing",
               args ? "present" : "missing", java ? "present" : "missing",
               recipe && args && java ? "structure-present" : "incomplete");
        if (recipe) {
            char reason[80];
            int valid = recipe_check(folder, reason, sizeof(reason));
            int args_nonempty = 0;
            char argpath[1024];
            struct stat argstat;
            int an = snprintf(argpath, sizeof(argpath), "%s/playden.args", folder);
            if (an > 0 && (size_t)an < sizeof(argpath) &&
                lstat(argpath, &argstat) == 0 && S_ISREG(argstat.st_mode) &&
                argstat.st_size > 0) args_nonempty = 1;
            const char *status = valid && args_nonempty && java ? "valid" : "invalid";
            printf("package_validation_root=%s package=%s status=%s recipe_check=%s args_check=%s java_check=%s\n",
                   label, name, status, reason, args_nonempty ? "nonempty" : "missing-or-empty",
                   java ? "present" : "missing");
        }
        ++packages;
    }
    closedir(dir);
    printf("import_root=%s packages=%u\n", label, packages);
}

int main(void) {
    struct utsname system;
    puts("minecraft-playden-ps5-diagnostic version=0.3 mode=read-only");
    printf("pid=%ld uid=%ld euid=%ld\n",
           (long)getpid(), (long)getuid(), (long)geteuid());
    if (uname(&system) == 0) {
        printf("system=%s release=%s machine=%s\n",
               system.sysname, system.release, system.machine);
    } else {
        printf("uname_error=%d (%s)\n", errno, strerror(errno));
    }
    probe("/data");
    probe("/data/playden");
    probe("/data/homebrew");
    probe("/mnt/usb0");
    scan_imports(PS5_INTERNAL_IMPORT, "internal");
    scan_imports(PS5_USB_IMPORT, "usb");
    puts("diagnostic_complete=true");
    fflush(stdout);
    return 0;
}
