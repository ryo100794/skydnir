/*
 * libcow.c — Copy-on-write LD_PRELOAD shim.
 *
 * Emulates overlayfs "copy-up" semantics on top of a hardlink-cloned
 * rootfs created with `cp -al lower merged`.
 *
 * When a process opens a regular file with st_nlink > 1 for writing
 * (or truncates it), we break the hardlink by copying the file to a
 * sibling temp path and renaming it over the original. After that,
 * the file in `merged` has its own inode and writes do not leak to
 * `lower` (the image rootfs).
 *
 * This gives containers per-instance isolation with near-zero disk
 * cost at creation time, mirroring the effect of overlayfs upperdir.
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/xattr.h>
#include <unistd.h>
#include <utime.h>

#ifndef PATH_MAX
#define PATH_MAX 4096
#endif

static int   (*real_open)   (const char *, int, ...);
static int   (*real_openat) (int, const char *, int, ...);
#ifdef __GLIBC__
static int   (*real_open64) (const char *, int, ...);
static int   (*real_openat64)(int, const char *, int, ...);
#endif
static int   (*real_creat)  (const char *, mode_t);
#ifdef __GLIBC__
static int   (*real_creat64)(const char *, mode_t);
#endif
static int   (*real_truncate)(const char *, off_t);
#ifdef __GLIBC__
static int   (*real_truncate64)(const char *, off_t);
#endif
static int   (*real_ftruncate)(int, off_t);
#ifdef __GLIBC__
static int   (*real_ftruncate64)(int, off_t);
#endif
static FILE *(*real_fopen)  (const char *, const char *);
#ifdef __GLIBC__
static FILE *(*real_fopen64)(const char *, const char *);
#endif
static FILE *(*real_freopen)(const char *, const char *, FILE *);
static int   (*real_rename)  (const char *, const char *);
static int   (*real_renameat)(int, const char *, int, const char *);
static int   (*real_chmod)   (const char *, mode_t);
static int   (*real_fchmodat)(int, const char *, mode_t, int);
static int   (*real_chown)   (const char *, uid_t, gid_t);
static int   (*real_lchown)  (const char *, uid_t, gid_t);
static int   (*real_fchownat)(int, const char *, uid_t, gid_t, int);
static int   (*real_utime)   (const char *, const struct utimbuf *);
static int   (*real_utimes)  (const char *, const struct timeval [2]);
static int   (*real_utimensat)(int, const char *, const struct timespec [2], int);
static int   (*real_setxattr)   (const char *, const char *, const void *, size_t, int);
static int   (*real_lsetxattr)  (const char *, const char *, const void *, size_t, int);
static int   (*real_removexattr)(const char *, const char *);
static int   (*real_lremovexattr)(const char *, const char *);
static int   (*real_close)   (int);
static int   (*real_dup)     (int);
static int   (*real_dup2)    (int, int);
static int   (*real_dup3)    (int, int, int);
static long  (*real_syscall) (long, ...);

/* ---------- fd → abs-path tracking ----------
 *
 * Overlayfs emulation via fd-based metadata calls (fchmod/fchown/...)
 * needs to know which path an fd refers to. /proc/self/fd can be unreliable
 * under userspace path translators, so we track writable opens ourselves:
 * remember the abs path the caller used; on close, clear it. dup/dup2/dup3
 * copy the entry. Read-only opens can be tracked with
 * PDOCKER_COW_TRACK_READONLY_FDS=1 for strict tests.
 *
 * Table indexed by fd value up to FD_TABLE_MAX. Entries above that are
 * not tracked (fallback path used instead).
 */
#define FD_TABLE_MAX 4096
static char *fd_paths[FD_TABLE_MAX];
static int cow_copy_xattrs = 0;
static int cow_track_readonly_fds = 0;

static void fdtab_set(int fd, const char *abspath) {
    if (fd < 0 || fd >= FD_TABLE_MAX) return;
    free(fd_paths[fd]);
    fd_paths[fd] = abspath ? strdup(abspath) : NULL;
}

