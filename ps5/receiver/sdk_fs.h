#ifndef PDLAN_SDK_FS_H
#define PDLAN_SDK_FS_H
#include <errno.h>
#include <stdint.h>

/* FreeBSD reports syscall failure in carry, with a positive errno in rax.
   Positive values are also valid file descriptors: never guess from sign for
   openat/dup. Keep these wrappers local to this receiver, not the installed SDK. */
static inline long receiver_syscall_result(long value, int failed) {
    if (failed) {
        errno = value > 0 && value <= 4095 ? (int)value : EIO;
        return -1;
    }
    return value;
}

#ifdef PS5_RECEIVER
#include <fcntl.h>
#include <stdarg.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

typedef struct {
    long value;
    int failed;
} ReceiverSyscall;

static inline ReceiverSyscall receiver_syscall4(long number, uintptr_t a, uintptr_t b, uintptr_t c,
                                                uintptr_t d) {
    register uintptr_t fourth __asm__("r10") = d;
    long value = number;
    unsigned char failed;
    __asm__ volatile("syscall"
                     : "+a"(value), "=@ccc"(failed)
                     : "D"(a), "S"(b), "d"(c), "r"(fourth)
                     : "rcx", "r11", "memory");
    return (ReceiverSyscall){value, failed};
}

static inline int receiver_openat(int directory, const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_list args;
        va_start(args, flags);
        mode = (mode_t)va_arg(args, int);
        va_end(args);
    }
    ReceiverSyscall call = receiver_syscall4(SYS_openat, directory, (uintptr_t)path, flags, mode);
    return (int)receiver_syscall_result(call.value, call.failed);
}
static inline int receiver_mkdirat(int directory, const char *path, mode_t mode) {
    ReceiverSyscall call = receiver_syscall4(SYS_mkdirat, directory, (uintptr_t)path, mode, 0);
    return (int)receiver_syscall_result(call.value, call.failed);
}
static inline int receiver_unlinkat(int directory, const char *path, int flags) {
    ReceiverSyscall call = receiver_syscall4(SYS_unlinkat, directory, (uintptr_t)path, flags, 0);
    return (int)receiver_syscall_result(call.value, call.failed);
}
static inline int receiver_renameat(int from, const char *source, int to, const char *target) {
    ReceiverSyscall call =
        receiver_syscall4(SYS_renameat, from, (uintptr_t)source, to, (uintptr_t)target);
    return (int)receiver_syscall_result(call.value, call.failed);
}
static inline int receiver_dup(int fd) {
    ReceiverSyscall call = receiver_syscall4(SYS_dup, fd, 0, 0, 0);
    return (int)receiver_syscall_result(call.value, call.failed);
}
static inline int receiver_fstatfs(int fd, struct statfs *info) {
    ReceiverSyscall call = receiver_syscall4(SYS_fstatfs, fd, (uintptr_t)info, 0, 0);
    return (int)receiver_syscall_result(call.value, call.failed);
}

#define openat receiver_openat
#define mkdirat receiver_mkdirat
#define unlinkat receiver_unlinkat
#define renameat receiver_renameat
#define dup receiver_dup
#define fstatfs receiver_fstatfs
#endif
#endif
