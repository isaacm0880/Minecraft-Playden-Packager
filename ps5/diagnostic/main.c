/* Read-only PS5 diagnostic payload. No filesystem writes or privilege changes. */
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <unistd.h>

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

int main(void) {
    struct utsname system;
    puts("minecraft-playden-ps5-diagnostic version=0.1 mode=read-only");
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
    probe("/data/playden-import");
    probe("/data/homebrew");
    probe("/mnt/usb0");
    puts("diagnostic_complete=true");
    fflush(stdout);
    return 0;
}