static void fdtab_clear(int fd) {
    if (fd < 0 || fd >= FD_TABLE_MAX) return;
    if (!fd_paths[fd]) return;
    free(fd_paths[fd]);
    fd_paths[fd] = NULL;
}

static const char *fdtab_get(int fd) {
    if (fd < 0 || fd >= FD_TABLE_MAX) return NULL;
    return fd_paths[fd];
}

static int env_enabled(const char *name) {
    const char *v = getenv(name);
    return v && v[0] && strcmp(v, "0") != 0 && strcmp(v, "false") != 0;
}

static void remember_open(int fd, const char *path) {
    if (fd < 0 || !path) return;
    if (path[0] == '/') {
        fdtab_set(fd, path);
        return;
    }
    char cwd[PATH_MAX];
    if (!getcwd(cwd, sizeof(cwd))) return;
    size_t l = strlen(cwd);
    if (l + 1 + strlen(path) + 1 >= sizeof(cwd)) return;
    char abs_[PATH_MAX];
    snprintf(abs_, sizeof(abs_), "%s/%s", cwd, path);
    fdtab_set(fd, abs_);
}

static int resolve_at_path(int dirfd, const char *path, char *out, size_t out_len) {
    if (!path || !out || out_len == 0) {
        errno = EINVAL;
        return -1;
    }
    if (path[0] == '/') {
        if (strlen(path) >= out_len) {
            errno = ENAMETOOLONG;
            return -1;
        }
        strcpy(out, path);
        return 0;
    }
    char base[PATH_MAX];
    if (dirfd == AT_FDCWD) {
        if (!getcwd(base, sizeof(base))) return -1;
    } else {
        const char *tracked = fdtab_get(dirfd);
        if (tracked) {
            if (strlen(tracked) >= sizeof(base)) {
                errno = ENAMETOOLONG;
                return -1;
            }
            strcpy(base, tracked);
        } else {
            char linkpath[64];
            snprintf(linkpath, sizeof(linkpath), "/proc/self/fd/%d", dirfd);
            ssize_t n = readlink(linkpath, base, sizeof(base) - 1);
            if (n < 0) return -1;
            base[n] = '\0';
        }
    }
    size_t base_len = strlen(base);
    size_t path_len = strlen(path);
    if (base_len + 1 + path_len + 1 > out_len) {
        errno = ENAMETOOLONG;
        return -1;
    }
    snprintf(out, out_len, "%s/%s", base, path);
    return 0;
}

static int cow_debug = 0;

#define DBG(fmt, ...) do { \
    if (cow_debug) fprintf(stderr, "[libcow] " fmt "\n", ##__VA_ARGS__); \
} while (0)

__attribute__((constructor))
static void libcow_init(void) {
    real_open      = dlsym(RTLD_NEXT, "open");
    real_openat    = dlsym(RTLD_NEXT, "openat");
#ifdef __GLIBC__
    real_open64    = dlsym(RTLD_NEXT, "open64");
    real_openat64  = dlsym(RTLD_NEXT, "openat64");
#endif
    real_creat     = dlsym(RTLD_NEXT, "creat");
#ifdef __GLIBC__
    real_creat64   = dlsym(RTLD_NEXT, "creat64");
#endif
    real_truncate  = dlsym(RTLD_NEXT, "truncate");
#ifdef __GLIBC__
    real_truncate64= dlsym(RTLD_NEXT, "truncate64");
#endif
    real_ftruncate = dlsym(RTLD_NEXT, "ftruncate");
#ifdef __GLIBC__
    real_ftruncate64= dlsym(RTLD_NEXT, "ftruncate64");
#endif
    real_fopen     = dlsym(RTLD_NEXT, "fopen");
#ifdef __GLIBC__
    real_fopen64   = dlsym(RTLD_NEXT, "fopen64");
#endif
    real_freopen   = dlsym(RTLD_NEXT, "freopen");
    real_rename    = dlsym(RTLD_NEXT, "rename");
    real_renameat  = dlsym(RTLD_NEXT, "renameat");
    real_chmod     = dlsym(RTLD_NEXT, "chmod");
    real_fchmodat  = dlsym(RTLD_NEXT, "fchmodat");
    real_chown     = dlsym(RTLD_NEXT, "chown");
    real_lchown    = dlsym(RTLD_NEXT, "lchown");
    real_fchownat  = dlsym(RTLD_NEXT, "fchownat");
    real_utime     = dlsym(RTLD_NEXT, "utime");
    real_utimes    = dlsym(RTLD_NEXT, "utimes");
    real_utimensat = dlsym(RTLD_NEXT, "utimensat");
    real_setxattr    = dlsym(RTLD_NEXT, "setxattr");
    real_lsetxattr   = dlsym(RTLD_NEXT, "lsetxattr");
    real_removexattr = dlsym(RTLD_NEXT, "removexattr");
    real_lremovexattr= dlsym(RTLD_NEXT, "lremovexattr");
    real_close     = dlsym(RTLD_NEXT, "close");
    real_dup       = dlsym(RTLD_NEXT, "dup");
    real_dup2      = dlsym(RTLD_NEXT, "dup2");
    real_dup3      = dlsym(RTLD_NEXT, "dup3");
    real_syscall   = dlsym(RTLD_NEXT, "syscall");

    if (getenv("COW_DEBUG")) cow_debug = 1;
    cow_track_readonly_fds = env_enabled("PDOCKER_COW_TRACK_READONLY_FDS") ||
                             env_enabled("COW_TRACK_READONLY_FDS");
    cow_copy_xattrs = env_enabled("PDOCKER_COW_COPY_XATTRS") ||
                      env_enabled("COW_COPY_XATTRS");
    DBG("initialized");
}

static int flags_write(int flags) {
    int acc = flags & O_ACCMODE;
    if (acc == O_WRONLY || acc == O_RDWR) return 1;
    if (flags & O_TRUNC)  return 1;
    if (flags & O_APPEND) return 1;
    return 0;
}

static int mode_write(const char *mode) {
    /* fopen modes: r, r+, w, w+, a, a+ (plus b/e/m/x) */
    for (const char *p = mode; *p; p++) {
        if (*p == 'w' || *p == 'a' || *p == '+') return 1;
    }
    return 0;
}

static int mode_truncates(const char *mode) {
    if (!mode || !mode[0]) return 0;
    return mode[0] == 'w';
}

static void remember_open_for_flags(int fd, const char *path, int flags) {
    if (fd < 0 || !path) return;
    if (!cow_track_readonly_fds && !flags_write(flags)) return;
    remember_open(fd, path);
}

static int copy_file_fallback(int sfd, int tfd) {
    char buf[65536];
    ssize_t n;
    while ((n = read(sfd, buf, sizeof(buf))) > 0) {
        ssize_t off = 0;
        while (off < n) {
            ssize_t w = write(tfd, buf + off, n - off);
            if (w < 0) {
                if (errno == EINTR) continue;
                return -1;
            }
            off += w;
        }
    }
    return n < 0 ? -1 : 0;
}

static int copy_file_fast(int sfd, int tfd, off_t size) {
#ifdef SYS_copy_file_range
    if (size > 0 && real_syscall) {
        off_t copied = 0;
        while (copied < size) {
            size_t want = (size_t)((size - copied) > (off_t)(1024 * 1024)
                ? (1024 * 1024) : (size - copied));
            long n = real_syscall(SYS_copy_file_range,
                                  sfd, NULL, tfd, NULL, want, 0);
            if (n > 0) {
                copied += (off_t)n;
                continue;
            }
            if (n == 0) return 0;
            if (errno == EINTR) continue;
            if (errno == ENOSYS || errno == EINVAL || errno == EXDEV ||
                errno == EPERM || errno == ENOTSUP) {
                if (lseek(sfd, 0, SEEK_SET) < 0) return -1;
                if (lseek(tfd, 0, SEEK_SET) < 0) return -1;
                if (ftruncate(tfd, 0) < 0) return -1;
                return copy_file_fallback(sfd, tfd);
            }
            return -1;
        }
        return 0;
    }
#endif
    return copy_file_fallback(sfd, tfd);
}

/*
 * Copy src to a temp file in the same dir, preserve mode, then
 * rename over src. Returns 0 on success, -1 on failure (errno set).
 * No-op and returns 0 if file is not a regular file or has nlink<=1.
 */
static int break_hardlink_copy(const char *path, int copy_data) {
    struct stat st;
    if (!path) return 0;
    if (stat(path, &st) < 0) {
        /* file doesn't exist yet — nothing to break */
        return 0;
    }
    if (!S_ISREG(st.st_mode)) return 0;
    if (st.st_nlink <= 1) return 0;

    DBG("break_hardlink %s (nlink=%ld)", path, (long)st.st_nlink);

    /* tmp name in same directory so rename(2) is atomic */
    char tmp[PATH_MAX];
    const char *slash = strrchr(path, '/');
    if (slash) {
        int dlen = (int)(slash - path) + 1;
        if (dlen + 16 >= (int)sizeof(tmp)) { errno = ENAMETOOLONG; return -1; }
        memcpy(tmp, path, dlen);
        memcpy(tmp + dlen, ".cowXXXXXX", 11);
    } else {
        memcpy(tmp, ".cowXXXXXX", 11);
    }

    int tfd = mkstemp(tmp);
    if (tfd < 0) { DBG("mkstemp failed: %s", strerror(errno)); return -1; }
    if (fchmod(tfd, st.st_mode & 07777) < 0) {
        DBG("fchmod warn: %s", strerror(errno));
    }

    if (copy_data) {
        int sfd = real_open(path, O_RDONLY, 0);
        if (sfd < 0) {
            int e = errno;
            close(tfd); unlink(tmp);
            errno = e; return -1;
        }

        if (copy_file_fast(sfd, tfd, st.st_size) < 0) {
            int e = errno;
            close(sfd); close(tfd); unlink(tmp);
            errno = e; return -1;
        }
        close(sfd);
    }
    if (close(tfd) < 0) { int e = errno; unlink(tmp); errno = e; return -1; }

    /* Copy xattrs (SELinux context, POSIX capabilities, user.*, etc.)
     * from src to tmp before renaming. overlayfs does this transparently;
     * if we skip it, file caps and security contexts vanish on copy-up
     * — which breaks setuid-replacement binaries and SELinux-labeled files. */
    ssize_t xl = cow_copy_xattrs ? llistxattr(path, NULL, 0) : 0;
    if (xl > 0) {
        char *names = malloc(xl);
        if (names) {
            xl = llistxattr(path, names, xl);
            for (char *n = names; xl > 0 && n < names + xl; n += strlen(n) + 1) {
                ssize_t vl = lgetxattr(path, n, NULL, 0);
                if (vl <= 0) continue;
                void *val = malloc(vl);
                if (!val) continue;
                vl = lgetxattr(path, n, val, vl);
                if (vl > 0) {
                    if (lsetxattr(tmp, n, val, vl, 0) < 0) {
                        DBG("xattr copy warn %s: %s", n, strerror(errno));
                    }
                }
                free(val);
            }
            free(names);
        }
    }

    if (env_enabled("PDOCKER_COW_FAIL_BEFORE_RENAME")) {
        int e = ENOMEM;
        unlink(tmp);
        errno = e;
        return -1;
    }

    if (real_rename(tmp, path) < 0) {
        int e = errno; unlink(tmp); errno = e; return -1;
    }
    return 0;
}

static int break_hardlink(const char *path) {
    return break_hardlink_copy(path, 1);
}


static int maybe_break(const char *path, int flags) {
    if (!flags_write(flags)) return 0;
    if (flags & O_TMPFILE) return 0;
    if ((flags & O_CREAT) && (flags & O_EXCL)) return 0;
    /* O_CREAT on an existing file still needs copy-up before open/truncate. */
    return break_hardlink_copy(path, !(flags & O_TRUNC));
}

/* ---------- intercepted symbols ---------- */

int open(const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & (O_CREAT | O_TMPFILE)) {
        va_list ap; va_start(ap, flags);
        mode = va_arg(ap, mode_t);
        va_end(ap);
    }
    if (maybe_break(path, flags) < 0) return -1;
    int fd = real_open(path, flags, mode);
    if (fd >= 0) remember_open_for_flags(fd, path, flags);
    return fd;
}

#ifdef __GLIBC__
int open64(const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & (O_CREAT | O_TMPFILE)) {
        va_list ap; va_start(ap, flags);
        mode = va_arg(ap, mode_t);
        va_end(ap);
    }
    if (maybe_break(path, flags) < 0) return -1;
    int fd = real_open64 ? real_open64(path, flags, mode)
                         : real_open  (path, flags, mode);
    if (fd >= 0) remember_open_for_flags(fd, path, flags);
    return fd;
}
#endif

int openat(int dirfd, const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & (O_CREAT | O_TMPFILE)) {
        va_list ap; va_start(ap, flags);
        mode = va_arg(ap, mode_t);
        va_end(ap);
    }
    char resolved[PATH_MAX];
    const char *cow_path = path;
    if (path && path[0] != '/' && dirfd != AT_FDCWD) {
        cow_path = resolve_at_path(dirfd, path, resolved, sizeof(resolved)) == 0
            ? resolved : NULL;
    }
    if (cow_path) {
        if (maybe_break(cow_path, flags) < 0) return -1;
    }
    int fd = real_openat(dirfd, path, flags, mode);
    if (fd >= 0 && cow_path) {
        remember_open_for_flags(fd, cow_path, flags);
    }
    return fd;
}

#ifdef __GLIBC__
int openat64(int dirfd, const char *path, int flags, ...) {
    mode_t mode = 0;
    if (flags & (O_CREAT | O_TMPFILE)) {
        va_list ap; va_start(ap, flags);
        mode = va_arg(ap, mode_t);
        va_end(ap);
    }
    char resolved[PATH_MAX];
    const char *cow_path = path;
    if (path && path[0] != '/' && dirfd != AT_FDCWD) {
        cow_path = resolve_at_path(dirfd, path, resolved, sizeof(resolved)) == 0
            ? resolved : NULL;
    }
    if (cow_path) {
        if (maybe_break(cow_path, flags) < 0) return -1;
    }
    int fd = real_openat64 ? real_openat64(dirfd, path, flags, mode)
                           : real_openat  (dirfd, path, flags, mode);
    if (fd >= 0 && cow_path) {
        remember_open_for_flags(fd, cow_path, flags);
    }
    return fd;
}
#endif

int creat(const char *path, mode_t mode) {
    /* creat = O_WRONLY|O_CREAT|O_TRUNC — but the file may exist and
     * share inodes with the image, so break first. O_TRUNC discards old
     * content, so copy up metadata only. */
    if (break_hardlink_copy(path, 0) < 0) return -1;
    int fd = real_creat(path, mode);
    if (fd >= 0) remember_open(fd, path);
    return fd;
}

#ifdef __GLIBC__
int creat64(const char *path, mode_t mode) {
    if (break_hardlink_copy(path, 0) < 0) return -1;
    int fd = real_creat64 ? real_creat64(path, mode) : real_creat(path, mode);
    if (fd >= 0) remember_open(fd, path);
    return fd;
}
#endif

int truncate(const char *path, off_t length) {
    if (break_hardlink_copy(path, length != 0) < 0) return -1;
    return real_truncate(path, length);
}

#ifdef __GLIBC__
int truncate64(const char *path, off_t length) {
    if (break_hardlink_copy(path, length != 0) < 0) return -1;
    return real_truncate64 ? real_truncate64(path, length)
                           : real_truncate  (path, length);
}
#endif

int ftruncate(int fd, off_t length) {
    const char *path = fdtab_get(fd);
    if (path) {
        if (break_hardlink_copy(path, length != 0) < 0) return -1;
        return real_truncate(path, length);
    }
    return real_ftruncate ? real_ftruncate(fd, length)
                          : real_syscall(SYS_ftruncate, fd, length);
}

#ifdef __GLIBC__
int ftruncate64(int fd, off_t length) {
    const char *path = fdtab_get(fd);
    if (path) {
        if (break_hardlink_copy(path, length != 0) < 0) return -1;
        return real_truncate64 ? real_truncate64(path, length)
                               : real_truncate(path, length);
    }
    return real_ftruncate64 ? real_ftruncate64(fd, length)
                            : ftruncate(fd, length);
}
#endif

FILE *fopen(const char *path, const char *mode) {
    if (mode_write(mode) && break_hardlink_copy(path, !mode_truncates(mode)) < 0) return NULL;
    return real_fopen(path, mode);
}

#ifdef __GLIBC__
FILE *fopen64(const char *path, const char *mode) {
    if (mode_write(mode) && break_hardlink_copy(path, !mode_truncates(mode)) < 0) return NULL;
    return real_fopen64 ? real_fopen64(path, mode) : real_fopen(path, mode);
}
#endif

FILE *freopen(const char *path, const char *mode, FILE *stream) {
    if (path && mode_write(mode) && break_hardlink_copy(path, !mode_truncates(mode)) < 0) return NULL;
    return real_freopen(path, mode, stream);
}

/*
 * rename(2) itself doesn't require copy-up for src (it's being
 * replaced anyway), but if dst exists with nlink>1, the rename will
 * unlink the shared inode — which is fine because unlink decrements
 * the shared inode's nlink without touching content. So no action
 * needed here; kept for future extension.
 */
int rename(const char *src, const char *dst) {
    return real_rename(src, dst);
}

int renameat(int sfd, const char *src, int dfd, const char *dst) {
    return real_renameat(sfd, src, dfd, dst);
}

/* ---------- inode-metadata hooks ----------
 *
 * chmod/chown/utime* modify the inode directly. Because a hardlinked file
 * in a cp -al cloned rootfs shares its inode with the image, these calls
 * would otherwise leak permission/ownership/timestamp changes back to the
 * image layer. Break the hardlink first, then apply the change to the
 * container-local copy.
 *
 * lchown targets symlinks (not regular files) so break_hardlink is a
 * no-op there — which is correct, since symlinks in the clone are
 * re-created, not hardlinked. Same for utimensat/fchownat with
 * AT_SYMLINK_NOFOLLOW.
 */

int chmod(const char *path, mode_t mode) {
    if (break_hardlink(path) < 0) return -1;
    return real_chmod(path, mode);
}

int fchmodat(int dirfd, const char *path, mode_t mode, int flags) {
    char resolved[PATH_MAX];
    const char *cow_path = path;
    if (path && path[0] != '/' && dirfd != AT_FDCWD) {
        cow_path = resolve_at_path(dirfd, path, resolved, sizeof(resolved)) == 0
            ? resolved : NULL;
    }
    if (cow_path) {
        if (break_hardlink(cow_path) < 0) return -1;
    }
    return real_fchmodat(dirfd, path, mode, flags);
}

int chown(const char *path, uid_t uid, gid_t gid) {
    if (break_hardlink(path) < 0) return -1;
    return real_chown(path, uid, gid);
}

int lchown(const char *path, uid_t uid, gid_t gid) {
    /* symlink target — break_hardlink is no-op on non-regular files */
    return real_lchown(path, uid, gid);
}

int fchownat(int dirfd, const char *path, uid_t uid, gid_t gid, int flags) {
    char resolved[PATH_MAX];
    const char *cow_path = path;
    if (path && path[0] != '/' && dirfd != AT_FDCWD) {
        cow_path = resolve_at_path(dirfd, path, resolved, sizeof(resolved)) == 0
            ? resolved : NULL;
    }
    if (cow_path && !(flags & AT_SYMLINK_NOFOLLOW)) {
        if (break_hardlink(cow_path) < 0) return -1;
    }
    return real_fchownat(dirfd, path, uid, gid, flags);
}

int utime(const char *path, const struct utimbuf *times) {
    if (break_hardlink(path) < 0) return -1;
    return real_utime(path, times);
}

int utimes(const char *path, const struct timeval times[2]) {
    if (break_hardlink(path) < 0) return -1;
    return real_utimes(path, times);
}

int utimensat(int dirfd, const char *path,
              const struct timespec times[2], int flags) {
    if (path && (path[0] == '/' || dirfd == AT_FDCWD) &&
        !(flags & AT_SYMLINK_NOFOLLOW)) {
        if (break_hardlink(path) < 0) return -1;
    } else if (path && !(flags & AT_SYMLINK_NOFOLLOW)) {
        char resolved[PATH_MAX];
        if (resolve_at_path(dirfd, path, resolved, sizeof(resolved)) == 0 &&
            break_hardlink(resolved) < 0) {
            return -1;
        }
    }
    return real_utimensat(dirfd, path, times, flags);
}

/* ---------- fd-based metadata (emulated via fd-table path) ----------
 *
 * These are emulated rather than forwarded: we look up fd→path in our
 * own fd table (populated at open()-time, since /proc/self/fd can expose
 * translated or otherwise non-container paths under external userspace
 * runners). We break_hardlink on the remembered path, then apply
 * the change via the path-based syscall. The caller's fd remains bound
 * to the (now-stale) lower inode, but the visible effect on the path
 * matches overlayfs semantics.
 *
 * If the fd isn't tracked (opened before LD_PRELOAD took effect, or
 * fd >= FD_TABLE_MAX) we fall back to the direct fd-based syscall —
 * best-effort, may leak in those edge cases.
 */

int fchmod(int fd, mode_t mode) {
    const char *path = fdtab_get(fd);
    if (path) {
        if (break_hardlink(path) == 0) {
            return real_chmod(path, mode);
        }
        return -1;
    }
    return syscall(SYS_fchmod, fd, mode);
}

int fchown(int fd, uid_t uid, gid_t gid) {
    const char *path = fdtab_get(fd);
    if (path) {
        if (break_hardlink(path) == 0) {
            return real_chown(path, uid, gid);
        }
        return -1;
    }
    return syscall(SYS_fchown, fd, uid, gid);
}

int futimens(int fd, const struct timespec times[2]) {
    const char *path = fdtab_get(fd);
    if (path) {
        if (break_hardlink(path) == 0) {
            return real_utimensat(AT_FDCWD, path, times, 0);
        }
        return -1;
    }
    /* utimensat(fd, NULL, ...) is the "futimens" form in Linux */
    return real_utimensat(fd, NULL, times, 0);
}

/* ---------- fd lifecycle: close / dup / dup2 / dup3 ---------- */

int close(int fd) {
    fdtab_clear(fd);
    return real_close(fd);
}

int dup(int oldfd) {
    int newfd = real_dup(oldfd);
    if (newfd >= 0) {
        const char *p = fdtab_get(oldfd);
        if (p) fdtab_set(newfd, p);
    }
    return newfd;
}

int dup2(int oldfd, int newfd) {
    int ret = real_dup2(oldfd, newfd);
    if (ret >= 0) {
        const char *p = fdtab_get(oldfd);
        fdtab_set(newfd, p);  /* may set NULL if oldfd not tracked */
    }
    return ret;
}

int dup3(int oldfd, int newfd, int flags) {
    int ret = real_dup3(oldfd, newfd, flags);
    if (ret >= 0) {
        const char *p = fdtab_get(oldfd);
        fdtab_set(newfd, p);
    }
    return ret;
}

/* ---------- xattr hooks ---------- */

int setxattr(const char *path, const char *name, const void *value,
             size_t size, int flags) {
    if (break_hardlink(path) < 0) return -1;
    return real_setxattr(path, name, value, size, flags);
}

int lsetxattr(const char *path, const char *name, const void *value,
              size_t size, int flags) {
    /* lsetxattr targets symlink itself; symlinks in the clone are
     * independently created so hardlink break is a no-op. Still call
     * break_hardlink for safety (it checks S_ISREG and returns early). */
    if (break_hardlink(path) < 0) return -1;
    return real_lsetxattr(path, name, value, size, flags);
}

int removexattr(const char *path, const char *name) {
    if (break_hardlink(path) < 0) return -1;
    return real_removexattr(path, name);
}

int lremovexattr(const char *path, const char *name) {
    if (break_hardlink(path) < 0) return -1;
    return real_lremovexattr(path, name);
}

int fsetxattr(int fd, const char *name, const void *value,
              size_t size, int flags) {
    const char *path = fdtab_get(fd);
    if (path) {
        if (break_hardlink(path) == 0) {
            return real_setxattr(path, name, value, size, flags);
        }
        return -1;
    }
    return syscall(SYS_fsetxattr, fd, name, value, size, flags);
}

int fremovexattr(int fd, const char *name) {
    const char *path = fdtab_get(fd);
    if (path) {
        if (break_hardlink(path) == 0) {
            return real_removexattr(path, name);
        }
        return -1;
    }
    return syscall(SYS_fremovexattr, fd, name);
}

/* ---------- syscall() catch-all ----------
 *
 * Some tools bypass libc wrappers and call `syscall(SYS_chmod, ...)` or
 * equivalent directly. Hooking the libc-exposed `syscall(3)` function
 * intercepts those and routes metadata ops through break_hardlink.
 * Unknown/unrelated syscall numbers pass through untouched.
 *
 * This does not hook kernel-level syscall stops; it only covers the common
 * libc entrypoint case of "I want to bypass the wrapper".
 */
long syscall(long number, ...) {
    va_list ap;
    va_start(ap, number);

    /* Extract up to 6 args (max syscall arity on aarch64) */
    long a0 = va_arg(ap, long);
    long a1 = va_arg(ap, long);
    long a2 = va_arg(ap, long);
    long a3 = va_arg(ap, long);
    long a4 = va_arg(ap, long);
    long a5 = va_arg(ap, long);
    va_end(ap);

    int cow_rc = 0;
    switch (number) {
#ifdef SYS_chmod
    case SYS_chmod:
        cow_rc = break_hardlink((const char *)a0);
        break;
#endif
#ifdef SYS_fchmodat
    case SYS_fchmodat: {
        const char *p = (const char *)a1;
        if (p && (p[0] == '/' || (int)a0 == AT_FDCWD)) cow_rc = break_hardlink(p);
        break;
    }
#endif
#ifdef SYS_chown
    case SYS_chown:
        cow_rc = break_hardlink((const char *)a0);
        break;
#endif
#ifdef SYS_fchownat
    case SYS_fchownat: {
        const char *p = (const char *)a1;
        if (p && (p[0] == '/' || (int)a0 == AT_FDCWD) &&
            !((int)a4 & AT_SYMLINK_NOFOLLOW)) cow_rc = break_hardlink(p);
        break;
    }
#endif
#ifdef SYS_truncate
    case SYS_truncate:
        cow_rc = break_hardlink_copy((const char *)a0, a1 != 0);
        break;
#endif
#ifdef SYS_ftruncate
    case SYS_ftruncate: {
        const char *p = fdtab_get((int)a0);
        if (p) cow_rc = break_hardlink_copy(p, a1 != 0);
        break;
    }
#endif
#ifdef SYS_utimensat
    case SYS_utimensat: {
        const char *p = (const char *)a1;
        if (p && (p[0] == '/' || (int)a0 == AT_FDCWD) &&
            !((int)a3 & AT_SYMLINK_NOFOLLOW)) cow_rc = break_hardlink(p);
        break;
    }
#endif
#ifdef SYS_setxattr
    case SYS_setxattr:
        cow_rc = break_hardlink((const char *)a0);
        break;
#endif
#ifdef SYS_removexattr
    case SYS_removexattr:
        cow_rc = break_hardlink((const char *)a0);
        break;
#endif
#ifdef SYS_fchmod
    case SYS_fchmod:
    case SYS_fchown:
    case SYS_fsetxattr:
    case SYS_fremovexattr: {
        const char *p = fdtab_get((int)a0);
        if (p) cow_rc = break_hardlink(p);
        break;
    }
#endif
    default:
        break;
    }
    if (cow_rc < 0) return -1;
    return real_syscall(number, a0, a1, a2, a3, a4, a5);
}
