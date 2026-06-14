#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>
#include <unistd.h>
#include <limits.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <errno.h>
#include <signal.h>
#include <sys/ptrace.h>
#include <sys/uio.h>
#include <sys/wait.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/mman.h>
#include <sys/statvfs.h>
#include <dirent.h>
#include <linux/elf.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <fcntl.h>
#include <time.h>
#include <sys/prctl.h>
#include <termios.h>

extern char **environ;

#ifndef NT_ARM_SYSTEM_CALL
#define NT_ARM_SYSTEM_CALL 0x404
#endif

static int g_trace_verbose = 0;
static int g_trace_linkat = 0;
static int g_trace_paths = 0;
static int g_trace_exec = 0;
static int g_trace_memory = 0;
static int g_trace_memory_verbose = 0;
static unsigned long long g_trace_memory_threshold = 64ULL * 1024ULL * 1024ULL;
static int g_memory_guard = 0;
static int g_managed_memory_pager = 0;
static unsigned long long g_memory_guard_min_request = 64ULL * 1024ULL * 1024ULL;
static unsigned long long g_memory_guard_min_available = 512ULL * 1024ULL * 1024ULL;
static unsigned long long g_memory_guard_min_swap = 256ULL * 1024ULL * 1024ULL;
static unsigned long long g_managed_memory_pager_min_request = 128ULL * 1024ULL * 1024ULL;
static unsigned long long g_managed_memory_pager_max_region = 1024ULL * 1024ULL * 1024ULL;
static unsigned long long g_managed_memory_pager_resident_pages = 256ULL;
static int g_memory_stats_printed = 0;
static char g_memory_telemetry_path[PATH_MAX];
static char g_memory_summary_path[PATH_MAX];
static char g_memory_operation_id[128];
static char g_memory_container_id[128];
static unsigned long long g_memory_telemetry_max_bytes = 1048576ULL;
static unsigned long long g_memory_telemetry_max_lines = 240ULL;
static unsigned long long g_memory_telemetry_max_line_bytes = 16384ULL;
static unsigned long long g_memory_telemetry_seq = 0;
static unsigned long long g_memory_telemetry_started_unix_ms = 0;
static int g_memory_telemetry_failed = 0;
static int g_memory_telemetry_truncated = 0;
static int g_sync_usec = 0;
static int g_stats = 0;
static int g_stats_top = 12;
static int g_path_profile = 0;
static int g_path_cache_enabled = 1;
static int g_path_cache_store_disabled = 0;
static int g_path_cache_mutation_inflight = 0;
static int g_selective_trace = 0;
static int g_rootfd_rewrite = 0;
static int g_validate_tracees = 0;
static int g_trace_stat_paths = 1;
static int g_rootfs_fd = -1;
static volatile sig_atomic_t g_trace_child_pgid = -1;
static const char *g_managed_pager_init_stage = "none";
static char g_managed_pager_backing_dir[PATH_MAX];
static char g_managed_pager_backing_path[PATH_MAX];
static char g_managed_pager_backing_op[32];
static int g_managed_pager_backing_errno = 0;
static unsigned long long g_syscall_counts[512];
static unsigned long long g_stop_count = 0;
static struct timespec g_stats_start;

typedef struct TraceeState TraceeState;

typedef struct {
    unsigned long long calls;
    unsigned long long empty_path;
    unsigned long long relative_path;
    unsigned long long absolute_path;
    unsigned long long no_rewrite;
    unsigned long long rewrote;
    unsigned long long rootfd_rewrite;
    unsigned long long denied;
    unsigned long long read_ns;
    unsigned long long relative_validate_ns;
    unsigned long long resolve_ns;
    unsigned long long validate_ns;
    unsigned long long write_ns;
    unsigned long long total_ns;
    unsigned long long validate_calls;
    unsigned long long validate_lexical_ns;
    unsigned long long validate_realpath_full_ns;
    unsigned long long validate_parent_realpath_ns;
    unsigned long long validate_parent_loops;
    unsigned long long validation_cache_hits;
    unsigned long long validation_cache_misses;
    unsigned long long realpath_cache_hits;
    unsigned long long realpath_cache_misses;
    unsigned long long cache_invalidations;
} PathMicroStats;

typedef struct {
    unsigned long long calls;
    unsigned long long failures;
    unsigned long long bytes;
    unsigned long long large_calls;
    unsigned long long largest;
} MemorySyscallStats;

typedef struct {
    unsigned long long denied;
    unsigned long long last_denied_bytes;
    unsigned long long last_available;
    unsigned long long last_swap_free;
    long last_denied_nr;
    int last_denied_errno;
    char last_denied_syscall[32];
    MemorySyscallStats mmap_;
    MemorySyscallStats mremap;
    MemorySyscallStats brk;
    MemorySyscallStats munmap_;
    MemorySyscallStats mprotect_;
    MemorySyscallStats madvise_;
} MemoryTraceStats;

typedef struct {
    unsigned long long considered;
    unsigned long long pending;
    unsigned long long accepted;
    unsigned long long denied_enomem;
    unsigned long long cleanup_munmap_failed;
    unsigned long long rejected_not_enabled;
    unsigned long long rejected_below_threshold;
    unsigned long long rejected_too_large;
    unsigned long long rejected_fixed_address;
    unsigned long long rejected_flags;
    unsigned long long rejected_file_backed;
    unsigned long long rejected_protection;
    unsigned long long register_failed;
    unsigned long long last_request_bytes;
    unsigned long long last_threshold_bytes;
    unsigned long long last_max_region_bytes;
    unsigned long long last_errno;
    char last_decision[48];
    char last_reason[96];
    char last_classification[64];
} ManagedPagerAdmissionStats;

static MemoryTraceStats g_memory_stats;
static ManagedPagerAdmissionStats g_managed_pager_admission;
static PathMicroStats g_path_stats;
static int managed_pager_mkdir_p(const char *dir);
static int read_meminfo_bytes(unsigned long long *available, unsigned long long *swap_free);
static int write_tracee_data(pid_t pid, unsigned long long addr, const void *value, size_t len);
static const char *syscall_name(long nr);
static int get_regs(pid_t pid, struct user_pt_regs *regs);
static int set_regs(pid_t pid, struct user_pt_regs *regs);
static int read_tracee_u32(pid_t pid, unsigned long long addr, uint32_t *out);
static int write_tracee_u32(pid_t pid, unsigned long long addr, unsigned long long value);
static ssize_t pdocker_process_vm_writev(pid_t pid,
                                         const struct iovec *local_iov,
                                         unsigned long liovcnt,
                                         const struct iovec *remote_iov,
                                         unsigned long riovcnt,
                                         unsigned long flags);

#define MAX_BIND_MAPS 96
#define EXEC_REWRITE_MAX_ARGC 511
#define EXEC_REWRITE_STACK_SAFETY 16384ULL
#define EXEC_REWRITE_MAX_SCRATCH (2ULL * 1024ULL * 1024ULL)
#define EXEC_REWRITE_MAX_ARG_BYTES (EXEC_REWRITE_MAX_SCRATCH - EXEC_REWRITE_STACK_SAFETY)
#define REWRITE_SYSCALL_COMPLETED 2
#define PATH_VALIDATION_CACHE_SIZE 512
#define PATH_REALPATH_CACHE_SIZE 256

typedef struct {
    char host[PATH_MAX];
    char guest[PATH_MAX];
    int readonly;
} BindMap;

typedef struct {
    unsigned long long generation;
    int follow_final;
    int rc;
    char host[PATH_MAX];
} PathValidationCacheEntry;

typedef struct {
    unsigned long long generation;
    char path[PATH_MAX];
    char resolved[PATH_MAX];
} PathRealpathCacheEntry;

static BindMap g_bind_maps[MAX_BIND_MAPS];
static int g_bind_map_count = 0;
static unsigned long long g_path_cache_generation = 1;
static PathValidationCacheEntry g_path_validation_cache[PATH_VALIDATION_CACHE_SIZE];
static PathRealpathCacheEntry g_path_realpath_cache[PATH_REALPATH_CACHE_SIZE];

#define TRACE_LOG(...) do { if (g_trace_verbose) fprintf(stderr, __VA_ARGS__); } while (0)

static void tracer_signal_handler(int sig) {
    pid_t pgid = (pid_t)g_trace_child_pgid;
    if (pgid > 0) {
        kill(-pgid, SIGKILL);
    }
    signal(sig, SIG_DFL);
    raise(sig);
}

static void install_tracer_signal_handlers(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = tracer_signal_handler;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);
    sigaction(SIGQUIT, &sa, NULL);
}

static int env_flag_enabled(const char *name) {
    const char *v = getenv(name);
    return v && v[0] && strcmp(v, "0") != 0 && strcasecmp(v, "false") != 0;
}

static unsigned long long env_u64_or_default(const char *name, unsigned long long fallback) {
    const char *v = getenv(name);
    if (!v || !v[0]) return fallback;
    char *end = NULL;
    errno = 0;
    unsigned long long parsed = strtoull(v, &end, 0);
    if (errno != 0 || end == v) return fallback;
    if (end && (*end == 'k' || *end == 'K')) parsed *= 1024ULL;
    else if (end && (*end == 'm' || *end == 'M')) parsed *= 1024ULL * 1024ULL;
    else if (end && (*end == 'g' || *end == 'G')) parsed *= 1024ULL * 1024ULL * 1024ULL;
    return parsed;
}

static unsigned long long wall_now_ms(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_REALTIME, &ts) != 0) return 0;
    return (unsigned long long)ts.tv_sec * 1000ULL +
           (unsigned long long)ts.tv_nsec / 1000000ULL;
}

static void json_write_string(FILE *fp, const char *value) {
    fputc('"', fp);
    const unsigned char *p = (const unsigned char *)(value ? value : "");
    for (; *p; ++p) {
        switch (*p) {
            case '\\': fputs("\\\\", fp); break;
            case '"': fputs("\\\"", fp); break;
            case '\n': fputs("\\n", fp); break;
            case '\r': fputs("\\r", fp); break;
            case '\t': fputs("\\t", fp); break;
            default:
                if (*p < 0x20) fprintf(fp, "\\u%04x", (unsigned)*p);
                else fputc(*p, fp);
                break;
        }
    }
    fputc('"', fp);
}

static int mkdir_parent_for_path(const char *path) {
    if (!path || !path[0]) return -1;
    char dir[PATH_MAX];
    if (snprintf(dir, sizeof(dir), "%s", path) >= (int)sizeof(dir)) {
        errno = ENAMETOOLONG;
        return -1;
    }
    char *slash = strrchr(dir, '/');
    if (!slash) return 0;
    if (slash == dir) return 0;
    *slash = '\0';
    return managed_pager_mkdir_p(dir);
}

static void fsync_parent_dir_for_path(const char *path) {
    if (!path || !path[0]) return;
    char dir[PATH_MAX];
    if (snprintf(dir, sizeof(dir), "%s", path) >= (int)sizeof(dir)) return;
    char *slash = strrchr(dir, '/');
    if (!slash) {
        snprintf(dir, sizeof(dir), ".");
    } else if (slash == dir) {
        slash[1] = '\0';
    } else {
        *slash = '\0';
    }
    int fd = open(dir, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd < 0) return;
    (void)fsync(fd);
    close(fd);
}

static unsigned long long path_storage_free_bytes(const char *path) {
    char dir[PATH_MAX];
    if (!path || !path[0]) return 0;
    snprintf(dir, sizeof(dir), "%s", path);
    char *slash = strrchr(dir, '/');
    if (slash && slash != dir) *slash = '\0';
    struct statvfs st;
    if (statvfs(dir, &st) != 0) return 0;
    return (unsigned long long)st.f_bavail * (unsigned long long)st.f_frsize;
}

static unsigned long long self_rss_bytes(void) {
    FILE *fp = fopen("/proc/self/statm", "re");
    if (!fp) return 0;
    unsigned long long size_pages = 0, resident_pages = 0;
    int ok = fscanf(fp, "%llu %llu", &size_pages, &resident_pages);
    fclose(fp);
    long ps = sysconf(_SC_PAGESIZE);
    if (ok != 2 || ps <= 0) return 0;
    return resident_pages * (unsigned long long)ps;
}

static int read_oom_score_adj(void) {
    FILE *fp = fopen("/proc/self/oom_score_adj", "re");
    if (!fp) return 0;
    int value = 0;
    (void)fscanf(fp, "%d", &value);
    fclose(fp);
    return value;
}

static int pager_probe_ok(const char *name, int ok, int err) {
    if (ok) {
        printf("pager-probe:%s=ok\n", name);
        return 0;
    }
    printf("pager-probe:%s=fail errno=%d\n", name, err);
    return 1;
}

static int pager_poc_ok(const char *name, int ok, int err) {
    if (ok) {
        printf("pager-poc:%s=ok\n", name);
        return 0;
    }
    printf("pager-poc:%s=fail errno=%d\n", name, err);
    return 1;
}

static int inject_tracee_syscall(pid_t pid, struct user_pt_regs *fault_regs,
                                 long nr,
                                 unsigned long long arg0,
                                 unsigned long long arg1,
                                 unsigned long long arg2,
                                 unsigned long long *result) {
    const uint32_t insn_svc0 = 0xd4000001U;
    const uint32_t insn_brk0 = 0xd4200000U;
    unsigned long long pc = fault_regs->pc;
    uint32_t saved0 = 0;
    uint32_t saved1 = 0;
    if (read_tracee_u32(pid, pc, &saved0) != 0) return -1;
    if (read_tracee_u32(pid, pc + 4, &saved1) != 0) return -1;
    if (write_tracee_u32(pid, pc, insn_svc0) != 0) return -1;
    if (write_tracee_u32(pid, pc + 4, insn_brk0) != 0) {
        write_tracee_u32(pid, pc, saved0);
        return -1;
    }

    struct user_pt_regs regs = *fault_regs;
    regs.regs[0] = arg0;
    regs.regs[1] = arg1;
    regs.regs[2] = arg2;
    regs.regs[8] = (unsigned long long)nr;
    if (set_regs(pid, &regs) != 0) {
        write_tracee_u32(pid, pc, saved0);
        write_tracee_u32(pid, pc + 4, saved1);
        return -1;
    }

    if (ptrace(PTRACE_CONT, pid, NULL, NULL) != 0) {
        write_tracee_u32(pid, pc, saved0);
        write_tracee_u32(pid, pc + 4, saved1);
        return -1;
    }
    int status = 0;
    int waited = waitpid(pid, &status, 0);
    int ok = waited == pid && WIFSTOPPED(status) && WSTOPSIG(status) == SIGTRAP;
    struct user_pt_regs after;
    memset(&after, 0, sizeof(after));
    if (ok && get_regs(pid, &after) == 0 && result) {
        *result = after.regs[0];
    } else {
        ok = 0;
    }

    int restore0 = write_tracee_u32(pid, pc, saved0);
    int restore1 = write_tracee_u32(pid, pc + 4, saved1);
    return ok && restore0 == 0 && restore1 == 0 ? 0 : -1;
}

static int run_memory_pager_poc(void) {
    int failures = 0;
    long page_size = sysconf(_SC_PAGESIZE);
    if (page_size <= 0) page_size = 4096;

    int pipefd[2];
    if (pipe(pipefd) != 0) {
        failures += pager_poc_ok("pipe", 0, errno);
        return 1;
    }

    pid_t child = fork();
    if (child == 0) {
        close(pipefd[0]);
        volatile char *fault_page = mmap(NULL, (size_t)page_size, PROT_NONE,
                                         MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (fault_page == MAP_FAILED) _exit(80);
        dprintf(pipefd[1], "%p\n", (void *)fault_page);
        close(pipefd[1]);
        if (ptrace(PTRACE_TRACEME, 0, NULL, NULL) != 0) _exit(81);
        raise(SIGSTOP);
        fault_page[0] = 0x37;
        _exit(fault_page[0] == 0x37 ? 0 : 82);
    }

    close(pipefd[1]);
    unsigned long long fault_addr = 0;
    FILE *pipe_read = fdopen(pipefd[0], "r");
    if (pipe_read) {
        if (fscanf(pipe_read, "%llx", &fault_addr) != 1) fault_addr = 0;
        fclose(pipe_read);
    } else {
        close(pipefd[0]);
    }
    failures += pager_poc_ok("child_fault_page_reported", fault_addr != 0, EINVAL);

    int status = 0;
    int waited = waitpid(child, &status, 0);
    failures += pager_poc_ok("initial_ptrace_stop",
                             waited == child && WIFSTOPPED(status) && WSTOPSIG(status) == SIGSTOP,
                             waited < 0 ? errno : EINVAL);
    if (failures) {
        kill(child, SIGKILL);
        waitpid(child, NULL, 0);
        printf("pager-poc:result=fail\n");
        return 1;
    }

    ptrace(PTRACE_CONT, child, NULL, NULL);
    waited = waitpid(child, &status, 0);
    failures += pager_poc_ok("fault_sigsegv_stop",
                             waited == child && WIFSTOPPED(status) && WSTOPSIG(status) == SIGSEGV,
                             waited < 0 ? errno : EINVAL);

    siginfo_t info;
    memset(&info, 0, sizeof(info));
    if (!failures) {
        int rc = ptrace(PTRACE_GETSIGINFO, child, NULL, &info);
        failures += pager_poc_ok("fault_siginfo",
                                 rc == 0 && (uintptr_t)info.si_addr == (uintptr_t)fault_addr,
                                 errno);
    }

    struct user_pt_regs fault_regs;
    memset(&fault_regs, 0, sizeof(fault_regs));
    if (!failures) {
        failures += pager_poc_ok("get_fault_regs", get_regs(child, &fault_regs) == 0, errno);
    }

    unsigned long long page_base = fault_addr & ~((unsigned long long)page_size - 1ULL);
    if (!failures) {
        unsigned long long result = 0;
        int rc = inject_tracee_syscall(child, &fault_regs, __NR_mprotect,
                                       page_base, (unsigned long long)page_size,
                                       PROT_READ | PROT_WRITE, &result);
        failures += pager_poc_ok("inject_mprotect_syscall", rc == 0 && result == 0,
                                 rc == 0 ? (int)(-result) : errno);
    }

    if (!failures) {
        unsigned char seed = 0x23;
        struct iovec local = {.iov_base = &seed, .iov_len = sizeof(seed)};
        struct iovec remote = {.iov_base = (void *)(uintptr_t)fault_addr, .iov_len = sizeof(seed)};
        ssize_t written = pdocker_process_vm_writev(child, &local, 1, &remote, 1, 0);
        failures += pager_poc_ok("write_backed_page",
                                 written == (ssize_t)sizeof(seed), errno);
    }

    if (!failures) {
        failures += pager_poc_ok("restore_fault_regs", set_regs(child, &fault_regs) == 0, errno);
    }

    if (!failures) {
        ptrace(PTRACE_CONT, child, NULL, NULL);
        waited = waitpid(child, &status, 0);
        failures += pager_poc_ok("resumed_fault_instruction",
                                 waited == child && WIFEXITED(status) && WEXITSTATUS(status) == 0,
                                 waited < 0 ? errno : EINVAL);
    }

    if (failures) {
        kill(child, SIGKILL);
        waitpid(child, NULL, 0);
    }
    printf("pager-poc:result=%s\n", failures ? "fail" : "ok");
    return failures ? 1 : 0;
}

typedef struct {
    unsigned char *base;
    size_t page_size;
    size_t page_count;
    size_t resident_limit;
    size_t resident_count;
    size_t max_resident_count;
    size_t clock_hand;
    int backing_fd;
    unsigned char *resident;
    unsigned char *dirty;
    unsigned char *writable;
    unsigned long long page_ins;
    unsigned long long page_outs;
    unsigned long long dirty_page_outs;
    unsigned long long bytes_in;
    unsigned long long bytes_out;
} ManagedPagerPoc;

static unsigned long long monotonic_now_ns(void);
static void managed_pager_destroy(ManagedPagerPoc *pager);

static void managed_pager_record_backing_attempt(const char *dir, const char *path,
                                                 const char *op, int err) {
    snprintf(g_managed_pager_backing_dir, sizeof(g_managed_pager_backing_dir),
             "%s", dir ? dir : "");
    snprintf(g_managed_pager_backing_path, sizeof(g_managed_pager_backing_path),
             "%s", path ? path : "");
    snprintf(g_managed_pager_backing_op, sizeof(g_managed_pager_backing_op),
             "%s", op ? op : "");
    g_managed_pager_backing_errno = err;
}

static int managed_pager_mkdir_p(const char *dir) {
    if (!dir || !dir[0]) {
        errno = EINVAL;
        return -1;
    }
    char path[PATH_MAX];
    if (snprintf(path, sizeof(path), "%s", dir) >= (int)sizeof(path)) {
        errno = ENAMETOOLONG;
        return -1;
    }
    size_t len = strlen(path);
    while (len > 1 && path[len - 1] == '/') path[--len] = '\0';
    for (char *p = path + 1; *p; ++p) {
        if (*p != '/') continue;
        *p = '\0';
        if (mkdir(path, 0700) != 0 && errno != EEXIST) {
            *p = '/';
            return -1;
        }
        *p = '/';
    }
    if (mkdir(path, 0700) != 0 && errno != EEXIST) return -1;
    return 0;
}

static int managed_pager_should_create_dir(const char *dir) {
    if (!dir || !dir[0]) return 0;
    if (dir[0] == '/') return strstr(dir, "/pdocker/") != NULL;
    return strncmp(dir, "files", 5) == 0 || strncmp(dir, "cache", 5) == 0;
}

static int managed_pager_open_backing_file(void) {
    const char *tmpdir = getenv("TMPDIR");
    char cwd_tmp[PATH_MAX];
    cwd_tmp[0] = '\0';
    char cwd[PATH_MAX];
    if (getcwd(cwd, sizeof(cwd)) && cwd[0]) {
        snprintf(cwd_tmp, sizeof(cwd_tmp), "%s/files/pdocker/tmp", cwd);
    }
    const char *candidates[] = {
        tmpdir && tmpdir[0] ? tmpdir : "",
        cwd_tmp[0] ? cwd_tmp : "",
        "files/pdocker/tmp",
        "files/tmp",
        "files",
        "cache",
        ".",
        "/data/local/tmp",
        "/tmp",
        NULL,
    };
    managed_pager_record_backing_attempt("", "", "start", 0);
    for (size_t i = 0; candidates[i]; ++i) {
        const char *dir = candidates[i];
        if (!dir[0]) continue;
        if (managed_pager_should_create_dir(dir) && managed_pager_mkdir_p(dir) != 0) {
            managed_pager_record_backing_attempt(dir, "", "mkdir", errno);
            continue;
        }
        for (unsigned attempt = 0; attempt < 64; ++attempt) {
            char tmpl[PATH_MAX];
            int n = snprintf(tmpl, sizeof(tmpl),
                             "%s/pdocker-managed-pager-%ld-%llu-%u.tmp",
                             dir, (long)getpid(), monotonic_now_ns(), attempt);
            if (n <= 0 || (size_t)n >= sizeof(tmpl)) {
                managed_pager_record_backing_attempt(dir, "", "format", ENAMETOOLONG);
                break;
            }
            int fd = open(tmpl, O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
            if (fd >= 0) {
                unlink(tmpl);
                managed_pager_record_backing_attempt(dir, tmpl, "open", 0);
                return fd;
            }
            int saved = errno;
            managed_pager_record_backing_attempt(dir, tmpl, "open", saved);
            if (saved != EEXIST) break;
        }
    }
    errno = g_managed_pager_backing_errno ? g_managed_pager_backing_errno : ENOENT;
    return -1;
}

static int managed_pager_init(ManagedPagerPoc *pager, size_t page_count, size_t resident_limit) {
    g_managed_pager_init_stage = "start";
    memset(pager, 0, sizeof(*pager));
    pager->backing_fd = -1;
    if (page_count == 0) {
        errno = EINVAL;
        g_managed_pager_init_stage = "page-count";
        return -1;
    }
    long ps = sysconf(_SC_PAGESIZE);
    pager->page_size = ps > 0 ? (size_t)ps : 4096u;
    pager->page_count = page_count;
    pager->resident_limit = resident_limit ? resident_limit : 1u;
    if (pager->resident_limit > pager->page_count) pager->resident_limit = pager->page_count;
    if (pager->page_count > SIZE_MAX / pager->page_size) {
        errno = EOVERFLOW;
        g_managed_pager_init_stage = "overflow";
        return -1;
    }
    size_t total = pager->page_size * pager->page_count;
    g_managed_pager_init_stage = "calloc-resident";
    pager->resident = (unsigned char *)calloc(pager->page_count, 1);
    g_managed_pager_init_stage = "calloc-dirty";
    pager->dirty = (unsigned char *)calloc(pager->page_count, 1);
    g_managed_pager_init_stage = "calloc-writable";
    pager->writable = (unsigned char *)calloc(pager->page_count, 1);
    if (!pager->resident || !pager->dirty || !pager->writable) goto fail;
    g_managed_pager_init_stage = "open-backing";
    pager->backing_fd = managed_pager_open_backing_file();
    if (pager->backing_fd < 0) goto fail;
    g_managed_pager_init_stage = "ftruncate";
    if (ftruncate(pager->backing_fd, (off_t)total) != 0) {
        managed_pager_record_backing_attempt(g_managed_pager_backing_dir,
                                             g_managed_pager_backing_path,
                                             "ftruncate", errno);
        goto fail;
    }
    g_managed_pager_init_stage = "mmap";
    void *addr = mmap(NULL, total, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (addr == MAP_FAILED) goto fail;
    pager->base = (unsigned char *)addr;
    g_managed_pager_init_stage = "ok";
    return 0;

fail:
    {
        int saved_errno = errno ? errno : ENOMEM;
        managed_pager_destroy(pager);
        errno = saved_errno;
    }
    return -1;
}

static void managed_pager_destroy(ManagedPagerPoc *pager) {
    if (!pager) return;
    if (pager->base && pager->base != MAP_FAILED) {
        munmap(pager->base, pager->page_size * pager->page_count);
    }
    if (pager->backing_fd >= 0) close(pager->backing_fd);
    free(pager->resident);
    free(pager->dirty);
    free(pager->writable);
    memset(pager, 0, sizeof(*pager));
    pager->backing_fd = -1;
}

static int managed_pager_evict_one(ManagedPagerPoc *pager, size_t avoid_index) {
    if (!pager || pager->resident_count == 0) return -1;
    for (size_t scanned = 0; scanned < pager->page_count * 2u; ++scanned) {
        size_t idx = pager->clock_hand++ % pager->page_count;
        if (idx == avoid_index || !pager->resident[idx]) continue;
        void *addr = pager->base + idx * pager->page_size;
        if (pager->dirty[idx]) {
            ssize_t written = pwrite(pager->backing_fd, addr, pager->page_size,
                                     (off_t)(idx * pager->page_size));
            if (written != (ssize_t)pager->page_size) return -1;
            pager->dirty[idx] = 0;
            pager->dirty_page_outs++;
            pager->bytes_out += (unsigned long long)pager->page_size;
        }
        if (mprotect(addr, pager->page_size, PROT_NONE) != 0) return -1;
        pager->resident[idx] = 0;
        pager->writable[idx] = 0;
        pager->resident_count--;
        pager->page_outs++;
        return 0;
    }
    return -1;
}

static void *managed_pager_get_page(ManagedPagerPoc *pager, size_t index, int writeable) {
    if (!pager || index >= pager->page_count) {
        errno = EINVAL;
        return NULL;
    }
    if (!pager->resident[index]) {
        while (pager->resident_count >= pager->resident_limit) {
            if (managed_pager_evict_one(pager, index) != 0) return NULL;
        }
        void *addr = pager->base + index * pager->page_size;
        if (mprotect(addr, pager->page_size, PROT_READ | PROT_WRITE) != 0) return NULL;
        ssize_t read_bytes = pread(pager->backing_fd, addr, pager->page_size,
                                   (off_t)(index * pager->page_size));
        if (read_bytes < 0) return NULL;
        if (read_bytes < (ssize_t)pager->page_size) {
            memset((unsigned char *)addr + read_bytes, 0, pager->page_size - (size_t)read_bytes);
        }
        pager->resident[index] = 1;
        pager->resident_count++;
        if (pager->resident_count > pager->max_resident_count) {
            pager->max_resident_count = pager->resident_count;
        }
        pager->writable[index] = 1;
        pager->page_ins++;
        pager->bytes_in += (unsigned long long)pager->page_size;
    } else if (writeable && !pager->writable[index]) {
        void *addr = pager->base + index * pager->page_size;
        if (mprotect(addr, pager->page_size, PROT_READ | PROT_WRITE) != 0) return NULL;
        pager->writable[index] = 1;
    }
    if (!writeable && pager->writable[index]) {
        void *addr = pager->base + index * pager->page_size;
        if (mprotect(addr, pager->page_size, PROT_READ) != 0) return NULL;
        pager->writable[index] = 0;
    }
    if (writeable) pager->dirty[index] = 1;
    return pager->base + index * pager->page_size;
}

static int run_memory_pager_managed_poc(void) {
    ManagedPagerPoc pager;
    int failures = 0;
    size_t pages = (size_t)env_u64_or_default("PDOCKER_MEMORY_PAGER_POC_PAGES", 32ULL);
    size_t resident_limit = (size_t)env_u64_or_default("PDOCKER_MEMORY_PAGER_POC_RESIDENT_PAGES", 4ULL);
    if (pages == 0) pages = 1;
    if (resident_limit == 0) resident_limit = 1;
    if (resident_limit > pages) resident_limit = pages;
    unsigned long long start_ns = monotonic_now_ns();
    if (managed_pager_init(&pager, pages, resident_limit) != 0) {
        printf("pager-managed-poc:init=fail errno=%d\n", errno);
        printf("pager-managed-poc:init_stage=%s\n", g_managed_pager_init_stage);
        printf("pager-managed-poc:backing_errno=%d\n", g_managed_pager_backing_errno);
        printf("pager-managed-poc:backing_op=%s\n", g_managed_pager_backing_op);
        printf("pager-managed-poc:backing_dir=%s\n", g_managed_pager_backing_dir);
        printf("pager-managed-poc:backing_path=%s\n", g_managed_pager_backing_path);
        return 1;
    }
    printf("pager-managed-poc:reserve_bytes=%llu\n",
           (unsigned long long)(pager.page_size * pager.page_count));
    printf("pager-managed-poc:resident_limit_pages=%llu\n",
           (unsigned long long)pager.resident_limit);
    for (size_t i = 0; i < pages; ++i) {
        unsigned char *page = (unsigned char *)managed_pager_get_page(&pager, i, 1);
        if (!page) {
            failures++;
            break;
        }
        page[0] = (unsigned char)(0x40u + (i & 0x3fu));
        page[pager.page_size - 1u] = (unsigned char)(0x80u + (i & 0x3fu));
        if (pager.resident_count > pager.resident_limit) failures++;
    }
    for (size_t round = 0; round < 3 && !failures; ++round) {
        for (size_t i = pages; i > 0; --i) {
            size_t idx = i - 1u;
            unsigned char *page = (unsigned char *)managed_pager_get_page(&pager, idx, 0);
            if (!page) {
                failures++;
                break;
            }
            if (page[0] != (unsigned char)(0x40u + (idx & 0x3fu)) ||
                page[pager.page_size - 1u] != (unsigned char)(0x80u + (idx & 0x3fu))) {
                failures++;
                break;
            }
            if (pager.resident_count > pager.resident_limit) failures++;
        }
    }
    printf("pager-managed-poc:resident_pages=%llu\n",
           (unsigned long long)pager.resident_count);
    printf("pager-managed-poc:max_resident_pages=%llu\n",
           (unsigned long long)pager.max_resident_count);
    printf("pager-managed-poc:page_ins=%llu\n", pager.page_ins);
    printf("pager-managed-poc:page_outs=%llu\n", pager.page_outs);
    printf("pager-managed-poc:dirty_page_outs=%llu\n", pager.dirty_page_outs);
    printf("pager-managed-poc:bytes_in=%llu\n", pager.bytes_in);
    printf("pager-managed-poc:bytes_out=%llu\n", pager.bytes_out);
    printf("pager-managed-poc:elapsed_ns=%llu\n", monotonic_now_ns() - start_ns);
    printf("pager-managed-poc:result=%s\n", failures ? "fail" : "ok");
    managed_pager_destroy(&pager);
    return failures ? 1 : 0;
}

static int run_memory_pager_probe(void) {
    int failures = 0;
    int optional_failures = 0;
    long page_size = sysconf(_SC_PAGESIZE);
    if (page_size <= 0) page_size = 4096;

    void *page = mmap(NULL, (size_t)page_size, PROT_NONE,
                      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    failures += pager_probe_ok("mmap_prot_none", page != MAP_FAILED, errno);
    if (page != MAP_FAILED) {
        int rc = mprotect(page, (size_t)page_size, PROT_READ | PROT_WRITE);
        failures += pager_probe_ok("mprotect_rw", rc == 0, errno);
        if (rc == 0) {
            ((volatile char *)page)[0] = 0x5a;
            failures += pager_probe_ok("write_after_mprotect",
                                       ((volatile char *)page)[0] == 0x5a,
                                       errno);
        }
        rc = madvise(page, (size_t)page_size, MADV_DONTNEED);
        failures += pager_probe_ok("madvise_dontneed", rc == 0, errno);
        munmap(page, (size_t)page_size);
    }

#ifdef __NR_userfaultfd
    errno = 0;
    long ufd = syscall(__NR_userfaultfd, O_CLOEXEC | O_NONBLOCK);
    optional_failures += pager_probe_ok("userfaultfd_syscall", ufd >= 0, errno);
    if (ufd >= 0) close((int)ufd);
#else
    optional_failures += pager_probe_ok("userfaultfd_syscall", 0, ENOSYS);
#endif

    int fd = open("/dev/userfaultfd", O_RDONLY | O_CLOEXEC);
    optional_failures += pager_probe_ok("open_dev_userfaultfd", fd >= 0, errno);
    if (fd >= 0) close(fd);

    int pipefd[2];
    if (pipe(pipefd) != 0) {
        failures += pager_probe_ok("pipe", 0, errno);
        return failures ? 1 : 0;
    }
    pid_t child = fork();
    if (child == 0) {
        close(pipefd[0]);
        char *child_page = mmap(NULL, (size_t)page_size, PROT_READ | PROT_WRITE,
                                MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (child_page == MAP_FAILED) _exit(80);
        snprintf(child_page, (size_t)page_size, "before");
        dprintf(pipefd[1], "%p\n", child_page);
        close(pipefd[1]);
        if (ptrace(PTRACE_TRACEME, 0, NULL, NULL) != 0) _exit(81);
        raise(SIGSTOP);
        if (strcmp(child_page, "parent-write") != 0) _exit(82);
        void *fault_page = mmap(NULL, (size_t)page_size, PROT_NONE,
                                MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (fault_page == MAP_FAILED) _exit(83);
        *((volatile char *)fault_page) = 1;
        _exit(84);
    }
    close(pipefd[1]);
    unsigned long long child_addr = 0;
    FILE *pipe_read = fdopen(pipefd[0], "r");
    if (pipe_read) {
        if (fscanf(pipe_read, "%llx", &child_addr) != 1) child_addr = 0;
        fclose(pipe_read);
    } else {
        close(pipefd[0]);
    }
    int status = 0;
    int waited = waitpid(child, &status, 0);
    failures += pager_probe_ok("ptrace_traceme_stop",
                               waited == child && WIFSTOPPED(status) && WSTOPSIG(status) == SIGSTOP,
                               waited < 0 ? errno : EINVAL);
    if (waited == child && WIFSTOPPED(status)) {
        const char value[] = "parent-write";
        struct iovec local = {(void *)value, sizeof(value)};
        struct iovec remote = {(void *)(uintptr_t)child_addr, sizeof(value)};
        ssize_t written = pdocker_process_vm_writev(child, &local, 1, &remote, 1, 0);
        failures += pager_probe_ok("process_vm_writev_child",
                                   written == (ssize_t)sizeof(value), errno);
        ptrace(PTRACE_CONT, child, NULL, NULL);
        waited = waitpid(child, &status, 0);
        failures += pager_probe_ok("ptrace_sigsegv_stop",
                                   waited == child && WIFSTOPPED(status) && WSTOPSIG(status) == SIGSEGV,
                                   waited < 0 ? errno : EINVAL);
        if (waited == child && WIFSTOPPED(status) && WSTOPSIG(status) == SIGSEGV) {
            siginfo_t info;
            memset(&info, 0, sizeof(info));
            int rc = ptrace(PTRACE_GETSIGINFO, child, NULL, &info);
            failures += pager_probe_ok("ptrace_getsiginfo", rc == 0 && info.si_addr != NULL, errno);
        }
    }
    kill(child, SIGKILL);
    waitpid(child, NULL, 0);
    printf("pager-probe:userfaultfd=%s\n", optional_failures ? "blocked" : "ok");
    printf("pager-probe:ptrace_path=%s\n", failures ? "fail" : "ok");
    printf("pager-probe:result=%s\n", failures ? "fail" : "ok");
    return failures ? 1 : 0;
}

static double monotonic_seconds_since(const struct timespec *start) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (double)(now.tv_sec - start->tv_sec) +
           (double)(now.tv_nsec - start->tv_nsec) / 1000000000.0;
}

static unsigned long long monotonic_now_ns(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (unsigned long long)now.tv_sec * 1000000000ULL +
           (unsigned long long)now.tv_nsec;
}

static void record_syscall_stat(long nr) {
    if (!g_stats) return;
    if (nr >= 0 && nr < (long)(sizeof(g_syscall_counts) / sizeof(g_syscall_counts[0]))) {
        g_syscall_counts[nr]++;
    }
}

static void print_path_micro_profile(void) {
    if (!g_path_profile || !g_path_stats.calls) return;
    double calls = (double)g_path_stats.calls;
    fprintf(stderr,
            "pdocker-direct-path-profile: calls=%llu empty=%llu relative=%llu absolute=%llu no_rewrite=%llu rewrote=%llu rootfd=%llu denied=%llu total_us=%.3f avg_us=%.3f\n",
            g_path_stats.calls, g_path_stats.empty_path, g_path_stats.relative_path,
            g_path_stats.absolute_path, g_path_stats.no_rewrite, g_path_stats.rewrote,
            g_path_stats.rootfd_rewrite, g_path_stats.denied,
            (double)g_path_stats.total_ns / 1000.0,
            ((double)g_path_stats.total_ns / calls) / 1000.0);
    fprintf(stderr,
            "pdocker-direct-path-profile: phase_us read=%.3f relative_validate=%.3f resolve=%.3f validate=%.3f write=%.3f\n",
            (double)g_path_stats.read_ns / 1000.0,
            (double)g_path_stats.relative_validate_ns / 1000.0,
            (double)g_path_stats.resolve_ns / 1000.0,
            (double)g_path_stats.validate_ns / 1000.0,
            (double)g_path_stats.write_ns / 1000.0);
    if (g_path_stats.validate_calls) {
        fprintf(stderr,
            "pdocker-direct-path-profile: validate calls=%llu avg_us=%.3f lexical_us=%.3f realpath_full_us=%.3f parent_realpath_us=%.3f parent_loops=%llu\n",
            g_path_stats.validate_calls,
            ((double)(g_path_stats.validate_lexical_ns +
                      g_path_stats.validate_realpath_full_ns +
                      g_path_stats.validate_parent_realpath_ns) /
                 (double)g_path_stats.validate_calls) / 1000.0,
                (double)g_path_stats.validate_lexical_ns / 1000.0,
                (double)g_path_stats.validate_realpath_full_ns / 1000.0,
            (double)g_path_stats.validate_parent_realpath_ns / 1000.0,
            g_path_stats.validate_parent_loops);
    }
    fprintf(stderr,
            "pdocker-direct-path-profile: cache validation_hits=%llu validation_misses=%llu realpath_hits=%llu realpath_misses=%llu invalidations=%llu generation=%llu\n",
            g_path_stats.validation_cache_hits, g_path_stats.validation_cache_misses,
            g_path_stats.realpath_cache_hits, g_path_stats.realpath_cache_misses,
            g_path_stats.cache_invalidations, g_path_cache_generation);
}

static void print_syscall_stats(const char *reason, int rc) {
    if (!g_stats) return;
    double seconds = monotonic_seconds_since(&g_stats_start);
    fprintf(stderr,
            "pdocker-direct-stats: reason=%s rc=%d elapsed=%.3fs stops=%llu\n",
            reason, rc, seconds, g_stop_count);
    print_path_micro_profile();
    int limit = g_stats_top;
    if (limit < 1) limit = 1;
    if (limit > (int)(sizeof(g_syscall_counts) / sizeof(g_syscall_counts[0]))) {
        limit = (int)(sizeof(g_syscall_counts) / sizeof(g_syscall_counts[0]));
    }
    for (int rank = 0; rank < limit; ++rank) {
        int best = -1;
        unsigned long long best_count = 0;
        for (int i = 0; i < (int)(sizeof(g_syscall_counts) / sizeof(g_syscall_counts[0])); ++i) {
            if (g_syscall_counts[i] > best_count) {
                best = i;
                best_count = g_syscall_counts[i];
            }
        }
        if (best < 0 || best_count == 0) break;
        fprintf(stderr, "pdocker-direct-stats: #%d nr=%d(%s) count=%llu\n",
                rank + 1, best, syscall_name(best), best_count);
        g_syscall_counts[best] = 0;
    }
}

static int syscall_failed_result(unsigned long long result) {
    return result >= (unsigned long long)-4095LL;
}

static int is_memory_trace_syscall(long nr) {
    return nr == 214 ||  /* brk */
           nr == 215 ||  /* munmap */
           nr == 216 ||  /* mremap */
           nr == 222 ||  /* mmap */
           nr == 226 ||  /* mprotect */
           nr == 233;    /* madvise */
}

static void update_memory_stat(MemorySyscallStats *stats, unsigned long long bytes, int failed) {
    stats->calls++;
    if (failed) {
        stats->failures++;
        return;
    }
    stats->bytes += bytes;
    if (bytes >= g_trace_memory_threshold) stats->large_calls++;
    if (bytes > stats->largest) stats->largest = bytes;
}

static void print_one_memory_stat(const char *name, const MemorySyscallStats *stats) {
    if (!stats->calls) return;
    fprintf(stderr,
            "pdocker-direct-memory: %s calls=%llu failures=%llu bytes=%llu large=%llu largest=%llu\n",
            name, stats->calls, stats->failures, stats->bytes,
            stats->large_calls, stats->largest);
}

static void record_managed_pager_admission(const char *decision,
                                           const char *reason,
                                           const char *classification,
                                           unsigned long long bytes,
                                           unsigned long long threshold,
                                           unsigned long long max_region,
                                           int err) {
    snprintf(g_managed_pager_admission.last_decision,
             sizeof(g_managed_pager_admission.last_decision), "%s",
             decision ? decision : "");
    snprintf(g_managed_pager_admission.last_reason,
             sizeof(g_managed_pager_admission.last_reason), "%s",
             reason ? reason : "");
    snprintf(g_managed_pager_admission.last_classification,
             sizeof(g_managed_pager_admission.last_classification), "%s",
             classification ? classification : "");
    g_managed_pager_admission.last_request_bytes = bytes;
    g_managed_pager_admission.last_threshold_bytes = threshold;
    g_managed_pager_admission.last_max_region_bytes = max_region;
    g_managed_pager_admission.last_errno = err > 0 ? (unsigned long long)err : 0ULL;
}

static void print_managed_pager_admission_stats(void) {
    if (!g_managed_pager_admission.considered &&
            !g_managed_pager_admission.rejected_not_enabled) {
        return;
    }
    fprintf(stderr,
            "pdocker-direct-memory-pager: schema=pdocker.memory-pager.admission.v1 "
            "considered=%llu pending=%llu accepted=%llu denied_enomem=%llu "
            "register_failed=%llu cleanup_munmap_failed=%llu "
            "rejected_not_enabled=%llu rejected_below_threshold=%llu "
            "rejected_too_large=%llu rejected_fixed_address=%llu "
            "rejected_flags=%llu rejected_file_backed=%llu rejected_protection=%llu "
            "last_decision=%s last_reason=%s classification=%s last_request_bytes=%llu "
            "threshold_bytes=%llu max_region_bytes=%llu last_errno=%llu "
            "backing_op=%s backing_errno=%d backing_dir=%s\n",
            g_managed_pager_admission.considered,
            g_managed_pager_admission.pending,
            g_managed_pager_admission.accepted,
            g_managed_pager_admission.denied_enomem,
            g_managed_pager_admission.register_failed,
            g_managed_pager_admission.cleanup_munmap_failed,
            g_managed_pager_admission.rejected_not_enabled,
            g_managed_pager_admission.rejected_below_threshold,
            g_managed_pager_admission.rejected_too_large,
            g_managed_pager_admission.rejected_fixed_address,
            g_managed_pager_admission.rejected_flags,
            g_managed_pager_admission.rejected_file_backed,
            g_managed_pager_admission.rejected_protection,
            g_managed_pager_admission.last_decision[0]
                    ? g_managed_pager_admission.last_decision : "none",
            g_managed_pager_admission.last_reason[0]
                    ? g_managed_pager_admission.last_reason : "none",
            g_managed_pager_admission.last_classification[0]
                    ? g_managed_pager_admission.last_classification : "none",
            g_managed_pager_admission.last_request_bytes,
            g_managed_pager_admission.last_threshold_bytes,
            g_managed_pager_admission.last_max_region_bytes,
            g_managed_pager_admission.last_errno,
            g_managed_pager_backing_op,
            g_managed_pager_backing_errno,
            g_managed_pager_backing_dir);
}

static void memory_telemetry_derive_summary_path(void) {
    const char *summary = getenv("PDOCKER_MEMORY_SUMMARY_PATH");
    if (!summary || !summary[0]) summary = getenv("PDOCKER_MEMORY_TELEMETRY_SUMMARY_PATH");
    if (summary && summary[0]) {
        snprintf(g_memory_summary_path, sizeof(g_memory_summary_path), "%s", summary);
        return;
    }
    if (!g_memory_telemetry_path[0]) return;
    snprintf(g_memory_summary_path, sizeof(g_memory_summary_path), "%s", g_memory_telemetry_path);
    char *slash = strrchr(g_memory_summary_path, '/');
    if (slash) {
        snprintf(slash + 1,
                 sizeof(g_memory_summary_path) - (size_t)(slash + 1 - g_memory_summary_path),
                 "memory-summary.json");
    } else {
        snprintf(g_memory_summary_path, sizeof(g_memory_summary_path), "memory-summary.json");
    }
}

static void memory_telemetry_init_from_env(void) {
    const char *path = getenv("PDOCKER_MEMORY_RING_PATH");
    if (!path || !path[0]) path = getenv("PDOCKER_MEMORY_TELEMETRY_PATH");
    if (!path || !path[0]) return;
    snprintf(g_memory_telemetry_path, sizeof(g_memory_telemetry_path), "%s", path);
    const char *op = getenv("PDOCKER_MEMORY_TELEMETRY_OPERATION_ID");
    if (!op || !op[0]) op = getenv("PDOCKER_MEMORY_OPERATION_ID");
    const char *cid = getenv("PDOCKER_MEMORY_TELEMETRY_CONTAINER_ID");
    if (!cid || !cid[0]) cid = getenv("PDOCKER_MEMORY_CONTAINER_ID");
    snprintf(g_memory_operation_id, sizeof(g_memory_operation_id), "%s", op ? op : "");
    snprintf(g_memory_container_id, sizeof(g_memory_container_id), "%s", cid ? cid : "");
    /* Contract tokens: memory-ring.jsonl, memory-summary.json,
       ring_max_bytes, ring_max_lines, max_line_bytes, rotate oldest complete. */
    g_memory_telemetry_max_bytes = env_u64_or_default("PDOCKER_MEMORY_TELEMETRY_MAX_BYTES", 1048576ULL);
    g_memory_telemetry_max_lines = env_u64_or_default("PDOCKER_MEMORY_TELEMETRY_MAX_LINES", 240ULL);
    g_memory_telemetry_max_line_bytes = env_u64_or_default("PDOCKER_MEMORY_TELEMETRY_MAX_LINE_BYTES", 16384ULL);
    if (g_memory_telemetry_max_bytes < 4096ULL) g_memory_telemetry_max_bytes = 4096ULL;
    if (g_memory_telemetry_max_lines == 0) g_memory_telemetry_max_lines = 1;
    if (g_memory_telemetry_max_line_bytes < 4096ULL) g_memory_telemetry_max_line_bytes = 4096ULL;
    g_memory_telemetry_started_unix_ms = wall_now_ms();
    memory_telemetry_derive_summary_path();
    (void)mkdir_parent_for_path(g_memory_telemetry_path);
    (void)mkdir_parent_for_path(g_memory_summary_path);
}

static int memory_telemetry_count_lines_and_bytes(const char *path,
                                                  unsigned long long *lines_out,
                                                  unsigned long long *bytes_out) {
    FILE *fp = fopen(path, "re");
    if (!fp) {
        if (errno == ENOENT) {
            if (lines_out) *lines_out = 0;
            if (bytes_out) *bytes_out = 0;
            return 0;
        }
        return -1;
    }
    unsigned long long lines = 0, bytes = 0;
    char buf[4096];
    while (fgets(buf, sizeof(buf), fp)) {
        bytes += (unsigned long long)strlen(buf);
        if (strchr(buf, '\n')) lines++;
    }
    fclose(fp);
    if (lines_out) *lines_out = lines;
    if (bytes_out) *bytes_out = bytes;
    return 0;
}

static int memory_telemetry_append_bounded_jsonl(const char *line) {
    if (!g_memory_telemetry_path[0] || !line) return 0;
    size_t line_len = strlen(line);
    if (line_len + 1ULL > g_memory_telemetry_max_line_bytes ||
            line_len + 1ULL > g_memory_telemetry_max_bytes) {
        errno = EMSGSIZE;
        g_memory_telemetry_failed = 1;
        return -1;
    }
    if (mkdir_parent_for_path(g_memory_telemetry_path) != 0) {
        g_memory_telemetry_failed = 1;
        return -1;
    }
    char tmp[PATH_MAX];
    if (snprintf(tmp, sizeof(tmp), "%s.tmp.%ld", g_memory_telemetry_path, (long)getpid()) >= (int)sizeof(tmp)) {
        errno = ENAMETOOLONG;
        g_memory_telemetry_failed = 1;
        return -1;
    }

    char **kept = NULL;
    size_t kept_count = 0;
    size_t kept_cap = 0;
    unsigned long long kept_bytes = 0;
    FILE *in = fopen(g_memory_telemetry_path, "re");
    if (in) {
        char *buf = NULL;
        size_t cap = 0;
        ssize_t nread = 0;
        while ((nread = getline(&buf, &cap, in)) >= 0) {
            size_t len = (size_t)nread;
            if (len == 0 || buf[len - 1] != '\n') {
                g_memory_telemetry_truncated = 1;  /* partial JSON record is not evidence */
                continue;
            }
            if ((unsigned long long)len > g_memory_telemetry_max_line_bytes) {
                g_memory_telemetry_truncated = 1;
                continue;
            }
            char *copy = malloc(len + 1);
            if (!copy) {
                free(buf);
                fclose(in);
                for (size_t i = 0; i < kept_count; i++) free(kept[i]);
                free(kept);
                g_memory_telemetry_failed = 1;
                return -1;
            }
            memcpy(copy, buf, len + 1);
            if (kept_count == kept_cap) {
                size_t next_cap = kept_cap ? kept_cap * 2 : 16;
                char **next = realloc(kept, next_cap * sizeof(char *));
                if (!next) {
                    free(copy);
                    free(buf);
                    fclose(in);
                    for (size_t i = 0; i < kept_count; i++) free(kept[i]);
                    free(kept);
                    g_memory_telemetry_failed = 1;
                    return -1;
                }
                kept = next;
                kept_cap = next_cap;
            }
            kept[kept_count++] = copy;
            kept_bytes += (unsigned long long)len;
            while (kept_count + 1ULL > g_memory_telemetry_max_lines ||
                   kept_bytes + (unsigned long long)line_len + 1ULL > g_memory_telemetry_max_bytes) {
                size_t first_len = strlen(kept[0]);
                free(kept[0]);
                if (kept_count > 1) {
                    memmove(kept, kept + 1, (kept_count - 1) * sizeof(char *));
                }
                kept_count--;
                kept_bytes = kept_bytes > (unsigned long long)first_len
                        ? kept_bytes - (unsigned long long)first_len : 0;
                g_memory_telemetry_truncated = 1;
                if (kept_count == 0) break;
            }
        }
        free(buf);
        fclose(in);
    }

    FILE *out = fopen(tmp, "we");
    if (!out) {
        for (size_t i = 0; i < kept_count; i++) free(kept[i]);
        free(kept);
        g_memory_telemetry_failed = 1;
        return -1;
    }
    for (size_t i = 0; i < kept_count; i++) {
        fputs(kept[i], out);
        free(kept[i]);
    }
    free(kept);
    fputs(line, out);
    fputc('\n', out);
    fflush(out);
    fsync(fileno(out));
    if (fclose(out) != 0) {
        unlink(tmp);
        g_memory_telemetry_failed = 1;
        return -1;
    }
    if (rename(tmp, g_memory_telemetry_path) != 0) {
        unlink(tmp);
        g_memory_telemetry_failed = 1;
        return -1;
    }
    fsync_parent_dir_for_path(g_memory_telemetry_path);
    return 0;
}

static void memory_telemetry_append_sample(const char *phase,
                                           const char *classifier_hint,
                                           const char *progress_marker) {
    if (!g_memory_telemetry_path[0]) return;
    unsigned long long available = 0, swap_free = 0;
    (void)read_meminfo_bytes(&available, &swap_free);
    unsigned long long rss = self_rss_bytes();
    unsigned long long storage_free = path_storage_free_bytes(g_memory_telemetry_path);
    const char *classification = classifier_hint ? classifier_hint : "unknown";
    const char *alloc_syscall = g_memory_stats.last_denied_syscall[0]
            ? g_memory_stats.last_denied_syscall
            : (g_managed_pager_admission.last_request_bytes ? "mmap" : "unknown");
    unsigned long long requested = g_memory_stats.last_denied_bytes
            ? g_memory_stats.last_denied_bytes
            : g_managed_pager_admission.last_request_bytes;
    unsigned long long threshold = g_memory_stats.last_denied_bytes
            ? g_memory_guard_min_request
            : g_managed_pager_admission.last_threshold_bytes;
    unsigned long long alloc_errno = g_memory_stats.last_denied_errno
            ? (unsigned long long)g_memory_stats.last_denied_errno
            : g_managed_pager_admission.last_errno;
    int accepted = g_memory_stats.last_denied_bytes ? 0
            : (strcmp(g_managed_pager_admission.last_decision, "accepted") == 0);
    char line[16384];
    FILE *fp = fmemopen(line, sizeof(line), "w");
    if (!fp) return;
    g_memory_telemetry_seq++;
    fprintf(fp, "{\"ring_schema\":\"pdocker.memory-telemetry-ring.v1\"");
    fprintf(fp, ",\"sample_seq\":%llu", g_memory_telemetry_seq);
    fprintf(fp, ",\"sample_time_unix_ms\":%llu", wall_now_ms());
    fprintf(fp, ",\"sample_monotonic_ms\":%llu", monotonic_now_ns() / 1000000ULL);
    fprintf(fp, ",\"operation_id\":"); json_write_string(fp, g_memory_operation_id);
    fprintf(fp, ",\"container_id\":"); json_write_string(fp, g_memory_container_id);
    fprintf(fp, ",\"phase\":"); json_write_string(fp, phase ? phase : "direct-exec");
    fprintf(fp, ",\"tracee_pid\":0,\"process_group_id\":0,\"direct_executor_pid\":%ld", (long)getpid());
    fprintf(fp, ",\"oom_score_adj\":%d", read_oom_score_adj());
    fprintf(fp, ",\"app_lifecycle\":\"direct-executor\"");
    fprintf(fp, ",\"mem_available_bytes\":%llu,\"mem_free_bytes\":0", available);
    fprintf(fp, ",\"swap_free_bytes\":%llu,\"swap_total_bytes\":0,\"zram_bytes\":0", swap_free);
    fprintf(fp, ",\"storage_free_bytes\":%llu,\"rss_bytes\":%llu,\"pss_unavailable\":true", storage_free, rss);
    fprintf(fp, ",\"last_large_allocation\":{\"syscall\":"); json_write_string(fp, alloc_syscall);
    fprintf(fp, ",\"requested_bytes\":%llu,\"accepted\":%s,\"errno\":%llu,\"threshold_bytes\":%llu",
            requested, accepted ? "true" : "false", alloc_errno, threshold);
    fprintf(fp, ",\"mem_available_at_decision_bytes\":%llu,\"swap_free_at_decision_bytes\":%llu",
            g_memory_stats.last_available, g_memory_stats.last_swap_free);
    fprintf(fp, ",\"region_id\":0,\"classification\":"); json_write_string(fp, classification);
    fprintf(fp, ",\"decision\":"); json_write_string(fp, g_managed_pager_admission.last_decision);
    fprintf(fp, ",\"reason\":"); json_write_string(fp, g_managed_pager_admission.last_reason);
    fprintf(fp, "}");
    fprintf(fp, ",\"pager_counters\":{\"reserved_bytes\":0,\"resident_bytes\":0,\"backing_bytes\":0,\"considered\":%llu,\"pending\":%llu,\"accepted\":%llu,\"register_failed\":%llu,\"faults_handled\":0,\"faults_delivered\":0,\"page_ins\":0,\"page_outs\":0,\"dirty_page_outs\":0,\"storage_exhausted\":false,\"dirty_precision\":\"not-applicable\"}",
            g_managed_pager_admission.considered,
            g_managed_pager_admission.pending,
            g_managed_pager_admission.accepted,
            g_managed_pager_admission.register_failed);
    fprintf(fp, ",\"guard_denial_count\":%llu", g_memory_stats.denied);
    fprintf(fp, ",\"classifier_hint\":"); json_write_string(fp, classification);
    fprintf(fp, ",\"progress_marker\":"); json_write_string(fp, progress_marker ? progress_marker : "memory-sample");
    fprintf(fp, ",\"mem_available_at_decision_bytes\":%llu,\"swap_free_at_decision_bytes\":%llu",
            g_memory_stats.last_available, g_memory_stats.last_swap_free);
    fprintf(fp, "}");
    long pos = ftell(fp);
    fclose(fp);
    if (pos <= 0 || (size_t)pos >= sizeof(line)) {
        g_memory_telemetry_failed = 1;
        return;
    }
    (void)memory_telemetry_append_bounded_jsonl(line);
}

static int memory_telemetry_atomic_write_summary(const char *reason, int rc,
                                                 const char *classification) {
    if (!g_memory_summary_path[0]) return 0;
    if (mkdir_parent_for_path(g_memory_summary_path) != 0) {
        g_memory_telemetry_failed = 1;
        return -1;
    }
    unsigned long long ring_lines = 0, ring_bytes = 0;
    (void)memory_telemetry_count_lines_and_bytes(g_memory_telemetry_path, &ring_lines, &ring_bytes);
    char tmp[PATH_MAX];
    if (snprintf(tmp, sizeof(tmp), "%s.tmp.%ld", g_memory_summary_path, (long)getpid()) >= (int)sizeof(tmp)) {
        errno = ENAMETOOLONG;
        g_memory_telemetry_failed = 1;
        return -1;
    }
    FILE *fp = fopen(tmp, "we");
    if (!fp) {
        g_memory_telemetry_failed = 1;
        return -1;
    }
    fprintf(fp, "{\n  \"summary_schema\": \"pdocker.memory-telemetry-summary.v1\",\n");
    fprintf(fp, "  \"summary_seq\": %llu,\n", g_memory_telemetry_seq);
    fprintf(fp, "  \"started_unix_ms\": %llu,\n", g_memory_telemetry_started_unix_ms);
    fprintf(fp, "  \"ended_unix_ms\": %llu,\n", wall_now_ms());
    fprintf(fp, "  \"operation_id\": "); json_write_string(fp, g_memory_operation_id); fprintf(fp, ",\n");
    fprintf(fp, "  \"container_id\": "); json_write_string(fp, g_memory_container_id); fprintf(fp, ",\n");
    fprintf(fp, "  \"final_phase\": "); json_write_string(fp, reason ? reason : "direct-exit"); fprintf(fp, ",\n");
    fprintf(fp, "  \"exit_code\": %d,\n  \"signal\": %d,\n", rc, rc < 0 ? -rc : 0);
    fprintf(fp, "  \"classification\": "); json_write_string(fp, classification ? classification : "unknown"); fprintf(fp, ",\n");
    fprintf(fp, "  \"classifier_reason\": "); json_write_string(fp, reason ? reason : "trace-return"); fprintf(fp, ",\n");
    fprintf(fp, "  \"lmk_suspected\": false,\n");
    fprintf(fp, "  \"last_sample_seq\": %llu,\n", g_memory_telemetry_seq);
    fprintf(fp, "  \"ring_path\": "); json_write_string(fp, g_memory_telemetry_path); fprintf(fp, ",\n");
    fprintf(fp, "  \"ring_bytes\": %llu,\n  \"ring_samples\": %llu,\n", ring_bytes, ring_lines);
    fprintf(fp, "  \"ring_truncated\": %s,\n", g_memory_telemetry_truncated ? "true" : "false");
    unsigned long long final_available = 0, final_swap_free = 0;
    (void)read_meminfo_bytes(&final_available, &final_swap_free);
    fprintf(fp, "  \"final_mem_available_bytes\": %llu,\n  \"final_swap_free_bytes\": %llu,\n", final_available, final_swap_free);
    fprintf(fp, "  \"final_rss_bytes\": %llu,\n  \"pss_unavailable\": true,\n", self_rss_bytes());
    const char *summary_alloc_syscall = g_memory_stats.last_denied_syscall[0] ? g_memory_stats.last_denied_syscall : (g_managed_pager_admission.last_request_bytes ? "mmap" : "unknown");
    unsigned long long summary_requested = g_memory_stats.last_denied_bytes ? g_memory_stats.last_denied_bytes : g_managed_pager_admission.last_request_bytes;
    unsigned long long summary_threshold = g_memory_stats.last_denied_bytes ? g_memory_guard_min_request : g_managed_pager_admission.last_threshold_bytes;
    unsigned long long summary_errno = g_memory_stats.last_denied_errno ? (unsigned long long)g_memory_stats.last_denied_errno : g_managed_pager_admission.last_errno;
    int summary_accepted = g_memory_stats.last_denied_bytes ? 0 : (strcmp(g_managed_pager_admission.last_decision, "accepted") == 0);
    fprintf(fp, "  \"last_large_allocation\": {\"syscall\": "); json_write_string(fp, summary_alloc_syscall);
    fprintf(fp, ", \"requested_bytes\": %llu, \"accepted\": %s, \"errno\": %llu, \"threshold_bytes\": %llu, \"mem_available_at_decision_bytes\": %llu, \"swap_free_at_decision_bytes\": %llu, \"region_id\": 0, \"classification\": ", summary_requested, summary_accepted ? "true" : "false", summary_errno, summary_threshold, g_memory_stats.last_available, g_memory_stats.last_swap_free);
    json_write_string(fp, classification ? classification : "unknown"); fprintf(fp, "},\n");
    fprintf(fp, "  \"pager_counters\": {\"reserved_bytes\": 0, \"resident_bytes\": 0, \"backing_bytes\": 0, \"page_ins\": 0, \"page_outs\": 0, \"dirty_page_outs\": 0, \"faults_handled\": 0, \"faults_delivered\": 0, \"storage_exhausted\": false, \"dirty_precision\": \"not-applicable\"},\n");
    fprintf(fp, "  \"progress_marker\": "); json_write_string(fp, reason ? reason : "trace-return"); fprintf(fp, ",\n");
    fprintf(fp, "  \"ui_live_state_allowed\": false,\n  \"engine_snapshot_fresh\": false,\n  \"pid_liveness_checked\": false,\n");
    fprintf(fp, "  \"artifact_retention_policy\": \"bounded-app-private\",\n");
    fprintf(fp, "  \"telemetry_persistence_failed\": %s,\n", g_memory_telemetry_failed ? "true" : "false");
    fprintf(fp, "  \"summary_write_degraded\": %s\n}\n", g_memory_telemetry_failed ? "true" : "false");
    fflush(fp);
    fsync(fileno(fp));
    if (fclose(fp) != 0) {
        unlink(tmp);
        g_memory_telemetry_failed = 1;
        return -1;
    }
    if (rename(tmp, g_memory_summary_path) != 0) {
        unlink(tmp);
        g_memory_telemetry_failed = 1;
        return -1;
    }
    fsync_parent_dir_for_path(g_memory_summary_path);
    return 0;
}

static void print_memory_stats(const char *reason, int rc) {
    if (!g_trace_memory || g_memory_stats_printed) return;
    g_memory_stats_printed = 1;
    const char *classification = g_memory_stats.denied ? "allocation_denied_enomem" :
            (g_managed_pager_admission.last_classification[0]
             ? g_managed_pager_admission.last_classification : "not_lmk_suspected");
    memory_telemetry_append_sample("trace-return", classification, reason);
    fprintf(stderr,
            "pdocker-direct-memory: reason=%s rc=%d threshold=%llu guard=%d guard_min_request=%llu denied=%llu last_denied=%llu last_available=%llu last_swap_free=%llu\n",
            reason, rc, g_trace_memory_threshold, g_memory_guard,
            g_memory_guard_min_request,
            g_memory_stats.denied, g_memory_stats.last_denied_bytes,
            g_memory_stats.last_available, g_memory_stats.last_swap_free);
    print_one_memory_stat("mmap", &g_memory_stats.mmap_);
    print_one_memory_stat("mremap", &g_memory_stats.mremap);
    print_one_memory_stat("brk", &g_memory_stats.brk);
    print_one_memory_stat("munmap", &g_memory_stats.munmap_);
    print_one_memory_stat("mprotect", &g_memory_stats.mprotect_);
    print_one_memory_stat("madvise", &g_memory_stats.madvise_);
    print_managed_pager_admission_stats();
    (void)memory_telemetry_atomic_write_summary(reason, rc, classification);
}

static int read_meminfo_bytes(unsigned long long *available, unsigned long long *swap_free) {
    FILE *fp = fopen("/proc/meminfo", "re");
    if (!fp) return -1;
    char line[192];
    unsigned long long mem_available_kb = 0;
    unsigned long long mem_free_kb = 0;
    unsigned long long swap_free_kb = 0;
    while (fgets(line, sizeof(line), fp)) {
        sscanf(line, "MemAvailable: %llu kB", &mem_available_kb);
        sscanf(line, "MemFree: %llu kB", &mem_free_kb);
        sscanf(line, "SwapFree: %llu kB", &swap_free_kb);
    }
    fclose(fp);
    if (available) *available = (mem_available_kb ? mem_available_kb : mem_free_kb) * 1024ULL;
    if (swap_free) *swap_free = swap_free_kb * 1024ULL;
    return 0;
}

static int memory_guard_would_deny(unsigned long long requested_bytes,
                                   unsigned long long *available,
                                   unsigned long long *swap_free) {
    if (!g_memory_guard || requested_bytes < g_memory_guard_min_request) return 0;
    unsigned long long avail = 0;
    unsigned long long swap = 0;
    if (read_meminfo_bytes(&avail, &swap) != 0) return 0;
    if (available) *available = avail;
    if (swap_free) *swap_free = swap;
    if (avail < g_memory_guard_min_available) return 1;
    if (swap < g_memory_guard_min_swap) return 1;
    if (requested_bytes > 0 && avail < requested_bytes + g_memory_guard_min_available) return 1;
    return 0;
}

static ssize_t pdocker_process_vm_readv(pid_t pid,
                                        const struct iovec *local_iov,
                                        unsigned long liovcnt,
                                        const struct iovec *remote_iov,
                                        unsigned long riovcnt,
                                        unsigned long flags) {
    return (ssize_t)syscall(__NR_process_vm_readv, pid, local_iov, liovcnt,
                            remote_iov, riovcnt, flags);
}

static ssize_t pdocker_process_vm_writev(pid_t pid,
                                         const struct iovec *local_iov,
                                         unsigned long liovcnt,
                                         const struct iovec *remote_iov,
                                         unsigned long riovcnt,
                                         unsigned long flags) {
    return (ssize_t)syscall(__NR_process_vm_writev, pid, local_iov, liovcnt,
                            remote_iov, riovcnt, flags);
}

static void usage(FILE *stream) {
    fprintf(stream,
            "usage: pdocker-direct --pdocker-direct-probe\n"
            "       pdocker-direct --pdocker-memory-pager-probe\n"
            "       pdocker-direct --pdocker-memory-pager-poc\n"
            "       pdocker-direct --pdocker-memory-pager-managed-poc\n"
            "       pdocker-direct --pdocker-memory-pager-transparent-poc\n"
            "       pdocker-direct run --mode MODE --rootfs PATH --workdir PATH [--env KEY=VAL] [--bind SPEC] -- ARGV...\n");
}

static const char *value_after(int *index, int argc, char **argv, const char *name) {
    if (*index + 1 >= argc) {
        fprintf(stderr, "pdocker-direct-executor: missing value for %s\n", name);
        exit(2);
    }
    *index += 1;
    return argv[*index];
}

static int file_starts_with(const char *path, const char *magic) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    char buf[4] = {0};
    size_t n = fread(buf, 1, sizeof(buf), f);
    fclose(f);
    return n >= strlen(magic) && memcmp(buf, magic, strlen(magic)) == 0;
}

static int elf_has_interp(const char *path) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    Elf64_Ehdr eh;
    ssize_t n = read(fd, &eh, sizeof(eh));
    if (n != (ssize_t)sizeof(eh) || memcmp(eh.e_ident, ELFMAG, SELFMAG) != 0 ||
        eh.e_ident[EI_CLASS] != ELFCLASS64 || eh.e_phoff == 0 ||
        eh.e_phentsize != sizeof(Elf64_Phdr)) {
        close(fd);
        return -1;
    }
    if (lseek(fd, (off_t)eh.e_phoff, SEEK_SET) < 0) {
        close(fd);
        return -1;
    }
    for (int i = 0; i < eh.e_phnum; ++i) {
        Elf64_Phdr ph;
        if (read(fd, &ph, sizeof(ph)) != (ssize_t)sizeof(ph)) {
            close(fd);
            return -1;
        }
        if (ph.p_type == PT_INTERP) {
            close(fd);
            return 1;
        }
    }
    close(fd);
    return 0;
}

static void trim_trailing_slashes(char *path) {
    size_t len = strlen(path);
    while (len > 1 && path[len - 1] == '/') {
        path[len - 1] = '\0';
        len--;
    }
}

static int parse_bind_spec(const char *spec) {
    if (!spec || !spec[0] || g_bind_map_count >= MAX_BIND_MAPS) return 0;
    char tmp[PATH_MAX * 2];
    snprintf(tmp, sizeof(tmp), "%s", spec);
    char *host = tmp;
    char *guest = strchr(tmp, ':');
    if (!guest) return 0;
    *guest++ = '\0';
    char *opts = strchr(guest, ':');
    if (opts) *opts++ = '\0';
    if (!host[0] || !guest[0] || guest[0] != '/') return 0;

    BindMap *m = &g_bind_maps[g_bind_map_count];
    char resolved[PATH_MAX];
    if (realpath(host, resolved)) {
        snprintf(m->host, sizeof(m->host), "%s", resolved);
    } else {
        snprintf(m->host, sizeof(m->host), "%s", host);
    }
    snprintf(m->guest, sizeof(m->guest), "%s", guest);
    trim_trailing_slashes(m->host);
    trim_trailing_slashes(m->guest);
    m->readonly = opts && strstr(opts, "ro");
    g_bind_map_count++;
    return 1;
}

static int bind_guest_match(const BindMap *m, const char *guest, const char **suffix) {
    size_t glen = strlen(m->guest);
    if (strcmp(m->guest, "/") == 0) {
        *suffix = guest;
        return 1;
    }
    if (strncmp(guest, m->guest, glen) != 0) return 0;
    if (guest[glen] != '\0' && guest[glen] != '/') return 0;
    *suffix = guest + glen;
    return 1;
}

static int resolve_bind_path(const char *guest, char *out, size_t out_len) {
    int best = -1;
    size_t best_len = 0;
    const char *best_suffix = NULL;
    for (int i = 0; i < g_bind_map_count; ++i) {
        const char *suffix = NULL;
        if (!bind_guest_match(&g_bind_maps[i], guest, &suffix)) continue;
        size_t len = strlen(g_bind_maps[i].guest);
        if (len >= best_len) {
            best = i;
            best_len = len;
            best_suffix = suffix;
        }
    }
    if (best < 0) return 0;
    const char *host = g_bind_maps[best].host;
    if (!best_suffix || !best_suffix[0]) {
        if (snprintf(out, out_len, "%s", host) >= (int)out_len) return -ENAMETOOLONG;
    } else if (strcmp(host, "/") == 0) {
        if (snprintf(out, out_len, "%s", best_suffix) >= (int)out_len) return -ENAMETOOLONG;
    } else {
        if (snprintf(out, out_len, "%s%s", host, best_suffix) >= (int)out_len) return -ENAMETOOLONG;
    }
    return 1;
}

static int host_path_is_under_prefix(const char *prefix, const char *path) {
    if (!prefix || !prefix[0] || !path || !path[0]) return 0;
    size_t prefix_len = strlen(prefix);
    return strncmp(path, prefix, prefix_len) == 0 &&
           (path[prefix_len] == '\0' || path[prefix_len] == '/');
}

static int bind_host_to_guest_path(const char *host_path, char *out, size_t out_len) {
    int best = -1;
    size_t best_len = 0;
    for (int i = 0; i < g_bind_map_count; ++i) {
        if (!host_path_is_under_prefix(g_bind_maps[i].host, host_path)) continue;
        size_t len = strlen(g_bind_maps[i].host);
        if (len >= best_len) {
            best = i;
            best_len = len;
        }
    }
    if (best < 0) return 0;
    const char *suffix = host_path + strlen(g_bind_maps[best].host);
    if (!suffix[0]) {
        if (snprintf(out, out_len, "%s", g_bind_maps[best].guest) >= (int)out_len) return -ENAMETOOLONG;
    } else if (strcmp(g_bind_maps[best].guest, "/") == 0) {
        if (snprintf(out, out_len, "%s", suffix) >= (int)out_len) return -ENAMETOOLONG;
    } else {
        if (snprintf(out, out_len, "%s%s", g_bind_maps[best].guest, suffix) >= (int)out_len) return -ENAMETOOLONG;
    }
    return 1;
}

static int host_path_is_under_bind_host(const char *path) {
    for (int i = 0; i < g_bind_map_count; ++i) {
        if (host_path_is_under_prefix(g_bind_maps[i].host, path)) return 1;
    }
    return 0;
}

static int host_path_is_under_allowed_host_path(const char *rootfs, const char *path) {
    return host_path_is_under_prefix(rootfs, path) || host_path_is_under_bind_host(path);
}

static unsigned long long path_cache_hash(const char *s, int salt) {
    unsigned long long h = 1469598103934665603ULL ^ (unsigned long long)(unsigned int)salt;
    if (!s) return h;
    while (*s) {
        h ^= (unsigned char)*s++;
        h *= 1099511628211ULL;
    }
    return h;
}

static void invalidate_path_caches(void) {
    g_path_cache_generation++;
    if (g_path_cache_generation == 0) {
        memset(g_path_validation_cache, 0, sizeof(g_path_validation_cache));
        memset(g_path_realpath_cache, 0, sizeof(g_path_realpath_cache));
        g_path_cache_generation = 1;
    }
    if (g_path_profile) g_path_stats.cache_invalidations++;
}

static void begin_path_cache_mutation(TraceeState *state) {
    (void)state;
    invalidate_path_caches();
    g_path_cache_mutation_inflight++;
    g_path_cache_store_disabled++;
}

static void finish_path_cache_mutation(TraceeState *state) {
    (void)state;
    if (g_path_cache_mutation_inflight > 0) g_path_cache_mutation_inflight--;
    if (g_path_cache_store_disabled > 0) g_path_cache_store_disabled--;
}

static int path_validation_cache_get(const char *host_path, int follow_final, int *rc) {
    if (!g_path_cache_enabled || !host_path || !host_path[0]) return 0;
    unsigned long long h = path_cache_hash(host_path, follow_final ? 1 : 0);
    PathValidationCacheEntry *entry = &g_path_validation_cache[h % PATH_VALIDATION_CACHE_SIZE];
    if (entry->generation == g_path_cache_generation &&
        entry->follow_final == follow_final &&
        strcmp(entry->host, host_path) == 0) {
        if (rc) *rc = entry->rc;
        if (g_path_profile) g_path_stats.validation_cache_hits++;
        return 1;
    }
    if (g_path_profile) g_path_stats.validation_cache_misses++;
    return 0;
}

static void path_validation_cache_put(const char *host_path, int follow_final, int rc) {
    if (!g_path_cache_enabled || g_path_cache_store_disabled ||
        g_path_cache_mutation_inflight > 0 || !host_path || !host_path[0]) return;
    if (rc != 0) return;
    unsigned long long h = path_cache_hash(host_path, follow_final ? 1 : 0);
    PathValidationCacheEntry *entry = &g_path_validation_cache[h % PATH_VALIDATION_CACHE_SIZE];
    entry->generation = g_path_cache_generation;
    entry->follow_final = follow_final;
    entry->rc = rc;
    snprintf(entry->host, sizeof(entry->host), "%s", host_path);
}

static char *cached_realpath(const char *path, char *resolved) {
    if (!g_path_cache_enabled || !path || !path[0]) {
        if (g_path_profile) g_path_stats.realpath_cache_misses++;
        return realpath(path, resolved);
    }
    unsigned long long h = path_cache_hash(path, 17);
    PathRealpathCacheEntry *entry = &g_path_realpath_cache[h % PATH_REALPATH_CACHE_SIZE];
    if (entry->generation == g_path_cache_generation &&
        strcmp(entry->path, path) == 0) {
        snprintf(resolved, PATH_MAX, "%s", entry->resolved);
        if (g_path_profile) g_path_stats.realpath_cache_hits++;
        return resolved;
    }
    if (g_path_profile) g_path_stats.realpath_cache_misses++;
    char *rc = realpath(path, resolved);
    if (rc && !g_path_cache_store_disabled && g_path_cache_mutation_inflight == 0) {
        entry->generation = g_path_cache_generation;
        snprintf(entry->path, sizeof(entry->path), "%s", path);
        snprintf(entry->resolved, sizeof(entry->resolved), "%s", resolved);
    }
    return rc;
}

static int resolve_guest_program(const char *rootfs, const char *program, char *out, size_t out_len) {
    if (!program || !program[0]) return -1;
    if (program[0] == '/') {
        if (snprintf(out, out_len, "%s%s", rootfs, program) >= (int)out_len) return -1;
        return access(out, X_OK) == 0 ? 0 : -1;
    }
    const char *path = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";
    char *copy = strdup(path);
    if (!copy) return -1;
    char *save = NULL;
    for (char *dir = strtok_r(copy, ":", &save); dir; dir = strtok_r(NULL, ":", &save)) {
        if (snprintf(out, out_len, "%s/%s/%s", rootfs, dir[0] == '/' ? dir + 1 : dir, program) >= (int)out_len) {
            continue;
        }
        if (access(out, X_OK) == 0) {
            free(copy);
            return 0;
        }
    }
    free(copy);
    return -1;
}

static int parse_shebang(const char *path, char *program, size_t program_len,
                         char *arg, size_t arg_len) {
    FILE *f = fopen(path, "r");
    if (!f) return -1;
    char line[PATH_MAX];
    if (!fgets(line, sizeof(line), f)) {
        fclose(f);
        return -1;
    }
    fclose(f);
    if (strncmp(line, "#!", 2) != 0) return -1;
    char *p = line + 2;
    while (*p == ' ' || *p == '\t') p++;
    char *prog = p;
    while (*p && *p != ' ' && *p != '\t' && *p != '\r' && *p != '\n') p++;
    if (*p) *p++ = '\0';
    while (*p == ' ' || *p == '\t') p++;
    char *first_arg = p;
    while (*p && *p != ' ' && *p != '\t' && *p != '\r' && *p != '\n') p++;
    if (*p) *p = '\0';
    if (strcmp(prog, "/usr/bin/env") == 0 && first_arg[0]) {
        prog = first_arg;
        first_arg = "";
    }
    snprintf(program, program_len, "%s", prog);
    snprintf(arg, arg_len, "%s", first_arg);
    return program[0] ? 0 : -1;
}

static const char *syscall_name(long nr) {
    switch (nr) {
        case 17: return "getcwd";
        case 5: return "setxattr";
        case 6: return "lsetxattr";
        case 8: return "getxattr";
        case 9: return "lgetxattr";
        case 11: return "listxattr";
        case 12: return "llistxattr";
        case 14: return "removexattr";
        case 15: return "lremovexattr";
        case 23: return "dup";
        case 24: return "dup3";
        case 25: return "fcntl";
        case 29: return "ioctl";
        case 33: return "mknodat";
        case 34: return "mkdirat";
        case 35: return "unlinkat";
        case 36: return "symlinkat";
        case 37: return "linkat";
        case 38: return "renameat";
        case 43: return "statfs";
        case 44: return "fstatfs";
        case 48: return "faccessat";
        case 49: return "chdir";
        case 50: return "fchdir";
        case 51: return "chroot";
        case 53: return "fchmodat";
        case 54: return "fchownat";
        case 55: return "fchown";
        case 56: return "openat";
        case 57: return "close";
        case 59: return "pipe2";
        case 61: return "getdents64";
        case 62: return "lseek";
        case 63: return "read";
        case 64: return "write";
        case 78: return "readlinkat";
        case 79: return "newfstatat";
        case 93: return "exit";
        case 94: return "exit_group";
        case 96: return "set_tid_address";
        case 98: return "futex";
        case 99: return "set_robust_list";
        case 117: return "ptrace";
        case 132: return "sigaltstack";
        case 134: return "rt_sigaction";
        case 135: return "rt_sigprocmask";
        case 139: return "rt_sigreturn";
        case 143: return "setregid";
        case 144: return "setgid";
        case 145: return "setreuid";
        case 146: return "setuid";
        case 147: return "setresuid";
        case 148: return "getresuid";
        case 149: return "setresgid";
        case 150: return "getresgid";
        case 151: return "setfsuid";
        case 152: return "setfsgid";
        case 159: return "setgroups";
        case 160: return "uname";
        case 166: return "umask";
        case 167: return "prctl";
        case 172: return "getpid";
        case 173: return "getppid";
        case 174: return "getuid";
        case 175: return "geteuid";
        case 176: return "getgid";
        case 177: return "getegid";
        case 178: return "gettid";
        case 179: return "sysinfo";
        case 197: return "socket";
        case 198: return "socketpair";
        case 203: return "connect";
        case 214: return "brk";
        case 215: return "munmap";
        case 216: return "mremap";
        case 220: return "clone";
        case 221: return "execve";
        case 222: return "mmap";
        case 223: return "fadvise64";
        case 226: return "mprotect";
        case 227: return "msync";
        case 233: return "madvise";
        case 235: return "mbind";
        case 236: return "get_mempolicy";
        case 237: return "set_mempolicy";
        case 238: return "migrate_pages";
        case 239: return "move_pages";
        case 242: return "sched_getaffinity";
        case 260: return "wait4";
        case 261: return "prlimit64";
        case 276: return "renameat2";
        case 278: return "getrandom";
        case 88: return "utimensat";
        case 280: return "bpf";
        case 281: return "execveat";
        case 291: return "statx";
        case 293: return "rseq";
        case 437: return "openat2";
        case 439: return "faccessat2";
        case 425: return "io_uring_setup";
        case 426: return "io_uring_enter";
        case 427: return "io_uring_register";
        case 435: return "clone3";
        case 436: return "close_range";
        case 440: return "process_madvise";
        case 441: return "epoll_pwait2";
        case 443: return "quotactl_fd";
        case 446: return "landlock_restrict_self";
        case 448: return "process_mrelease";
        case 449: return "futex_waitv";
        case 450: return "set_mempolicy_home_node";
        default: return "?";
    }
}

static int get_regs(pid_t pid, struct user_pt_regs *regs) {
    struct iovec iov;
    memset(regs, 0, sizeof(*regs));
    iov.iov_base = regs;
    iov.iov_len = sizeof(*regs);
    return ptrace(PTRACE_GETREGSET, pid, (void *)NT_PRSTATUS, &iov);
}

static int set_regs(pid_t pid, struct user_pt_regs *regs) {
    struct iovec iov;
    iov.iov_base = regs;
    iov.iov_len = sizeof(*regs);
    return ptrace(PTRACE_SETREGSET, pid, (void *)NT_PRSTATUS, &iov);
}

static int set_syscall_number(pid_t pid, int nr) {
    struct iovec iov;
    iov.iov_base = &nr;
    iov.iov_len = sizeof(nr);
    return ptrace(PTRACE_SETREGSET, pid, (void *)NT_ARM_SYSTEM_CALL, &iov);
}

static int syscall_emulate_success(long nr) {
    return nr == 99 ||   /* set_robust_list: Android app seccomp blocks glibc robust futex setup. */
           nr == 174 ||  /* getuid: default container user is root in the current executor. */
           nr == 175 ||  /* geteuid */
           nr == 176 ||  /* getgid */
           nr == 177 ||  /* getegid */
           nr == 54 ||   /* fchownat: keep app-owned files, report container-root success. */
           nr == 55 ||   /* fchown */
           nr == 143 ||  /* setregid */
           nr == 144 ||  /* setgid: keep Android credentials, report container-root success. */
           nr == 145 ||  /* setreuid */
           nr == 146 ||  /* setuid */
           nr == 147 ||  /* setresuid */
           nr == 148 ||  /* getresuid */
           nr == 149 ||  /* setresgid */
           nr == 150 ||  /* getresgid */
           nr == 151 ||  /* setfsuid */
           nr == 152 ||  /* setfsgid */
           nr == 159 ||  /* setgroups */
           nr == 293;    /* rseq: glibc can continue when registration appears unavailable. */
}

static int syscall_emulate_errno(long nr, int *err) {
    if (nr == 51) {
        /* The direct executor does not change the process root.  Return the
         * normal unprivileged Linux failure instead of allowing Android
         * seccomp to surface SIGSYS/"Bad system call" or ENOSYS. */
        if (err) *err = EPERM;
        return 1;
    }
    if ((nr >= 194 && nr <= 197) || (nr >= 235 && nr <= 239) || nr == 450) {
        /* Android app seccomp commonly blocks SysV shared memory and NUMA policy
         * syscalls. Container workloads should treat these like unavailable
         * optional kernel facilities and continue with fallback paths. */
        if (err) *err = ENOSYS;
        return 1;
    }
    return 0;
}

static int syscall_completed_in_userland(long nr) {
    int ignored = 0;
    return nr == 17 ||   /* getcwd */
           nr == 36 ||   /* symlinkat */
           nr == 37 ||   /* linkat */
           nr == 48 ||   /* faccessat */
           nr == 78 ||   /* readlinkat proc-exe */
           nr == 425 ||  /* io_uring_setup: report unavailable to libc/node. */
           nr == 426 ||  /* io_uring_enter */
           nr == 427 ||  /* io_uring_register */
           nr == 439 ||  /* faccessat2 */
           syscall_emulate_errno(nr, &ignored) ||
           syscall_emulate_success(nr);
}

#define ADD_STMT(code_, k_) do { \
    filter[n++] = (struct sock_filter)BPF_STMT((code_), (k_)); \
} while (0)

#define ADD_JUMP(code_, k_, jt_, jf_) do { \
    filter[n++] = (struct sock_filter)BPF_JUMP((code_), (k_), (jt_), (jf_)); \
} while (0)

#define ADD_TRACE_SYSCALL(nr) do { \
    ADD_JUMP(BPF_JMP | BPF_JEQ | BPF_K, (nr), 0, 1); \
    ADD_STMT(BPF_RET | BPF_K, SECCOMP_RET_TRACE); \
} while (0)

#define ADD_ERRNO_SYSCALL(nr, err) do { \
    ADD_JUMP(BPF_JMP | BPF_JEQ | BPF_K, (nr), 0, 1); \
    ADD_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | ((err) & SECCOMP_RET_DATA)); \
} while (0)

static int install_selective_seccomp_trace_filter(void) {
    struct sock_filter filter[192];
    size_t n = 0;

    ADD_STMT(BPF_LD | BPF_W | BPF_ABS, (uint32_t)offsetof(struct seccomp_data, arch));
    ADD_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_AARCH64, 1, 0);
    ADD_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW);
    ADD_STMT(BPF_LD | BPF_W | BPF_ABS, (uint32_t)offsetof(struct seccomp_data, nr));

    /* Path-bearing filesystem syscalls. */
    ADD_TRACE_SYSCALL(5);    /* setxattr */
    ADD_TRACE_SYSCALL(6);    /* lsetxattr */
    ADD_TRACE_SYSCALL(8);    /* getxattr */
    ADD_TRACE_SYSCALL(9);    /* lgetxattr */
    ADD_TRACE_SYSCALL(11);   /* listxattr */
    ADD_TRACE_SYSCALL(12);   /* llistxattr */
    ADD_TRACE_SYSCALL(14);   /* removexattr */
    ADD_TRACE_SYSCALL(15);   /* lremovexattr */
    ADD_TRACE_SYSCALL(17);   /* getcwd */
    ADD_TRACE_SYSCALL(33);   /* mknodat */
    ADD_TRACE_SYSCALL(34);   /* mkdirat */
    ADD_TRACE_SYSCALL(35);   /* unlinkat */
    ADD_TRACE_SYSCALL(36);   /* symlinkat */
    ADD_TRACE_SYSCALL(37);   /* linkat */
    ADD_TRACE_SYSCALL(38);   /* renameat */
    ADD_TRACE_SYSCALL(43);   /* statfs */
    ADD_TRACE_SYSCALL(48);   /* faccessat */
    ADD_TRACE_SYSCALL(49);   /* chdir */
    ADD_ERRNO_SYSCALL(51, EPERM);   /* chroot: unsupported in app domain, fail like unprivileged Linux. */
    ADD_TRACE_SYSCALL(53);   /* fchmodat */
    ADD_TRACE_SYSCALL(54);   /* fchownat */
    ADD_TRACE_SYSCALL(55);   /* fchown */
    ADD_TRACE_SYSCALL(56);   /* openat */
    ADD_TRACE_SYSCALL(78);   /* readlinkat */
    if (g_trace_stat_paths) {
        ADD_TRACE_SYSCALL(79);   /* newfstatat */
        ADD_TRACE_SYSCALL(291);  /* statx */
    }
    ADD_TRACE_SYSCALL(88);   /* utimensat */
    ADD_TRACE_SYSCALL(264);  /* name_to_handle_at */
    ADD_TRACE_SYSCALL(276);  /* renameat2 */
    ADD_TRACE_SYSCALL(281);  /* execveat */
    ADD_TRACE_SYSCALL(437);  /* openat2 */
    ADD_TRACE_SYSCALL(439);  /* faccessat2 */

    /* Process startup, credentials, and Android-blocked compatibility. */
    ADD_ERRNO_SYSCALL(99, ENOSYS);   /* set_robust_list: glibc tolerates unavailable robust futex lists. */
    ADD_TRACE_SYSCALL(143);  /* setregid */
    ADD_TRACE_SYSCALL(144);  /* setgid */
    ADD_TRACE_SYSCALL(145);  /* setreuid */
    ADD_TRACE_SYSCALL(146);  /* setuid */
    ADD_TRACE_SYSCALL(147);  /* setresuid */
    ADD_TRACE_SYSCALL(148);  /* getresuid */
    ADD_TRACE_SYSCALL(149);  /* setresgid */
    ADD_TRACE_SYSCALL(150);  /* getresgid */
    ADD_TRACE_SYSCALL(151);  /* setfsuid */
    ADD_TRACE_SYSCALL(152);  /* setfsgid */
    ADD_TRACE_SYSCALL(159);  /* setgroups */
    ADD_TRACE_SYSCALL(174);  /* getuid */
    ADD_TRACE_SYSCALL(175);  /* geteuid */
    ADD_TRACE_SYSCALL(176);  /* getgid */
    ADD_TRACE_SYSCALL(177);  /* getegid */
    ADD_ERRNO_SYSCALL(194, ENOSYS);  /* shmget */
    ADD_ERRNO_SYSCALL(195, ENOSYS);  /* shmctl */
    ADD_ERRNO_SYSCALL(196, ENOSYS);  /* shmat */
    ADD_ERRNO_SYSCALL(197, ENOSYS);  /* shmdt */
    ADD_TRACE_SYSCALL(200);  /* bind: rewrite AF_UNIX socket paths. */
    ADD_TRACE_SYSCALL(203);  /* connect: rewrite AF_UNIX socket paths. */
    ADD_TRACE_SYSCALL(221);  /* execve */
    ADD_ERRNO_SYSCALL(293, ENOSYS);  /* rseq */
    ADD_ERRNO_SYSCALL(235, ENOSYS);  /* mbind */
    ADD_ERRNO_SYSCALL(236, ENOSYS);  /* get_mempolicy */
    ADD_ERRNO_SYSCALL(237, ENOSYS);  /* set_mempolicy */
    ADD_ERRNO_SYSCALL(238, ENOSYS);  /* migrate_pages */
    ADD_ERRNO_SYSCALL(239, ENOSYS);  /* move_pages */
    ADD_ERRNO_SYSCALL(425, ENOSYS);  /* io_uring_setup */
    ADD_ERRNO_SYSCALL(426, ENOSYS);  /* io_uring_enter */
    ADD_ERRNO_SYSCALL(427, ENOSYS);  /* io_uring_register */
    ADD_ERRNO_SYSCALL(435, ENOSYS);  /* clone3 */
    ADD_ERRNO_SYSCALL(436, ENOSYS);  /* close_range */
    ADD_ERRNO_SYSCALL(450, ENOSYS);  /* set_mempolicy_home_node */

    if (g_trace_memory || g_memory_guard) {
        ADD_TRACE_SYSCALL(214);  /* brk */
        ADD_TRACE_SYSCALL(215);  /* munmap */
        ADD_TRACE_SYSCALL(216);  /* mremap */
        ADD_TRACE_SYSCALL(222);  /* mmap */
        ADD_TRACE_SYSCALL(226);  /* mprotect */
        ADD_TRACE_SYSCALL(233);  /* madvise */
    }

    ADD_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW);

    struct sock_fprog prog = {
        .len = (unsigned short)n,
        .filter = filter,
    };
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0) {
        return -1;
    }
    return prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog);
}

#undef ADD_TRACE_SYSCALL
#undef ADD_ERRNO_SYSCALL
#undef ADD_JUMP
#undef ADD_STMT

static long syscall_remap_number(long nr) {
    return nr;
}

struct TraceeState {
    pid_t pid;
    int active;
    int in_syscall;
    long last_nr;
    long emulated_nr;
    long last_emulated_nr;
    unsigned long long emulated_result;
    unsigned long long uid;
    unsigned long long euid;
    unsigned long long suid;
    unsigned long long gid;
    unsigned long long egid;
    unsigned long long sgid;
    unsigned long long last_args[6];
    unsigned long long last_brk;
    unsigned long long managed_pending_len;
    unsigned long long managed_pending_prot;
    unsigned long long managed_pending_flags;
    int pending_path_cache_invalidation;
    char exec_guest_path[PATH_MAX];
    char guest_cwd[PATH_MAX];
    char pending_guest_cwd[PATH_MAX];
    struct ManagedTraceRegion *managed_regions;
};

static void record_memory_syscall_exit(TraceeState *state, unsigned long long result);
static int handle_syscall_entry(pid_t pid, struct user_pt_regs *regs, TraceeState *state,
                                const char *rootfs, const char *loader, const char *libpath,
                                int events, int *completed_in_userland);

#define MAX_TRACEES 128

static TraceeState *find_tracee(TraceeState *tracees, pid_t pid) {
    for (int i = 0; i < MAX_TRACEES; ++i) {
        if (tracees[i].active && tracees[i].pid == pid) return &tracees[i];
    }
    return NULL;
}

static TraceeState *add_tracee(TraceeState *tracees, pid_t pid) {
    TraceeState *existing = find_tracee(tracees, pid);
    if (existing) return existing;
    for (int i = 0; i < MAX_TRACEES; ++i) {
        if (!tracees[i].active) {
            memset(&tracees[i], 0, sizeof(tracees[i]));
            tracees[i].pid = pid;
            tracees[i].active = 1;
            tracees[i].last_nr = -1;
            tracees[i].emulated_nr = -1;
            tracees[i].last_emulated_nr = -1;
            tracees[i].emulated_result = 0;
            tracees[i].uid = 0;
            tracees[i].euid = 0;
            tracees[i].suid = 0;
            tracees[i].gid = 0;
            tracees[i].egid = 0;
            tracees[i].sgid = 0;
            snprintf(tracees[i].guest_cwd, sizeof(tracees[i].guest_cwd), "/");
            return &tracees[i];
        }
    }
    return NULL;
}

static int is_minus_one_arg(unsigned long long value) {
    return value == 0xffffffffffffffffULL;
}

static int write_tracee_u32(pid_t pid, unsigned long long addr, unsigned long long value) {
    if (!addr) return -1;
    uint32_t v = (uint32_t)value;
    if (write_tracee_data(pid, addr, &v, sizeof(v)) == 0) return 0;
    unsigned long long aligned = addr & ~(unsigned long long)(sizeof(long) - 1);
    unsigned long shift = (unsigned long)(addr - aligned) * 8u;
    errno = 0;
    long word = ptrace(PTRACE_PEEKDATA, pid, (void *)(uintptr_t)aligned, NULL);
    if (word == -1 && errno) {
        TRACE_LOG("pdocker-direct-trace: pid=%d write u32 peek failed addr=%llx: %s\n",
                  (int)pid, addr, strerror(errno));
        return -1;
    }
    unsigned long mask = 0xffffffffUL << shift;
    unsigned long patched = ((unsigned long)word & ~mask) | (((unsigned long)v << shift) & mask);
    if (ptrace(PTRACE_POKEDATA, pid, (void *)(uintptr_t)aligned, (void *)patched) != 0) {
        TRACE_LOG("pdocker-direct-trace: pid=%d write u32 poke failed addr=%llx: %s\n",
                  (int)pid, addr, strerror(errno));
        return -1;
    }
    return 0;
}

static unsigned long long prepare_emulated_result(pid_t pid, TraceeState *state, long nr) {
    switch (nr) {
        case 174: return state->uid;
        case 175: return state->euid;
        case 176: return state->gid;
        case 177: return state->egid;
        case 143: /* setregid(rgid, egid) */
            if (!is_minus_one_arg(state->last_args[0])) state->gid = state->last_args[0];
            if (!is_minus_one_arg(state->last_args[1])) state->egid = state->last_args[1];
            state->sgid = state->egid;
            return 0;
        case 144: /* setgid(gid) */
            state->gid = state->last_args[0];
            state->egid = state->last_args[0];
            state->sgid = state->last_args[0];
            return 0;
        case 145: /* setreuid(ruid, euid) */
            if (!is_minus_one_arg(state->last_args[0])) state->uid = state->last_args[0];
            if (!is_minus_one_arg(state->last_args[1])) state->euid = state->last_args[1];
            state->suid = state->euid;
            return 0;
        case 146: /* setuid(uid) */
            state->uid = state->last_args[0];
            state->euid = state->last_args[0];
            state->suid = state->last_args[0];
            return 0;
        case 147: /* setresuid(ruid, euid, suid) */
            if (!is_minus_one_arg(state->last_args[0])) state->uid = state->last_args[0];
            if (!is_minus_one_arg(state->last_args[1])) state->euid = state->last_args[1];
            if (!is_minus_one_arg(state->last_args[2])) state->suid = state->last_args[2];
            else state->suid = state->euid;
            TRACE_LOG("pdocker-direct-trace: pid=%d setresuid args=%llx,%llx,%llx -> %llu,%llu,%llu\n",
                      (int)pid, state->last_args[0], state->last_args[1], state->last_args[2],
                      state->uid, state->euid, state->suid);
            return 0;
        case 148: /* getresuid(ruid, euid, suid) */
            write_tracee_u32(pid, state->last_args[0], state->uid);
            write_tracee_u32(pid, state->last_args[1], state->euid);
            write_tracee_u32(pid, state->last_args[2], state->suid);
            TRACE_LOG("pdocker-direct-trace: pid=%d getresuid -> %llu,%llu,%llu ptr=%llx,%llx,%llx\n",
                      (int)pid, state->uid, state->euid, state->suid,
                      state->last_args[0], state->last_args[1], state->last_args[2]);
            return 0;
        case 149: /* setresgid(rgid, egid, sgid) */
            if (!is_minus_one_arg(state->last_args[0])) state->gid = state->last_args[0];
            if (!is_minus_one_arg(state->last_args[1])) state->egid = state->last_args[1];
            if (!is_minus_one_arg(state->last_args[2])) state->sgid = state->last_args[2];
            else state->sgid = state->egid;
            TRACE_LOG("pdocker-direct-trace: pid=%d setresgid args=%llx,%llx,%llx -> %llu,%llu,%llu\n",
                      (int)pid, state->last_args[0], state->last_args[1], state->last_args[2],
                      state->gid, state->egid, state->sgid);
            return 0;
        case 150: /* getresgid(rgid, egid, sgid) */
            write_tracee_u32(pid, state->last_args[0], state->gid);
            write_tracee_u32(pid, state->last_args[1], state->egid);
            write_tracee_u32(pid, state->last_args[2], state->sgid);
            TRACE_LOG("pdocker-direct-trace: pid=%d getresgid -> %llu,%llu,%llu ptr=%llx,%llx,%llx\n",
                      (int)pid, state->gid, state->egid, state->sgid,
                      state->last_args[0], state->last_args[1], state->last_args[2]);
            return 0;
        default:
            if (nr == 147 || nr == 149) {
                TRACE_LOG("pdocker-direct-trace: pid=%d cred nr=%ld args=%llx,%llx,%llx uid=%llu,%llu,%llu gid=%llu,%llu,%llu\n",
                          (int)pid, nr, state->last_args[0], state->last_args[1], state->last_args[2],
                          state->uid, state->euid, state->suid, state->gid, state->egid, state->sgid);
            }
            return 0;
    }
}

static void free_managed_trace_regions(TraceeState *state);

static void remove_tracee(TraceeState *tracees, pid_t pid) {
    TraceeState *state = find_tracee(tracees, pid);
    if (state) {
        if (state->pending_path_cache_invalidation) {
            finish_path_cache_mutation(state);
            state->pending_path_cache_invalidation = 0;
        }
        free_managed_trace_regions(state);
        memset(state, 0, sizeof(*state));
    }
}

static int tracee_count(TraceeState *tracees) {
    int count = 0;
    for (int i = 0; i < MAX_TRACEES; ++i) {
        if (tracees[i].active) count++;
    }
    return count;
}

static int tracee_is_still_owned(pid_t tracer, pid_t tracee) {
    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/status", (int)tracee);
    FILE *fp = fopen(path, "re");
    if (!fp) return 0;
    char line[128];
    int ppid = -1;
    int tracer_pid = -1;
    while (fgets(line, sizeof(line), fp)) {
        sscanf(line, "PPid:\t%d", &ppid);
        sscanf(line, "TracerPid:\t%d", &tracer_pid);
    }
    fclose(fp);
    return ppid == (int)tracer || tracer_pid == (int)tracer;
}

static void tracee_status_summary(pid_t tracee, char *buf, size_t cap) {
    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/status", (int)tracee);
    FILE *fp = fopen(path, "re");
    if (!fp) {
        snprintf(buf, cap, "status=unreadable:%s", strerror(errno));
        return;
    }
    char line[128];
    char state[32] = "?";
    int ppid = -1;
    int tracer_pid = -1;
    while (fgets(line, sizeof(line), fp)) {
        sscanf(line, "State:\t%31[^\n]", state);
        sscanf(line, "PPid:\t%d", &ppid);
        sscanf(line, "TracerPid:\t%d", &tracer_pid);
    }
    fclose(fp);
    snprintf(buf, cap, "state=%s ppid=%d tracer=%d", state, ppid, tracer_pid);
}

static int prune_dead_tracees(TraceeState *tracees, pid_t tracer) {
    int alive = 0;
    for (int i = 0; i < MAX_TRACEES; ++i) {
        if (!tracees[i].active) continue;
        if (kill(tracees[i].pid, 0) == 0 || errno == EPERM) {
            if (tracee_is_still_owned(tracer, tracees[i].pid)) {
                alive++;
            } else {
                TRACE_LOG("pdocker-direct-trace: prune detached/reused tracee pid=%d last=%ld(%s)\n",
                          (int)tracees[i].pid, tracees[i].last_nr,
                          syscall_name(tracees[i].last_nr));
                memset(&tracees[i], 0, sizeof(tracees[i]));
            }
        } else if (errno == ESRCH) {
            TRACE_LOG("pdocker-direct-trace: prune vanished tracee pid=%d last=%ld(%s)\n",
                      (int)tracees[i].pid, tracees[i].last_nr,
                      syscall_name(tracees[i].last_nr));
            memset(&tracees[i], 0, sizeof(tracees[i]));
        }
    }
    return alive;
}

static int set_trace_options(pid_t pid) {
    long opts = PTRACE_O_TRACESYSGOOD |
                PTRACE_O_TRACEEXEC |
                PTRACE_O_TRACESECCOMP |
                PTRACE_O_TRACEEXIT |
                PTRACE_O_TRACEFORK |
                PTRACE_O_TRACEVFORK |
                PTRACE_O_TRACECLONE;
#ifdef PTRACE_O_EXITKILL
    opts |= PTRACE_O_EXITKILL;
#endif
    if (ptrace(PTRACE_SETOPTIONS, pid, NULL, (void *)opts) != 0) {
        TRACE_LOG("pdocker-direct-trace: PTRACE_SETOPTIONS pid=%d failed: %s\n",
                  (int)pid, strerror(errno));
        return -1;
    }
    return 0;
}

static int continue_tracee(pid_t pid, int sig) {
    if (g_sync_usec > 0) usleep((useconds_t)g_sync_usec);
    int request = g_selective_trace ? PTRACE_CONT : PTRACE_SYSCALL;
    return ptrace(request, pid, NULL, (void *)(long)sig);
}

static int continue_tracee_to_syscall_exit(pid_t pid, int sig) {
    if (g_sync_usec > 0) usleep((useconds_t)g_sync_usec);
    return ptrace(PTRACE_SYSCALL, pid, NULL, (void *)(long)sig);
}

static ssize_t read_tracee_string(pid_t pid, unsigned long long addr, char *buf, size_t cap) {
    if (!addr || cap == 0) return -1;
    size_t off = 0;
    while (off + 1 < cap) {
        char chunk[128];
        size_t want = sizeof(chunk);
        if (want > cap - 1 - off) want = cap - 1 - off;
        struct iovec local = {.iov_base = chunk, .iov_len = want};
        struct iovec remote = {.iov_base = (void *)(uintptr_t)(addr + off), .iov_len = want};
        ssize_t n = pdocker_process_vm_readv(pid, &local, 1, &remote, 1, 0);
        if (n <= 0) return -1;
        memcpy(buf + off, chunk, (size_t)n);
        for (ssize_t i = 0; i < n; ++i) {
            if (chunk[i] == '\0') {
                buf[off + (size_t)i] = '\0';
                return (ssize_t)(off + (size_t)i);
            }
        }
        off += (size_t)n;
    }
    buf[cap - 1] = '\0';
    return (ssize_t)(cap - 1);
}

typedef struct {
    char *data;
    size_t len;
    size_t cap;
} ExecArgArena;

static void free_exec_arg_arena(ExecArgArena *arena) {
    if (!arena) return;
    free(arena->data);
    arena->data = NULL;
    arena->len = 0;
    arena->cap = 0;
}

static int reserve_exec_arg_arena(ExecArgArena *arena, size_t need) {
    if (need > (size_t)EXEC_REWRITE_MAX_ARG_BYTES) {
        errno = ENAMETOOLONG;
        return -1;
    }
    if (arena->cap >= need) return 0;
    size_t next = arena->cap ? arena->cap : 8192;
    while (next < need) {
        if (next > (size_t)EXEC_REWRITE_MAX_ARG_BYTES / 2) {
            next = (size_t)EXEC_REWRITE_MAX_ARG_BYTES;
            break;
        }
        next *= 2;
    }
    if (next < need) {
        errno = ENAMETOOLONG;
        return -1;
    }
    char *grown = realloc(arena->data, next);
    if (!grown) return -1;
    arena->data = grown;
    arena->cap = next;
    return 0;
}

static int read_tracee_string_to_arena(pid_t pid, unsigned long long addr,
                                       ExecArgArena *arena, size_t *offset_out) {
    if (!addr || !arena || !offset_out) return -1;
    *offset_out = arena->len;
    size_t local_len = 0;
    while (local_len < (size_t)EXEC_REWRITE_MAX_ARG_BYTES) {
        char chunk[512];
        if (arena->len + 1 >= (size_t)EXEC_REWRITE_MAX_ARG_BYTES) {
            errno = ENAMETOOLONG;
            return -1;
        }
        size_t want = sizeof(chunk);
        size_t remaining = (size_t)EXEC_REWRITE_MAX_ARG_BYTES - arena->len - 1;
        if (want > remaining) want = remaining;
        if (want == 0) {
            errno = ENAMETOOLONG;
            return -1;
        }
        if (reserve_exec_arg_arena(arena, arena->len + want + 1) != 0) {
            return -1;
        }
        struct iovec local = {.iov_base = chunk, .iov_len = want};
        struct iovec remote = {.iov_base = (void *)(uintptr_t)(addr + local_len), .iov_len = want};
        ssize_t n = pdocker_process_vm_readv(pid, &local, 1, &remote, 1, 0);
        if (n <= 0) return -1;
        memcpy(arena->data + arena->len, chunk, (size_t)n);
        for (ssize_t i = 0; i < n; ++i) {
            if (chunk[i] == '\0') {
                arena->len += (size_t)i + 1;
                return 0;
            }
        }
        arena->len += (size_t)n;
        local_len += (size_t)n;
    }
    errno = ENAMETOOLONG;
    return -1;
}

static int write_tracee_string(pid_t pid, unsigned long long addr, const char *value) {
    size_t len = strlen(value) + 1;
    struct iovec local = {.iov_base = (void *)value, .iov_len = len};
    struct iovec remote = {.iov_base = (void *)(uintptr_t)addr, .iov_len = len};
    ssize_t n = pdocker_process_vm_writev(pid, &local, 1, &remote, 1, 0);
    return n == (ssize_t)len ? 0 : -1;
}

static int write_tracee_data(pid_t pid, unsigned long long addr, const void *value, size_t len) {
    struct iovec local = {.iov_base = (void *)value, .iov_len = len};
    struct iovec remote = {.iov_base = (void *)(uintptr_t)addr, .iov_len = len};
    ssize_t n = pdocker_process_vm_writev(pid, &local, 1, &remote, 1, 0);
    return n == (ssize_t)len ? 0 : -1;
}

static int read_tracee_u32(pid_t pid, unsigned long long addr, uint32_t *out) {
    unsigned long long aligned = addr & ~(unsigned long long)(sizeof(long) - 1);
    unsigned shift = (unsigned)((addr - aligned) * 8u);
    errno = 0;
    long word = ptrace(PTRACE_PEEKDATA, pid, (void *)(uintptr_t)aligned, NULL);
    if (word == -1 && errno) return -1;
    *out = (uint32_t)(((unsigned long)word >> shift) & 0xffffffffUL);
    return 0;
}

static void maybe_advance_past_svc(pid_t pid, struct user_pt_regs *regs) {
    uint32_t insn = 0;
    if (read_tracee_u32(pid, regs->pc, &insn) == 0 && insn == 0xd4000001U) {
        regs->pc += 4;
    }
}

static int complete_emulated_syscall(pid_t pid, struct user_pt_regs *regs,
                                     unsigned long long result) {
    regs->regs[0] = result;
    regs->regs[8] = (unsigned long long)-1;
    if (set_syscall_number(pid, -1) != 0) return -1;
    maybe_advance_past_svc(pid, regs);
    return set_regs(pid, regs);
}

#define MAX_MANAGED_TRACE_REGIONS 8

typedef struct ManagedTraceRegion {
    int active;
    unsigned long long base;
    unsigned long long length;
    int prot;
    size_t page_size;
    size_t page_count;
    size_t resident_limit;
    size_t resident_count;
    size_t max_resident_count;
    size_t clock_hand;
    int backing_fd;
    unsigned char *resident;
    unsigned char *ever_loaded;
    unsigned long long page_ins;
    unsigned long long page_outs;
    unsigned long long dirty_page_outs;
    unsigned long long bytes_in;
    unsigned long long bytes_out;
} ManagedTraceRegion;

static int managed_trace_is_candidate_mmap(const unsigned long long args[6]) {
    unsigned long long addr = args[0];
    unsigned long long length = args[1];
    unsigned long long prot = args[2];
    unsigned long long flags = args[3];
    unsigned long long fd = args[4];
    if (!g_managed_memory_pager) {
        g_managed_pager_admission.rejected_not_enabled++;
        return 0;
    }
    g_managed_pager_admission.considered++;
    if (addr != 0) {
        g_managed_pager_admission.rejected_fixed_address++;
        record_managed_pager_admission("pass-through", "fixed-address", "not_lmk_suspected", length,
                                       g_managed_memory_pager_min_request,
                                       g_managed_memory_pager_max_region, 0);
        return 0;
    }
    if (length < g_managed_memory_pager_min_request) {
        g_managed_pager_admission.rejected_below_threshold++;
        record_managed_pager_admission("pass-through", "below-threshold", "not_lmk_suspected", length,
                                       g_managed_memory_pager_min_request,
                                       g_managed_memory_pager_max_region, 0);
        return 0;
    }
    if (g_managed_memory_pager_max_region && length > g_managed_memory_pager_max_region) {
        g_managed_pager_admission.rejected_too_large++;
        record_managed_pager_admission("pass-through", "too-large", "not_lmk_suspected", length,
                                       g_managed_memory_pager_min_request,
                                       g_managed_memory_pager_max_region, 0);
        return 0;
    }
    if (!(flags & MAP_ANONYMOUS) || !(flags & MAP_PRIVATE) || (flags & MAP_FIXED)) {
        g_managed_pager_admission.rejected_flags++;
        record_managed_pager_admission("pass-through", "unsupported-flags", "not_lmk_suspected", length,
                                       g_managed_memory_pager_min_request,
                                       g_managed_memory_pager_max_region, 0);
        return 0;
    }
    if (fd != 0xffffffffffffffffULL && fd != 0xffffffffULL) {
        g_managed_pager_admission.rejected_file_backed++;
        record_managed_pager_admission("pass-through", "file-backed", "not_lmk_suspected", length,
                                       g_managed_memory_pager_min_request,
                                       g_managed_memory_pager_max_region, 0);
        return 0;
    }
    if (!(prot & (PROT_READ | PROT_WRITE)) || (prot & PROT_EXEC)) {
        g_managed_pager_admission.rejected_protection++;
        record_managed_pager_admission("pass-through", "unsupported-protection", "not_lmk_suspected", length,
                                       g_managed_memory_pager_min_request,
                                       g_managed_memory_pager_max_region, 0);
        return 0;
    }
    record_managed_pager_admission("pending", "candidate", "unknown", length,
                                   g_managed_memory_pager_min_request,
                                   g_managed_memory_pager_max_region, 0);
    return 1;
}

static void free_one_managed_region(ManagedTraceRegion *region) {
    if (!region || !region->active) return;
    if (region->backing_fd >= 0) close(region->backing_fd);
    free(region->resident);
    free(region->ever_loaded);
    memset(region, 0, sizeof(*region));
    region->backing_fd = -1;
}

static void free_managed_trace_regions(TraceeState *state) {
    if (!state || !state->managed_regions) return;
    for (size_t i = 0; i < MAX_MANAGED_TRACE_REGIONS; ++i) {
        free_one_managed_region(&state->managed_regions[i]);
    }
    free(state->managed_regions);
    state->managed_regions = NULL;
}

static ManagedTraceRegion *find_managed_region_for_addr(TraceeState *state,
                                                        unsigned long long addr,
                                                        size_t *page_index_out) {
    if (!state || !state->managed_regions) return NULL;
    for (size_t i = 0; i < MAX_MANAGED_TRACE_REGIONS; ++i) {
        ManagedTraceRegion *region = &state->managed_regions[i];
        if (!region->active) continue;
        if (addr >= region->base && addr < region->base + region->length) {
            size_t idx = (size_t)((addr - region->base) / region->page_size);
            if (idx >= region->page_count) return NULL;
            if (page_index_out) *page_index_out = idx;
            return region;
        }
    }
    return NULL;
}

static ManagedTraceRegion *alloc_managed_region_slot(TraceeState *state) {
    if (!state->managed_regions) {
        state->managed_regions = (ManagedTraceRegion *)calloc(MAX_MANAGED_TRACE_REGIONS,
                                                              sizeof(ManagedTraceRegion));
        if (!state->managed_regions) return NULL;
        for (size_t i = 0; i < MAX_MANAGED_TRACE_REGIONS; ++i) {
            state->managed_regions[i].backing_fd = -1;
        }
    }
    for (size_t i = 0; i < MAX_MANAGED_TRACE_REGIONS; ++i) {
        if (!state->managed_regions[i].active) return &state->managed_regions[i];
    }
    return NULL;
}

static int register_managed_mmap_region(TraceeState *state,
                                        unsigned long long base,
                                        unsigned long long length,
                                        int prot) {
    if (!state || syscall_failed_result(base) || !base || !length) return -1;
    long ps = sysconf(_SC_PAGESIZE);
    size_t page_size = ps > 0 ? (size_t)ps : 4096u;
    unsigned long long pages64 = (length + page_size - 1ULL) / page_size;
    if (!pages64 || pages64 > (unsigned long long)(SIZE_MAX / 2u)) {
        errno = EOVERFLOW;
        return -1;
    }
    ManagedTraceRegion *region = alloc_managed_region_slot(state);
    if (!region) {
        errno = ENOSPC;
        return -1;
    }
    memset(region, 0, sizeof(*region));
    region->backing_fd = -1;
    region->base = base;
    region->length = pages64 * page_size;
    region->prot = prot ? prot : (PROT_READ | PROT_WRITE);
    region->page_size = page_size;
    region->page_count = (size_t)pages64;
    region->resident_limit = (size_t)g_managed_memory_pager_resident_pages;
    if (region->resident_limit == 0) region->resident_limit = 1;
    if (region->resident_limit > region->page_count) region->resident_limit = region->page_count;
    region->resident = (unsigned char *)calloc(region->page_count, 1);
    region->ever_loaded = (unsigned char *)calloc(region->page_count, 1);
    if (!region->resident || !region->ever_loaded) goto fail;
    region->backing_fd = managed_pager_open_backing_file();
    if (region->backing_fd < 0) goto fail;
    if (ftruncate(region->backing_fd, (off_t)region->length) != 0) {
        managed_pager_record_backing_attempt(g_managed_pager_backing_dir,
                                             g_managed_pager_backing_path,
                                             "ftruncate", errno);
        goto fail;
    }
    region->active = 1;
    g_managed_pager_admission.accepted++;
    record_managed_pager_admission("accepted", "registered", "not_lmk_suspected", region->length,
                                   g_managed_memory_pager_min_request,
                                   g_managed_memory_pager_max_region, 0);
    fprintf(stderr,
            "pdocker-direct-managed-pager: registered pid=%d base=0x%llx length=%llu pages=%zu resident_limit=%zu prot=0x%x\n",
            (int)state->pid, base, region->length, region->page_count,
            region->resident_limit, region->prot);
    return 0;
fail:
    {
        int saved = errno ? errno : ENOMEM;
        g_managed_pager_admission.register_failed++;
        record_managed_pager_admission("failed", "register-failed", "allocation_denied_enomem", length,
                                       g_managed_memory_pager_min_request,
                                       g_managed_memory_pager_max_region, saved);
        free_one_managed_region(region);
        errno = saved;
    }
    return -1;
}

static int evict_one_managed_trace_page(pid_t pid, ManagedTraceRegion *region,
                                        size_t avoid_index,
                                        struct user_pt_regs *fault_regs) {
    if (!region || region->resident_count == 0) return -1;
    for (size_t scanned = 0; scanned < region->page_count * 2u; ++scanned) {
        size_t idx = region->clock_hand++ % region->page_count;
        if (idx == avoid_index || !region->resident[idx]) continue;
        unsigned long long addr = region->base + (unsigned long long)idx * region->page_size;
        unsigned char *buf = (unsigned char *)malloc(region->page_size);
        if (!buf) return -1;
        struct iovec local = {.iov_base = buf, .iov_len = region->page_size};
        struct iovec remote = {.iov_base = (void *)(uintptr_t)addr, .iov_len = region->page_size};
        ssize_t n = pdocker_process_vm_readv(pid, &local, 1, &remote, 1, 0);
        if (n != (ssize_t)region->page_size) {
            free(buf);
            return -1;
        }
        ssize_t written = pwrite(region->backing_fd, buf, region->page_size,
                                 (off_t)((unsigned long long)idx * region->page_size));
        free(buf);
        if (written != (ssize_t)region->page_size) return -1;
        region->dirty_page_outs++;
        region->bytes_out += (unsigned long long)region->page_size;
        unsigned long long result = 0;
        if (inject_tracee_syscall(pid, fault_regs, __NR_mprotect,
                                  addr, (unsigned long long)region->page_size,
                                  PROT_NONE, &result) != 0 || result != 0) {
            return -1;
        }
        region->resident[idx] = 0;
        region->resident_count--;
        region->page_outs++;
        return 0;
    }
    return -1;
}

static int handle_managed_memory_fault(pid_t pid, TraceeState *state,
                                       struct user_pt_regs *fault_regs,
                                       unsigned long long fault_addr) {
    size_t idx = 0;
    ManagedTraceRegion *region = find_managed_region_for_addr(state, fault_addr, &idx);
    if (!region) return 0;
    while (region->resident_count >= region->resident_limit) {
        if (evict_one_managed_trace_page(pid, region, idx, fault_regs) != 0) {
            fprintf(stderr,
                    "pdocker-direct-managed-pager: evict failed pid=%d addr=0x%llx errno=%d\n",
                    (int)pid, fault_addr, errno);
            return -1;
        }
    }
    unsigned long long page_addr = region->base + (unsigned long long)idx * region->page_size;
    unsigned long long result = 0;
    if (inject_tracee_syscall(pid, fault_regs, __NR_mprotect,
                              page_addr, (unsigned long long)region->page_size,
                              region->prot, &result) != 0 || result != 0) {
        fprintf(stderr,
                "pdocker-direct-managed-pager: mprotect-in failed pid=%d page=0x%llx result=%lld errno=%d\n",
                (int)pid, page_addr, (long long)result, errno);
        return -1;
    }
    if (!region->resident[idx]) {
        unsigned char *buf = (unsigned char *)calloc(1, region->page_size);
        if (!buf) return -1;
        if (region->ever_loaded[idx]) {
            ssize_t n = pread(region->backing_fd, buf, region->page_size,
                              (off_t)((unsigned long long)idx * region->page_size));
            if (n < 0) {
                free(buf);
                return -1;
            }
            if (n < (ssize_t)region->page_size) {
                memset(buf + n, 0, region->page_size - (size_t)n);
            }
        }
        struct iovec local = {.iov_base = buf, .iov_len = region->page_size};
        struct iovec remote = {.iov_base = (void *)(uintptr_t)page_addr, .iov_len = region->page_size};
        ssize_t written = pdocker_process_vm_writev(pid, &local, 1, &remote, 1, 0);
        free(buf);
        if (written != (ssize_t)region->page_size) return -1;
        region->resident[idx] = 1;
        region->ever_loaded[idx] = 1;
        region->resident_count++;
        if (region->resident_count > region->max_resident_count) {
            region->max_resident_count = region->resident_count;
        }
        region->page_ins++;
        region->bytes_in += (unsigned long long)region->page_size;
    }
    if (set_regs(pid, fault_regs) != 0) return -1;
    return 1;
}

static int maybe_prepare_managed_mmap(pid_t pid, struct user_pt_regs *regs,
                                      TraceeState *state) {
    (void)pid;
    if (!managed_trace_is_candidate_mmap(state->last_args)) {
        state->managed_pending_len = 0;
        return 0;
    }
    state->managed_pending_len = state->last_args[1];
    state->managed_pending_prot = state->last_args[2];
    state->managed_pending_flags = state->last_args[3];
    g_managed_pager_admission.pending++;
    regs->regs[2] = PROT_NONE;
    if (set_regs(pid, regs) != 0) {
        state->managed_pending_len = 0;
        return -1;
    }
    fprintf(stderr,
            "pdocker-direct-managed-pager: pending mmap pid=%d length=%llu prot=0x%llx flags=0x%llx\n",
            (int)state->pid, state->managed_pending_len,
            state->managed_pending_prot, state->managed_pending_flags);
    return 1;
}

static void maybe_finish_managed_mmap(pid_t pid, struct user_pt_regs *regs,
                                      TraceeState *state,
                                      unsigned long long result) {
    if (!state || !state->managed_pending_len) return;
    unsigned long long len = state->managed_pending_len;
    int prot = (int)state->managed_pending_prot;
    state->managed_pending_len = 0;
    if (syscall_failed_result(result) || !result) return;
    if (register_managed_mmap_region(state, result, len, prot) == 0) return;
    int saved_errno = errno ? errno : ENOMEM;
    unsigned long long cleanup_result = 0;
    int cleanup_rc = inject_tracee_syscall(pid, regs, __NR_munmap, result, len, 0,
                                           &cleanup_result);
    if (cleanup_rc != 0 || cleanup_result != 0) {
        g_managed_pager_admission.cleanup_munmap_failed++;
        fprintf(stderr,
                "pdocker-direct-managed-pager: fail-closed cleanup munmap failed pid=%d base=0x%llx length=%llu rc=%d result=%lld errno=%d\n",
                (int)pid, result, len, cleanup_rc, (long long)cleanup_result, errno);
    }
    g_managed_pager_admission.denied_enomem++;
    g_memory_stats.denied++;
    g_memory_stats.last_denied_bytes = len;
    regs->regs[0] = (unsigned long long)-ENOMEM;
    if (set_regs(pid, regs) != 0) {
        fprintf(stderr,
                "pdocker-direct-managed-pager: fail-closed ENOMEM setregs failed pid=%d base=0x%llx length=%llu errno=%d\n",
                (int)pid, result, len, errno);
    }
    record_managed_pager_admission("denied", "register-failed",
                                   "allocation_denied_enomem", len,
                                   g_managed_memory_pager_min_request,
                                   g_managed_memory_pager_max_region,
                                   saved_errno);
    fprintf(stderr,
            "pdocker-direct-managed-pager: register failed pid=%d base=0x%llx length=%llu denied=-ENOMEM classification=allocation_denied_enomem errno=%d cleanup_rc=%d cleanup_result=%lld\n",
            (int)pid, result, len, saved_errno, cleanup_rc, (long long)cleanup_result);
}

static int run_memory_pager_transparent_poc(void) {
    int failures = 0;
    long ps = sysconf(_SC_PAGESIZE);
    size_t page_size = ps > 0 ? (size_t)ps : 4096u;
    size_t pages = (size_t)env_u64_or_default("PDOCKER_MEMORY_PAGER_POC_PAGES", 32ULL);
    size_t resident_limit = (size_t)env_u64_or_default("PDOCKER_MEMORY_PAGER_POC_RESIDENT_PAGES", 4ULL);
    if (pages == 0) pages = 1;
    if (resident_limit == 0) resident_limit = 1;
    if (resident_limit > pages) resident_limit = pages;
    size_t length = page_size * pages;
    unsigned long long start_ns = monotonic_now_ns();

    pid_t child = fork();
    if (child == 0) {
        if (ptrace(PTRACE_TRACEME, 0, NULL, NULL) != 0) _exit(81);
        raise(SIGSTOP);
        volatile unsigned char *p = (volatile unsigned char *)mmap(
                NULL, length, PROT_READ | PROT_WRITE,
                MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (p == MAP_FAILED) _exit(82);
        for (size_t i = 0; i < pages; ++i) {
            p[i * page_size] = (unsigned char)(0x11u + (i & 0x7fu));
            p[i * page_size + page_size - 1u] = (unsigned char)(0x81u + (i & 0x7fu));
        }
        for (size_t round = 0; round < 3; ++round) {
            for (size_t i = pages; i > 0; --i) {
                size_t idx = i - 1u;
                if (p[idx * page_size] != (unsigned char)(0x11u + (idx & 0x7fu))) _exit(83);
                if (p[idx * page_size + page_size - 1u] !=
                        (unsigned char)(0x81u + (idx & 0x7fu))) _exit(84);
            }
        }
        munmap((void *)p, length);
        _exit(0);
    }
    if (child < 0) {
        printf("pager-transparent-poc:fork=fail errno=%d\n", errno);
        return 1;
    }

    TraceeState state;
    memset(&state, 0, sizeof(state));
    state.pid = child;
    state.active = 1;
    state.last_nr = -1;
    state.emulated_nr = -1;
    state.last_emulated_nr = -1;
    snprintf(state.guest_cwd, sizeof(state.guest_cwd), "/");

    int saved_managed = g_managed_memory_pager;
    int saved_trace_memory = g_trace_memory;
    unsigned long long saved_min_request = g_managed_memory_pager_min_request;
    unsigned long long saved_max_region = g_managed_memory_pager_max_region;
    unsigned long long saved_resident_pages = g_managed_memory_pager_resident_pages;
    g_managed_memory_pager = 1;
    g_trace_memory = 1;
    g_managed_memory_pager_min_request = length;
    g_managed_memory_pager_max_region = length;
    g_managed_memory_pager_resident_pages = resident_limit;

    int status = 0;
    pid_t waited = waitpid(child, &status, 0);
    failures += pager_poc_ok("transparent_initial_stop",
                             waited == child && WIFSTOPPED(status) && WSTOPSIG(status) == SIGSTOP,
                             waited < 0 ? errno : EINVAL);
    if (!failures) failures += pager_poc_ok("transparent_setopts", set_trace_options(child) == 0, errno);
    if (!failures && ptrace(PTRACE_SYSCALL, child, NULL, NULL) != 0) {
        failures += pager_poc_ok("transparent_start", 0, errno);
    }

    int exit_rc = 255;
    int events = 0;
    int mmap_entries = 0;
    int mmap_exits = 0;
    unsigned long long last_mmap_len = 0;
    unsigned long long last_mmap_prot = 0;
    unsigned long long last_mmap_flags = 0;
    unsigned long long last_mmap_fd = 0;
    unsigned long long last_mmap_result = 0;
    unsigned long long pending_after_entry = 0;
    int sigsegv_stops = 0;
    int trap_good_stops = 0;
    int plain_trap_stops = 0;
    while (!failures) {
        waited = waitpid(child, &status, __WALL);
        if (waited < 0) {
            failures += pager_poc_ok("transparent_wait", 0, errno);
            break;
        }
        if (WIFEXITED(status)) {
            exit_rc = WEXITSTATUS(status);
            break;
        }
        if (WIFSIGNALED(status)) {
            exit_rc = 128 + WTERMSIG(status);
            break;
        }
        if (!WIFSTOPPED(status)) continue;
        int sig = WSTOPSIG(status);
        unsigned int event = (unsigned int)status >> 16;
        events++;
        if (sig == SIGSEGV) {
            sigsegv_stops++;
            siginfo_t info;
            struct user_pt_regs fault_regs;
            memset(&info, 0, sizeof(info));
            memset(&fault_regs, 0, sizeof(fault_regs));
            if (ptrace(PTRACE_GETSIGINFO, child, NULL, &info) != 0 ||
                    get_regs(child, &fault_regs) != 0) {
                failures += pager_poc_ok("transparent_fault_regs", 0, errno);
                break;
            }
            int handled = handle_managed_memory_fault(
                    child, &state, &fault_regs,
                    (unsigned long long)(uintptr_t)info.si_addr);
            if (handled <= 0) {
                failures += pager_poc_ok("transparent_fault_handled", 0, errno ? errno : EINVAL);
                break;
            }
            if (ptrace(PTRACE_SYSCALL, child, NULL, NULL) != 0) {
                failures += pager_poc_ok("transparent_continue_fault", 0, errno);
                break;
            }
            continue;
        }
        int syscall_stop = (sig == (SIGTRAP | 0x80)) || (sig == SIGTRAP && event == 0);
        if (syscall_stop) {
            trap_good_stops++;
            struct user_pt_regs regs;
            if (get_regs(child, &regs) != 0) {
                failures += pager_poc_ok("transparent_syscall_regs", 0, errno);
                break;
            }
            int completed_in_userland = 0;
            if (!state.in_syscall) {
                handle_syscall_entry(child, &regs, &state, "/", "", "", events,
                                     &completed_in_userland);
                if (state.last_nr == 222) {
                    mmap_entries++;
                    last_mmap_len = state.last_args[1];
                    last_mmap_prot = state.last_args[2];
                    last_mmap_flags = state.last_args[3];
                    last_mmap_fd = state.last_args[4];
                    pending_after_entry = state.managed_pending_len;
                }
            } else {
                if (state.in_syscall && is_memory_trace_syscall(state.last_nr)) {
                    record_memory_syscall_exit(&state, regs.regs[0]);
                    if (state.last_nr == 222) {
                        mmap_exits++;
                        last_mmap_result = regs.regs[0];
                        maybe_finish_managed_mmap(child, &regs, &state, regs.regs[0]);
                    }
                }
            }
            state.in_syscall = completed_in_userland ? 1 : !state.in_syscall;
            if (ptrace(PTRACE_SYSCALL, child, NULL, NULL) != 0) {
                failures += pager_poc_ok("transparent_continue_syscall", 0, errno);
                break;
            }
            continue;
        }
        if (sig == SIGTRAP) {
            plain_trap_stops++;
            if (ptrace(PTRACE_SYSCALL, child, NULL, NULL) != 0) {
                failures += pager_poc_ok("transparent_continue_trap", 0, errno);
                break;
            }
            continue;
        }
        if (ptrace(PTRACE_SYSCALL, child, NULL, (void *)(long)sig) != 0) {
            failures += pager_poc_ok("transparent_continue_signal", 0, errno);
            break;
        }
    }

    ManagedTraceRegion *region = state.managed_regions ? &state.managed_regions[0] : NULL;
    printf("pager-transparent-poc:exit_rc=%d\n", exit_rc);
    printf("pager-transparent-poc:events=%d\n", events);
    printf("pager-transparent-poc:mmap_entries=%d\n", mmap_entries);
    printf("pager-transparent-poc:mmap_exits=%d\n", mmap_exits);
    printf("pager-transparent-poc:last_mmap_len=%llu\n", last_mmap_len);
    printf("pager-transparent-poc:last_mmap_prot=%llu\n", last_mmap_prot);
    printf("pager-transparent-poc:last_mmap_flags=%llu\n", last_mmap_flags);
    printf("pager-transparent-poc:last_mmap_fd=%llu\n", last_mmap_fd);
    printf("pager-transparent-poc:last_mmap_result=%llu\n", last_mmap_result);
    printf("pager-transparent-poc:pending_after_entry=%llu\n", pending_after_entry);
    printf("pager-transparent-poc:sigsegv_stops=%d\n", sigsegv_stops);
    printf("pager-transparent-poc:trap_good_stops=%d\n", trap_good_stops);
    printf("pager-transparent-poc:plain_trap_stops=%d\n", plain_trap_stops);
    printf("pager-transparent-poc:registered=%s\n", region && region->active ? "yes" : "no");
    printf("pager-transparent-poc:backing_errno=%d\n", g_managed_pager_backing_errno);
    printf("pager-transparent-poc:backing_op=%s\n", g_managed_pager_backing_op);
    printf("pager-transparent-poc:backing_dir=%s\n", g_managed_pager_backing_dir);
    printf("pager-transparent-poc:backing_path=%s\n", g_managed_pager_backing_path);
    printf("pager-transparent-poc:resident_limit_pages=%llu\n",
           (unsigned long long)resident_limit);
    printf("pager-transparent-poc:max_resident_pages=%llu\n",
           (unsigned long long)(region ? region->max_resident_count : 0));
    printf("pager-transparent-poc:page_ins=%llu\n",
           (unsigned long long)(region ? region->page_ins : 0));
    printf("pager-transparent-poc:page_outs=%llu\n",
           (unsigned long long)(region ? region->page_outs : 0));
    printf("pager-transparent-poc:dirty_page_outs=%llu\n",
           (unsigned long long)(region ? region->dirty_page_outs : 0));
    printf("pager-transparent-poc:bytes_in=%llu\n",
           (unsigned long long)(region ? region->bytes_in : 0));
    printf("pager-transparent-poc:bytes_out=%llu\n",
           (unsigned long long)(region ? region->bytes_out : 0));
    printf("pager-transparent-poc:elapsed_ns=%llu\n", monotonic_now_ns() - start_ns);
    if (exit_rc != 0 || !region || !region->active || region->max_resident_count > resident_limit ||
            region->page_ins == 0 || region->page_outs == 0 || region->dirty_page_outs == 0) {
        failures++;
    }
    printf("pager-transparent-poc:result=%s\n", failures ? "fail" : "ok");
    if (failures && exit_rc == 255) {
        kill(child, SIGKILL);
        waitpid(child, NULL, 0);
    }
    free_managed_trace_regions(&state);
    g_managed_memory_pager = saved_managed;
    g_trace_memory = saved_trace_memory;
    g_managed_memory_pager_min_request = saved_min_request;
    g_managed_memory_pager_max_region = saved_max_region;
    g_managed_memory_pager_resident_pages = saved_resident_pages;
    return failures ? 1 : 0;
}

static int should_rewrite_path(const char *rootfs, const char *path) {
    if (!rootfs || !rootfs[0] || !path || path[0] != '/') return 0;
    size_t root_len = strlen(rootfs);
    if (strncmp(path, rootfs, root_len) == 0 &&
        (path[root_len] == '\0' || path[root_len] == '/')) {
        return 0;
    }
    if (strncmp(path, "/proc/", 6) == 0 || strcmp(path, "/proc") == 0) return 0;
    if (strncmp(path, "/dev/", 5) == 0 || strcmp(path, "/dev") == 0) return 0;
    if (strncmp(path, "/sys/", 5) == 0 || strcmp(path, "/sys") == 0) return 0;
    return 1;
}

static int resolve_guest_host_path(const char *rootfs, const char *guest,
                                   char *out, size_t out_len, int *is_bind) {
    if (is_bind) *is_bind = 0;
    if (strcmp(guest, "/proc") == 0 ||
        strncmp(guest, "/proc/", 6) == 0) {
        if (strncmp(guest, "/proc/self/fd", 13) == 0 ||
            strcmp(guest, "/proc/self/exe") == 0 ||
            strcmp(guest, "/proc/thread-self/exe") == 0) {
            return 0;
        }
        const char *suffix = guest + strlen("/proc");
        if (snprintf(out, out_len, "%s/.pdocker-proc%s", rootfs, suffix) >= (int)out_len) {
            return -ENAMETOOLONG;
        }
        if (access(out, F_OK) == 0) return 1;
        return 0;
    }
    if (strcmp(guest, "/dev/tty") == 0 && isatty(STDIN_FILENO)) {
        if (snprintf(out, out_len, "/proc/self/fd/0") >= (int)out_len) {
            return -ENAMETOOLONG;
        }
        return 1;
    }
    if (!should_rewrite_path(rootfs, guest)) return 0;
    int bind_rc = resolve_bind_path(guest, out, out_len);
    if (bind_rc < 0) return bind_rc;
    if (bind_rc > 0) {
        if (is_bind) *is_bind = 1;
        return 1;
    }
    if (snprintf(out, out_len, "%s%s", rootfs, guest) >= (int)out_len) {
        return -ENAMETOOLONG;
    }
    return 1;
}

static int should_skip_unix_socket_rewrite(const char *guest) {
    if (!guest) return 0;
    /* glibc probes nscd aggressively during apt/dpkg. pdocker does not run
     * nscd inside the rootfs, and prefixing the long Android app-data rootfs
     * path often exceeds sockaddr_un.sun_path. Let the original short guest
     * path fail naturally with ENOENT instead of flooding build logs. */
    return strcmp(guest, "/var/run/nscd/socket") == 0 ||
           strcmp(guest, "/run/nscd/socket") == 0;
}

static int path_has_parent_segment(const char *path) {
    if (!path) return 0;
    const char *p = path;
    while (*p) {
        while (*p == '/') p++;
        if (p[0] == '.' && p[1] == '.' && (p[2] == '/' || p[2] == '\0')) {
            return 1;
        }
        while (*p && *p != '/') p++;
    }
    return 0;
}

static int path_parent(char *out, size_t out_len, const char *path) {
    if (!out || out_len == 0 || !path || !path[0]) return -EINVAL;
    char tmp[PATH_MAX];
    if (snprintf(tmp, sizeof(tmp), "%s", path) >= (int)sizeof(tmp)) return -ENAMETOOLONG;
    trim_trailing_slashes(tmp);
    char *slash = strrchr(tmp, '/');
    if (!slash) {
        if (snprintf(out, out_len, ".") >= (int)out_len) return -ENAMETOOLONG;
        return 0;
    }
    if (slash == tmp) {
        slash[1] = '\0';
    } else {
        *slash = '\0';
    }
    if (snprintf(out, out_len, "%s", tmp) >= (int)out_len) return -ENAMETOOLONG;
    return 0;
}

static int normalize_absolute_path_lexical(const char *path, char *out, size_t out_len) {
    if (!path || path[0] != '/' || !out || out_len < 2) return -EINVAL;
    size_t marks[PATH_MAX / 2];
    size_t depth = 0;
    size_t len = 1;
    out[0] = '/';
    out[1] = '\0';

    const char *p = path;
    while (*p) {
        while (*p == '/') p++;
        const char *seg = p;
        while (*p && *p != '/') p++;
        size_t seg_len = (size_t)(p - seg);
        if (seg_len == 0 || (seg_len == 1 && seg[0] == '.')) continue;
        if (seg_len == 2 && seg[0] == '.' && seg[1] == '.') {
            if (depth > 0) {
                len = marks[--depth];
                out[len] = '\0';
            }
            continue;
        }
        if (depth >= sizeof(marks) / sizeof(marks[0])) return -ENAMETOOLONG;
        marks[depth++] = len;
        if (len > 1) {
            if (len + 1 >= out_len) return -ENAMETOOLONG;
            out[len++] = '/';
        }
        if (len + seg_len >= out_len) return -ENAMETOOLONG;
        memcpy(out + len, seg, seg_len);
        len += seg_len;
        out[len] = '\0';
    }
    return 0;
}

static int validate_host_path_under_allowed(const char *rootfs, const char *host_path,
                                            int follow_final) {
    if (g_path_profile) g_path_stats.validate_calls++;
    if (!host_path || !host_path[0]) return -ENOENT;
    char normalized[PATH_MAX];
    unsigned long long t0 = g_path_profile ? monotonic_now_ns() : 0;
    int norm_rc = normalize_absolute_path_lexical(host_path, normalized, sizeof(normalized));
    if (g_path_profile) g_path_stats.validate_lexical_ns += monotonic_now_ns() - t0;
    if (norm_rc < 0) {
        path_validation_cache_put(host_path, follow_final, norm_rc);
        return norm_rc;
    }
    if (strcmp(normalized, "/proc/self/fd/0") == 0 && isatty(STDIN_FILENO)) {
        path_validation_cache_put(host_path, follow_final, 0);
        return 0;
    }
    if (!host_path_is_under_allowed_host_path(rootfs, normalized)) {
        path_validation_cache_put(host_path, follow_final, -EXDEV);
        return -EXDEV;
    }
    int cached_rc = 0;
    if (path_validation_cache_get(host_path, follow_final, &cached_rc)) return cached_rc;

    char resolved_full[PATH_MAX];
    t0 = g_path_profile ? monotonic_now_ns() : 0;
    if (follow_final && cached_realpath(host_path, resolved_full)) {
        if (g_path_profile) g_path_stats.validate_realpath_full_ns += monotonic_now_ns() - t0;
        int rc = host_path_is_under_allowed_host_path(rootfs, resolved_full) ? 0 : -EXDEV;
        path_validation_cache_put(host_path, follow_final, rc);
        return rc;
    }
    if (g_path_profile && follow_final) {
        g_path_stats.validate_realpath_full_ns += monotonic_now_ns() - t0;
    }
    if (follow_final && errno != ENOENT && errno != ENOTDIR) {
        int rc = -errno;
        path_validation_cache_put(host_path, follow_final, rc);
        return rc;
    }

    char probe[PATH_MAX];
    int parent_rc = path_parent(probe, sizeof(probe), host_path);
    if (parent_rc < 0) return parent_rc;
    char resolved_parent[PATH_MAX];
    for (;;) {
        t0 = g_path_profile ? monotonic_now_ns() : 0;
        char *parent_ok = cached_realpath(probe, resolved_parent);
        if (g_path_profile) g_path_stats.validate_parent_realpath_ns += monotonic_now_ns() - t0;
        if (parent_ok) break;
        if (g_path_profile) g_path_stats.validate_parent_loops++;
        if (errno != ENOENT && errno != ENOTDIR) {
            int rc = -errno;
            path_validation_cache_put(host_path, follow_final, rc);
            return rc;
        }
        if (strcmp(probe, "/") == 0 || strcmp(probe, ".") == 0) {
            int rc = -errno;
            path_validation_cache_put(host_path, follow_final, rc);
            return rc;
        }
        parent_rc = path_parent(probe, sizeof(probe), probe);
        if (parent_rc < 0) {
            path_validation_cache_put(host_path, follow_final, parent_rc);
            return parent_rc;
        }
    }
    int rc = host_path_is_under_allowed_host_path(rootfs, resolved_parent) ? 0 : -EXDEV;
    path_validation_cache_put(host_path, follow_final, rc);
    return rc;
}

static int tracee_dirfd_base(pid_t pid, int dirfd, char *out, size_t out_len) {
    char proc_path[64];
    if (dirfd == AT_FDCWD) {
        snprintf(proc_path, sizeof(proc_path), "/proc/%d/cwd", (int)pid);
    } else {
        snprintf(proc_path, sizeof(proc_path), "/proc/%d/fd/%d", (int)pid, dirfd);
    }
    ssize_t n = readlink(proc_path, out, out_len - 1);
    if (n < 0) return -errno;
    out[n] = '\0';
    return 0;
}

static int validate_relative_tracee_path(pid_t pid, int dirfd, const char *path,
                                         const char *rootfs, char *candidate,
                                         size_t candidate_len, int follow_final) {
    char base[PATH_MAX];
    int base_rc = tracee_dirfd_base(pid, dirfd, base, sizeof(base));
    if (base_rc < 0) return base_rc;
    if (!host_path_is_under_allowed_host_path(rootfs, base)) return -EXDEV;
    if (!path || path[0] == '\0') return 0;
    if (snprintf(candidate, candidate_len, "%s/%s", base, path) >= (int)candidate_len) {
        return -ENAMETOOLONG;
    }
    return validate_host_path_under_allowed(rootfs, candidate, follow_final);
}

static int path_context_follows_final_component(const char *context) {
    return strcmp(context, "unlinkat") != 0 &&
           strcmp(context, "symlinkat") != 0 &&
           strcmp(context, "mkdirat") != 0 &&
           strcmp(context, "mknodat") != 0;
}

static int deny_path_syscall(pid_t pid, struct user_pt_regs *regs, const char *context,
                             const char *path, int err) {
    if (err >= 0) err = EXDEV;
    fprintf(stderr, "pdocker-direct-trace: pid=%d deny %s unsafe path=%s (%s)\n",
            (int)pid, context, path ? path : "", strerror(-err));
    if (complete_emulated_syscall(pid, regs, (unsigned long long)err) != 0) return 0;
    return REWRITE_SYSCALL_COMPLETED;
}

static void trace_interesting_path(pid_t pid, const char *context, int arg_index, const char *path) {
    if (!g_trace_paths) return;
    if (!path) return;
    if (strstr(path, "apt-dpkg-install") || strstr(path, "/var/cache/apt/archives/")) {
        fprintf(stderr, "pdocker-direct-path: pid=%d %s arg%d path=%s\n",
                (int)pid, context, arg_index, path);
    }
}

static int rewrite_path_arg_scratch(pid_t pid, struct user_pt_regs *regs, int arg_index,
                                    const char *rootfs, const char *context,
                                    unsigned long long scratch_offset) {
    char original[PATH_MAX];
    char rewritten[PATH_MAX];
    if (read_tracee_string(pid, regs->regs[arg_index], original, sizeof(original)) < 0) {
        return 0;
    }
    trace_interesting_path(pid, context, arg_index, original);
    int bind_path = 0;
    int resolved = resolve_guest_host_path(rootfs, original, rewritten, sizeof(rewritten), &bind_path);
    if (resolved == 0) return 0;
    if (resolved < 0) {
        fprintf(stderr, "pdocker-direct-trace: pid=%d path too long for %s: %s\n",
                (int)pid, context, original);
        return 0;
    }
    int validate_rc = validate_host_path_under_allowed(rootfs, rewritten, 1);
    if (validate_rc < 0) {
        return deny_path_syscall(pid, regs, context, original, validate_rc);
    }
    unsigned long long scratch = (regs->sp - scratch_offset) & ~15ULL;
    if (write_tracee_string(pid, scratch, rewritten) != 0) {
        fprintf(stderr, "pdocker-direct-trace: pid=%d path rewrite failed for %s: %s -> %s (%s)\n",
                (int)pid, context, original, rewritten, strerror(errno));
        return 0;
    }
    regs->regs[arg_index] = scratch;
    TRACE_LOG("pdocker-direct-trace: pid=%d rewrite %s %s -> %s\n",
              (int)pid, context, original, rewritten);
    return 1;
}

static int rewrite_at_path_arg(pid_t pid, struct user_pt_regs *regs, int dirfd_index,
                               int path_index, const char *rootfs, const char *context,
                               unsigned long long scratch_offset) {
    unsigned long long profile_start = g_path_profile ? monotonic_now_ns() : 0;
    if (g_path_profile) g_path_stats.calls++;
#define RETURN_AT_PATH_PROFILE(value_) do { \
    if (g_path_profile) g_path_stats.total_ns += monotonic_now_ns() - profile_start; \
    return (value_); \
} while (0)
    char original[PATH_MAX];
    unsigned long long t0 = g_path_profile ? monotonic_now_ns() : 0;
    if (read_tracee_string(pid, regs->regs[path_index], original, sizeof(original)) < 0) {
        if (g_path_profile) g_path_stats.read_ns += monotonic_now_ns() - t0;
        RETURN_AT_PATH_PROFILE(0);
    }
    if (g_path_profile) g_path_stats.read_ns += monotonic_now_ns() - t0;
    trace_interesting_path(pid, context, path_index, original);

    if (original[0] == '\0') {
        TRACE_LOG("pdocker-direct-trace: pid=%d preserve-empty-path %s\n",
                  (int)pid, context);
        if (g_path_profile) {
            g_path_stats.empty_path++;
            g_path_stats.no_rewrite++;
        }
        RETURN_AT_PATH_PROFILE(0);
    }

    if (original[0] != '/') {
        if (g_path_profile) g_path_stats.relative_path++;
        char candidate[PATH_MAX];
        int follow_final = path_context_follows_final_component(context);
        t0 = g_path_profile ? monotonic_now_ns() : 0;
        int validate_rc = validate_relative_tracee_path(pid, (int)regs->regs[dirfd_index],
                                                        original, rootfs, candidate,
                                                        sizeof(candidate), follow_final);
        if (g_path_profile) g_path_stats.relative_validate_ns += monotonic_now_ns() - t0;
        if (validate_rc < 0) {
            if (g_path_profile) g_path_stats.denied++;
            RETURN_AT_PATH_PROFILE(deny_path_syscall(pid, regs, context, original, validate_rc));
        }
        TRACE_LOG("pdocker-direct-trace: pid=%d validate-relative %s %s -> %s\n",
                  (int)pid, context, original, candidate);
        if (g_path_profile) g_path_stats.no_rewrite++;
        RETURN_AT_PATH_PROFILE(0);
    }

    if (g_path_profile) g_path_stats.absolute_path++;
    char rewritten[PATH_MAX];
    int bind_path = 0;
    t0 = g_path_profile ? monotonic_now_ns() : 0;
    int resolved = resolve_guest_host_path(rootfs, original, rewritten, sizeof(rewritten), &bind_path);
    if (g_path_profile) g_path_stats.resolve_ns += monotonic_now_ns() - t0;
    if (resolved == 0) {
        if (g_path_profile) g_path_stats.no_rewrite++;
        RETURN_AT_PATH_PROFILE(0);
    }
    if (resolved < 0) {
        fprintf(stderr, "pdocker-direct-trace: pid=%d path too long for %s: %s\n",
                (int)pid, context, original);
        if (g_path_profile) g_path_stats.denied++;
        RETURN_AT_PATH_PROFILE(deny_path_syscall(pid, regs, context, original, resolved));
    }

    t0 = g_path_profile ? monotonic_now_ns() : 0;
    int validate_rc = validate_host_path_under_allowed(
        rootfs, rewritten, path_context_follows_final_component(context));
    if (g_path_profile) g_path_stats.validate_ns += monotonic_now_ns() - t0;
    if (validate_rc < 0) {
        if (g_path_profile) g_path_stats.denied++;
        RETURN_AT_PATH_PROFILE(deny_path_syscall(pid, regs, context, original, validate_rc));
    }

    if (!bind_path &&
        g_rootfd_rewrite &&
        g_rootfs_fd >= 0 &&
        original[0] == '/' &&
        original[1] != '\0' &&
        original[1] != '/' &&
        !path_has_parent_segment(original)) {
        regs->regs[dirfd_index] = (unsigned long long)g_rootfs_fd;
        regs->regs[path_index] = regs->regs[path_index] + 1;
        TRACE_LOG("pdocker-direct-trace: pid=%d rootfd-rewrite %s %s -> fd=%d %s\n",
                  (int)pid, context, original, g_rootfs_fd, original + 1);
        if (g_path_profile) {
            g_path_stats.rootfd_rewrite++;
            g_path_stats.rewrote++;
        }
        RETURN_AT_PATH_PROFILE(1);
    }

    unsigned long long scratch = (regs->sp - scratch_offset) & ~15ULL;
    t0 = g_path_profile ? monotonic_now_ns() : 0;
    if (write_tracee_string(pid, scratch, rewritten) != 0) {
        if (g_path_profile) g_path_stats.write_ns += monotonic_now_ns() - t0;
        fprintf(stderr, "pdocker-direct-trace: pid=%d path rewrite failed for %s: %s -> %s (%s)\n",
                (int)pid, context, original, rewritten, strerror(errno));
        RETURN_AT_PATH_PROFILE(0);
    }
    if (g_path_profile) g_path_stats.write_ns += monotonic_now_ns() - t0;
    regs->regs[path_index] = scratch;
    TRACE_LOG("pdocker-direct-trace: pid=%d rewrite %s %s -> %s\n",
              (int)pid, context, original, rewritten);
    if (g_path_profile) g_path_stats.rewrote++;
    RETURN_AT_PATH_PROFILE(1);
#undef RETURN_AT_PATH_PROFILE
}

static int rewrite_path_arg(pid_t pid, struct user_pt_regs *regs, int arg_index,
                            const char *rootfs, const char *context) {
    return rewrite_path_arg_scratch(pid, regs, arg_index, rootfs, context, 8192u);
}

static int rewrite_unix_sockaddr_arg(pid_t pid, struct user_pt_regs *regs,
                                     const char *rootfs, const char *context) {
    unsigned long long addr_ptr = regs->regs[1];
    unsigned long long len = regs->regs[2];
    if (!addr_ptr || len < offsetof(struct sockaddr_un, sun_path) + 1) return 0;
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    size_t to_read = len < sizeof(addr) ? (size_t)len : sizeof(addr);
    struct iovec local = { .iov_base = &addr, .iov_len = to_read };
    struct iovec remote = { .iov_base = (void *)(uintptr_t)addr_ptr, .iov_len = to_read };
    if (pdocker_process_vm_readv(pid, &local, 1, &remote, 1, 0) != (ssize_t)to_read) {
        return 0;
    }
    if (addr.sun_family != AF_UNIX || addr.sun_path[0] != '/') return 0;

    char guest[sizeof(addr.sun_path)];
    size_t max_path = to_read > offsetof(struct sockaddr_un, sun_path)
        ? to_read - offsetof(struct sockaddr_un, sun_path)
        : 0;
    if (max_path == 0) return 0;
    size_t i = 0;
    for (; i + 1 < sizeof(guest) && i < max_path; ++i) {
        guest[i] = addr.sun_path[i];
        if (addr.sun_path[i] == '\0') break;
    }
    guest[sizeof(guest) - 1] = '\0';
    if (guest[0] != '/') return 0;
    if (should_skip_unix_socket_rewrite(guest)) return 0;

    char rewritten[PATH_MAX];
    int bind_path = 0;
    int resolved = resolve_guest_host_path(rootfs, guest, rewritten, sizeof(rewritten), &bind_path);
    if (resolved <= 0) return 0;
    if (strlen(rewritten) >= sizeof(addr.sun_path)) {
        fprintf(stderr, "pdocker-direct-trace: pid=%d %s AF_UNIX path too long: %s -> %s\n",
                (int)pid, context, guest, rewritten);
        return 0;
    }

    struct sockaddr_un rewritten_addr;
    memset(&rewritten_addr, 0, sizeof(rewritten_addr));
    rewritten_addr.sun_family = AF_UNIX;
    snprintf(rewritten_addr.sun_path, sizeof(rewritten_addr.sun_path), "%s", rewritten);
    size_t rewritten_len = offsetof(struct sockaddr_un, sun_path) + strlen(rewritten) + 1;
    if (len >= rewritten_len) {
        size_t write_len = to_read >= rewritten_len ? to_read : rewritten_len;
        if (write_len > sizeof(rewritten_addr)) write_len = sizeof(rewritten_addr);
        if (write_tracee_data(pid, addr_ptr, &rewritten_addr, write_len) == 0) {
            regs->regs[2] = (unsigned long long)rewritten_len;
            TRACE_LOG("pdocker-direct-trace: pid=%d rewrite-in-place %s AF_UNIX %s -> %s\n",
                      (int)pid, context, guest, rewritten);
            return 1;
        }
    }
    unsigned long long scratch = (regs->sp - 8192u) & ~15ULL;
    if (write_tracee_data(pid, scratch, &rewritten_addr, rewritten_len) != 0) {
        fprintf(stderr, "pdocker-direct-trace: pid=%d %s AF_UNIX rewrite failed: %s -> %s (%s)\n",
                (int)pid, context, guest, rewritten, strerror(errno));
        return 0;
    }
    regs->regs[1] = scratch;
    regs->regs[2] = (unsigned long long)rewritten_len;
    TRACE_LOG("pdocker-direct-trace: pid=%d rewrite %s AF_UNIX %s -> %s\n",
              (int)pid, context, guest, rewritten);
    return 1;
}

static void normalize_guest_path(const char *base, const char *path, char *out, size_t out_len) {
    char combined[PATH_MAX];
    if (!path || !path[0]) {
        snprintf(out, out_len, "%s", base && base[0] ? base : "/");
        return;
    }
    if (path[0] == '/') {
        snprintf(combined, sizeof(combined), "%s", path);
    } else {
        snprintf(combined, sizeof(combined), "%s/%s", base && base[0] ? base : "/", path);
    }

    char tmp[PATH_MAX];
    snprintf(tmp, sizeof(tmp), "%s", combined);
    char *parts[256];
    int count = 0;
    char *save = NULL;
    for (char *part = strtok_r(tmp, "/", &save); part && count < 256; part = strtok_r(NULL, "/", &save)) {
        if (strcmp(part, ".") == 0 || part[0] == '\0') {
            continue;
        }
        if (strcmp(part, "..") == 0) {
            if (count > 0) count--;
            continue;
        }
        parts[count++] = part;
    }

    size_t pos = 0;
    if (out_len == 0) return;
    out[pos++] = '/';
    for (int i = 0; i < count && pos + 1 < out_len; ++i) {
        if (i > 0 && pos + 1 < out_len) out[pos++] = '/';
        size_t len = strlen(parts[i]);
        if (len > out_len - pos - 1) len = out_len - pos - 1;
        memcpy(out + pos, parts[i], len);
        pos += len;
    }
    out[pos] = '\0';
}

static int rewrite_chdir_arg(pid_t pid, struct user_pt_regs *regs, TraceeState *state,
                             const char *rootfs) {
    char original[PATH_MAX];
    if (read_tracee_string(pid, regs->regs[0], original, sizeof(original)) >= 0 && state) {
        normalize_guest_path(state->guest_cwd, original, state->pending_guest_cwd,
                             sizeof(state->pending_guest_cwd));
    }
    return rewrite_path_arg(pid, regs, 0, rootfs, "chdir");
}

static int rewrite_at_path_args(pid_t pid, struct user_pt_regs *regs,
                                int dirfd_a, int path_a, int dirfd_b, int path_b,
                                const char *rootfs, const char *context) {
    int rewrote = 0;
    int rc = rewrite_at_path_arg(pid, regs, dirfd_a, path_a, rootfs, context, 16384u);
    if (rc == REWRITE_SYSCALL_COMPLETED) return rc;
    rewrote |= rc;
    rc = rewrite_at_path_arg(pid, regs, dirfd_b, path_b, rootfs, context, 8192u);
    if (rc == REWRITE_SYSCALL_COMPLETED) return rc;
    rewrote |= rc;
    return rewrote;
}

static int path_syscall_invalidates_cache(long nr) {
    switch (nr) {
        case 33:  /* mknodat */
        case 35:  /* unlinkat */
        case 36:  /* symlinkat */
        case 37:  /* linkat */
        case 38:  /* renameat */
        case 276: /* renameat2 */
            return 1;
        default:
            return 0;
    }
}

static int emulate_getcwd(pid_t pid, struct user_pt_regs *regs, TraceeState *state,
                          const char *rootfs, unsigned long long *result) {
    unsigned long long buf = regs->regs[0];
    unsigned long long size = regs->regs[1];
    if (!buf || size == 0) {
        *result = (unsigned long long)-EINVAL;
        return 1;
    }
    const char *guest = "/";
    if (state && state->guest_cwd[0]) {
        guest = state->guest_cwd;
    } else {
        char proc_cwd[128];
        char host_cwd[PATH_MAX];
        snprintf(proc_cwd, sizeof(proc_cwd), "/proc/%d/cwd", (int)pid);
        ssize_t n = readlink(proc_cwd, host_cwd, sizeof(host_cwd) - 1);
        if (n < 0) {
            *result = (unsigned long long)-errno;
            return 1;
        }
        host_cwd[n] = '\0';
        size_t root_len = strlen(rootfs);
        if (strncmp(host_cwd, rootfs, root_len) == 0 &&
            (host_cwd[root_len] == '\0' || host_cwd[root_len] == '/')) {
            guest = host_cwd + root_len;
            if (!guest[0]) guest = "/";
        }
    }
    size_t need = strlen(guest) + 1;
    if (need > size) {
        *result = (unsigned long long)-ERANGE;
        return 1;
    }
    if (write_tracee_data(pid, buf, guest, need) != 0) {
        *result = (unsigned long long)-errno;
        return 1;
    }
    *result = (unsigned long long)need;
    return 1;
}

static int emulate_proc_self_exe_readlinkat(pid_t pid, struct user_pt_regs *regs,
                                            TraceeState *state,
                                            unsigned long long *result) {
    if (!state || !state->exec_guest_path[0]) return 0;
    char path[PATH_MAX];
    if (read_tracee_string(pid, regs->regs[1], path, sizeof(path)) < 0) return 0;
    int is_proc_pid_exe = 0;
    if (strncmp(path, "/proc/", 6) == 0) {
        const char *rest = path + 6;
        while (*rest >= '0' && *rest <= '9') rest++;
        is_proc_pid_exe = strcmp(rest, "/exe") == 0;
    }
    if (strcmp(path, "/proc/self/exe") != 0 &&
        strcmp(path, "/proc/thread-self/exe") != 0 &&
        !is_proc_pid_exe) {
        return 0;
    }
    unsigned long long buf = regs->regs[2];
    unsigned long long size = regs->regs[3];
    if (!buf || size == 0) {
        *result = (unsigned long long)-EINVAL;
        return 1;
    }
    size_t len = strlen(state->exec_guest_path);
    if (len > size) len = (size_t)size;
    if (write_tracee_data(pid, buf, state->exec_guest_path, len) != 0) {
        *result = (unsigned long long)-errno;
        return 1;
    }
    *result = (unsigned long long)len;
    return 1;
}

static int copy_file_for_linkat(const char *old_host, const char *new_host, int flags) {
    if (flags & ~AT_SYMLINK_FOLLOW) return -EINVAL;

    struct stat st;
    if (stat(old_host, &st) != 0) return -errno;
    if (!S_ISREG(st.st_mode)) return -EXDEV;

    int src = open(old_host, O_RDONLY | O_CLOEXEC);
    if (src < 0) return -errno;

    int dst = open(new_host, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, st.st_mode & 0777);
    if (dst < 0 && errno == EEXIST && strstr(new_host, "/var/lib/dpkg/status-old")) {
        unlink(new_host);
        dst = open(new_host, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, st.st_mode & 0777);
    }
    if (dst < 0) {
        int rc = -errno;
        close(src);
        return rc;
    }

    char buf[65536];
    int rc = 0;
    while (1) {
        ssize_t n = read(src, buf, sizeof(buf));
        if (n == 0) break;
        if (n < 0) {
            rc = -errno;
            break;
        }
        char *p = buf;
        ssize_t left = n;
        while (left > 0) {
            ssize_t w = write(dst, p, (size_t)left);
            if (w < 0) {
                rc = -errno;
                break;
            }
            p += w;
            left -= w;
        }
        if (rc != 0) break;
    }

    if (rc == 0 && fchmod(dst, st.st_mode & 07777) != 0) rc = -errno;
    if (close(dst) != 0 && rc == 0) rc = -errno;
    close(src);
    if (rc != 0) unlink(new_host);
    return rc;
}

static int host_path_is_under_rootfs(const char *rootfs, const char *path) {
    return host_path_is_under_prefix(rootfs, path);
}

static int resolve_tracee_host_path(pid_t pid, int dirfd, unsigned long long path_addr,
                                    const char *rootfs, char *out, size_t out_len,
                                    char *guest_out, size_t guest_len) {
    char guest[PATH_MAX];
    if (read_tracee_string(pid, path_addr, guest, sizeof(guest)) < 0) return 0;
    if (guest_out && guest_len > 0) {
        snprintf(guest_out, guest_len, "%s", guest);
    }
    if (guest[0] == '\0') return -ENOENT;

    if (guest[0] == '/') {
        return resolve_guest_host_path(rootfs, guest, out, out_len, NULL);
    }

    char proc_path[64];
    if (dirfd == AT_FDCWD) {
        snprintf(proc_path, sizeof(proc_path), "/proc/%d/cwd", (int)pid);
    } else {
        snprintf(proc_path, sizeof(proc_path), "/proc/%d/fd/%d", (int)pid, dirfd);
    }

    char base[PATH_MAX];
    ssize_t n = readlink(proc_path, base, sizeof(base) - 1);
    if (n < 0) return 0;
    base[n] = '\0';
    if (!host_path_is_under_rootfs(rootfs, base)) return 0;
    if (snprintf(out, out_len, "%s/%s", base, guest) >= (int)out_len) return -ENAMETOOLONG;
    return 1;
}

static int emulate_linkat_copy(pid_t pid, struct user_pt_regs *regs, const char *rootfs,
                               unsigned long long *result) {
    char old_host[PATH_MAX];
    char new_host[PATH_MAX];
    char old_guest[PATH_MAX];
    char new_guest[PATH_MAX];
    old_guest[0] = '\0';
    new_guest[0] = '\0';

    int old_rc = resolve_tracee_host_path(pid, (int)regs->regs[0], regs->regs[1],
                                          rootfs, old_host, sizeof(old_host),
                                          old_guest, sizeof(old_guest));
    int new_rc = resolve_tracee_host_path(pid, (int)regs->regs[2], regs->regs[3],
                                          rootfs, new_host, sizeof(new_host),
                                          new_guest, sizeof(new_guest));
    if (g_trace_linkat) {
        fprintf(stderr,
                "pdocker-direct-linkat: pid=%d olddir=%d old=%s rc=%d -> %s newdir=%d new=%s rc=%d -> %s flags=%llu\n",
                (int)pid, (int)regs->regs[0], old_guest, old_rc, old_rc > 0 ? old_host : "",
                (int)regs->regs[2], new_guest, new_rc, new_rc > 0 ? new_host : "",
                (unsigned long long)regs->regs[4]);
    }
    if (old_rc == 0 || new_rc == 0) return 0;
    if (old_rc < 0 || new_rc < 0) {
        *result = (unsigned long long)((old_rc < 0) ? old_rc : new_rc);
        return 1;
    }

    int rc = copy_file_for_linkat(old_host, new_host, (int)regs->regs[4]);
    *result = (unsigned long long)rc;
    if (g_trace_linkat) {
        fprintf(stderr, "pdocker-direct-linkat: copy rc=%d %s -> %s\n", rc, old_host, new_host);
    }
    TRACE_LOG("pdocker-direct-trace: pid=%d emulate linkat copy %s -> %s rc=%d\n",
              (int)pid, old_host, new_host, rc);
    return 1;
}

static int emulate_runtime_tmp_symlinkat(pid_t pid, struct user_pt_regs *regs, const char *rootfs,
                                         unsigned long long *result) {
    char target_guest[PATH_MAX];
    char link_guest[PATH_MAX];
    char target_host[PATH_MAX];
    char link_host[PATH_MAX];

    if (read_tracee_string(pid, regs->regs[0], target_guest, sizeof(target_guest)) < 0) return 0;
    if (read_tracee_string(pid, regs->regs[2], link_guest, sizeof(link_guest)) < 0) return 0;
    trace_interesting_path(pid, "symlinkat-target", 0, target_guest);
    trace_interesting_path(pid, "symlinkat-link", 2, link_guest);
    if (target_guest[0] != '/' || !strstr(target_guest, "/var/cache/apt/archives/")) return 0;
    if (!should_rewrite_path(rootfs, target_guest)) return 0;
    if (snprintf(target_host, sizeof(target_host), "%s%s", rootfs, target_guest) >= (int)sizeof(target_host)) {
        *result = (unsigned long long)-ENAMETOOLONG;
        return 1;
    }
    int link_rc = resolve_tracee_host_path(pid, (int)regs->regs[1], regs->regs[2],
                                           rootfs, link_host, sizeof(link_host),
                                           NULL, 0);
    if (link_rc <= 0) {
        if (link_rc < 0) {
            *result = (unsigned long long)link_rc;
        } else {
            *result = (unsigned long long)-ENOENT;
        }
        return 1;
    }
    int rc = copy_file_for_linkat(target_host, link_host, 0);
    *result = (unsigned long long)rc;
    if (g_trace_paths) {
        fprintf(stderr, "pdocker-direct-symlinkat: apt tmp copy %s -> %s result=%lld\n",
                link_host, target_host, (long long)*result);
    }
    return 1;
}

static int emulate_faccessat_path(pid_t pid, struct user_pt_regs *regs, const char *rootfs,
                                  unsigned long long *result) {
    char host[PATH_MAX];
    int rc = resolve_tracee_host_path(pid, (int)regs->regs[0], regs->regs[1],
                                      rootfs, host, sizeof(host), NULL, 0);
    if (rc == 0) {
        char original[PATH_MAX];
        if (read_tracee_string(pid, regs->regs[1], original, sizeof(original)) < 0) {
            *result = (unsigned long long)-EFAULT;
            return 1;
        }
        if (original[0] == '/') {
            snprintf(host, sizeof(host), "%s", original);
        } else {
            *result = (unsigned long long)-ENOENT;
            return 1;
        }
    } else if (rc < 0) {
        *result = (unsigned long long)rc;
        return 1;
    }
    if (access(host, (int)regs->regs[2]) == 0) {
        *result = 0;
    } else {
        *result = (unsigned long long)-errno;
    }
    TRACE_LOG("pdocker-direct-trace: emulate faccess path=%s mode=%lld result=%lld\n",
              host, (long long)regs->regs[2], (long long)*result);
    return 1;
}

static int should_skip_ldconfig(const char *path) {
    if (getenv("PDOCKER_DIRECT_LDCONFIG_REAL")) return 0;
    if (!path) return 0;
    const char *base = strrchr(path, '/');
    base = base ? base + 1 : path;
    return strcmp(base, "ldconfig") == 0 || strcmp(base, "ldconfig.real") == 0;
}

static int basename_is(const char *path, const char *name) {
    if (!path || !name) return 0;
    const char *base = strrchr(path, '/');
    base = base ? base + 1 : path;
    return strcmp(base, name) == 0;
}

static void guest_exec_path(const char *rootfs, const char *path, char *out, size_t out_len) {
    const char *guest = path;
    size_t root_len = strlen(rootfs);
    if (strncmp(path, rootfs, root_len) == 0 && (path[root_len] == '\0' || path[root_len] == '/')) {
        guest = path + root_len;
        if (!guest[0]) guest = "/";
    }
    snprintf(out, out_len, "%s", guest);
}

static int group_exists_in_rootfs(const char *rootfs, const char *group_name) {
    char path[PATH_MAX];
    if (snprintf(path, sizeof(path), "%s/etc/group", rootfs) >= (int)sizeof(path)) return 0;
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    char line[1024];
    size_t name_len = strlen(group_name);
    int found = 0;
    while (fgets(line, sizeof(line), f)) {
        if (strncmp(line, group_name, name_len) == 0 && line[name_len] == ':') {
            found = 1;
            break;
        }
    }
    fclose(f);
    return found;
}

static void append_group_to_rootfs(const char *rootfs, const char *group_name, const char *gid) {
    if (!group_name || !group_name[0] || group_exists_in_rootfs(rootfs, group_name)) return;
    char path[PATH_MAX];
    if (snprintf(path, sizeof(path), "%s/etc/group", rootfs) < (int)sizeof(path)) {
        FILE *f = fopen(path, "a");
        if (f) {
            fprintf(f, "%s:x:%s:\n", group_name, gid && gid[0] ? gid : "999");
            fclose(f);
        }
    }
    if (snprintf(path, sizeof(path), "%s/etc/gshadow", rootfs) < (int)sizeof(path)) {
        FILE *f = fopen(path, "a");
        if (f) {
            fprintf(f, "%s:!::\n", group_name);
            fclose(f);
        }
    }
}

static int emulate_groupadd_from_argv(pid_t pid, const char *rootfs,
                                      unsigned long long *argv_ptrs, int argc) {
    char gid[64] = "999";
    char group_name[256] = "";
    for (int i = 1; i < argc; ++i) {
        char arg[PATH_MAX];
        if (read_tracee_string(pid, argv_ptrs[i], arg, sizeof(arg)) < 0) continue;
        if (strcmp(arg, "-g") == 0 && i + 1 < argc) {
            char next[64];
            if (read_tracee_string(pid, argv_ptrs[i + 1], next, sizeof(next)) >= 0) {
                snprintf(gid, sizeof(gid), "%s", next);
            }
            i++;
            continue;
        }
        if (arg[0] != '-') {
            snprintf(group_name, sizeof(group_name), "%s", arg);
        }
    }
    if (!group_name[0]) return 0;
    append_group_to_rootfs(rootfs, group_name, gid);
    TRACE_LOG("pdocker-direct-trace: emulate groupadd %s gid=%s\n", group_name, gid);
    return 1;
}

static int relative_path_between(const char *from_dir, const char *to_path, char *out, size_t out_len) {
    size_t common = 0;
    size_t last_slash = 0;
    while (from_dir[common] && to_path[common] && from_dir[common] == to_path[common]) {
        if (from_dir[common] == '/') last_slash = common;
        common++;
    }
    if (from_dir[common] == '\0' && (to_path[common] == '/' || to_path[common] == '\0')) {
        last_slash = common;
    }
    if (last_slash == 0) return -1;

    char tmp[PATH_MAX * 2];
    tmp[0] = '\0';
    const char *rest_from = from_dir + last_slash;
    while (*rest_from == '/') rest_from++;
    for (const char *p = rest_from; *p; ) {
        while (*p == '/') p++;
        if (!*p) break;
        strncat(tmp, "../", sizeof(tmp) - strlen(tmp) - 1);
        while (*p && *p != '/') p++;
    }
    const char *rest_to = to_path + last_slash;
    while (*rest_to == '/') rest_to++;
    if (!tmp[0] && !*rest_to) {
        snprintf(tmp, sizeof(tmp), ".");
    } else {
        strncat(tmp, rest_to, sizeof(tmp) - strlen(tmp) - 1);
    }
    if (strlen(tmp) + 1 > out_len) return -1;
    snprintf(out, out_len, "%s", tmp);
    return 0;
}

static void normalize_absolute_symlinks_recursive(const char *rootfs, const char *dir) {
    DIR *d = opendir(dir);
    if (!d) return;
    struct dirent *ent;
    while ((ent = readdir(d)) != NULL) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) continue;
        char path[PATH_MAX];
        if (snprintf(path, sizeof(path), "%s/%s", dir, ent->d_name) >= (int)sizeof(path)) continue;
        struct stat st;
        if (lstat(path, &st) != 0) continue;
        if (S_ISLNK(st.st_mode)) {
            char target[PATH_MAX];
            ssize_t n = readlink(path, target, sizeof(target) - 1);
            if (n <= 0) continue;
            target[n] = '\0';
            if (target[0] != '/' || !should_rewrite_path(rootfs, target)) continue;

            char host_target[PATH_MAX];
            if (snprintf(host_target, sizeof(host_target), "%s%s", rootfs, target) >= (int)sizeof(host_target)) {
                continue;
            }
            char link_dir[PATH_MAX];
            snprintf(link_dir, sizeof(link_dir), "%s", path);
            char *slash = strrchr(link_dir, '/');
            if (!slash) continue;
            *slash = '\0';

            char rel[PATH_MAX];
            if (relative_path_between(link_dir, host_target, rel, sizeof(rel)) != 0) continue;
            if (unlink(path) == 0) {
                if (symlink(rel, path) != 0) {
                    symlink(target, path);
                } else {
                    TRACE_LOG("pdocker-direct-trace: normalized symlink %s -> %s\n", path, rel);
                }
            }
        } else if (S_ISDIR(st.st_mode)) {
            normalize_absolute_symlinks_recursive(rootfs, path);
        }
    }
    closedir(d);
}

static void normalize_absolute_symlinks_once(const char *rootfs) {
    const char *mode = getenv("PDOCKER_DIRECT_NORMALIZE_SYMLINKS");
    if (mode && strcmp(mode, "never") == 0) return;

    char marker[PATH_MAX];
    if (snprintf(marker, sizeof(marker), "%s/.pdocker-absolute-symlinks-normalized", rootfs) >=
        (int)sizeof(marker)) {
        normalize_absolute_symlinks_recursive(rootfs, rootfs);
        return;
    }
    if (!mode || strcmp(mode, "always") != 0) {
        if (access(marker, F_OK) == 0) return;
    }
    normalize_absolute_symlinks_recursive(rootfs, rootfs);
    int fd = open(marker, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd >= 0) {
        const char msg[] = "normalized=1\n";
        (void)write(fd, msg, sizeof(msg) - 1);
        close(fd);
    }
}

static int rewrite_execve_arg(pid_t pid, struct user_pt_regs *regs, TraceeState *state,
                              const char *rootfs, const char *loader, const char *libpath) {
    char original[PATH_MAX];
    char target[PATH_MAX];
    if (read_tracee_string(pid, regs->regs[0], original, sizeof(original)) < 0) {
        if (g_trace_exec) {
            fprintf(stderr, "pdocker-direct-exec: pid=%d read exec path failed addr=%llx\n",
                    (int)pid, (unsigned long long)regs->regs[0]);
        }
        return 0;
    }
    if (strcmp(original, loader) == 0) {
        return 0;
    }
    char guest_override[PATH_MAX];
    guest_override[0] = '\0';
    int bind_guest_rc = bind_host_to_guest_path(original, guest_override, sizeof(guest_override));
    if (bind_guest_rc < 0) {
        fprintf(stderr, "pdocker-direct-trace: pid=%d execve bind guest path too long: %s\n",
                (int)pid, original);
        return 0;
    }
    if (bind_guest_rc > 0) {
        snprintf(target, sizeof(target), "%s", original);
    } else if (strncmp(original, rootfs, strlen(rootfs)) == 0) {
        snprintf(target, sizeof(target), "%s", original);
    } else if (original[0] != '/') {
        int validate_rc = validate_relative_tracee_path(pid, AT_FDCWD, original, rootfs,
                                                        target, sizeof(target), 1);
        if (validate_rc < 0) {
            if (g_trace_exec) {
                fprintf(stderr, "pdocker-direct-exec: pid=%d relative exec unsafe %s: %s\n",
                        (int)pid, original, strerror(-validate_rc));
            }
            return 0;
        }
    } else if (should_rewrite_path(rootfs, original)) {
        if (snprintf(target, sizeof(target), "%s%s", rootfs, original) >= (int)sizeof(target)) {
            fprintf(stderr, "pdocker-direct-trace: pid=%d execve target too long: %s\n",
                    (int)pid, original);
            return 0;
        }
    } else {
        return 0;
    }
    if (access(target, F_OK) != 0) {
        if (g_trace_exec) {
            fprintf(stderr, "pdocker-direct-exec: pid=%d target missing %s -> %s: %s\n",
                    (int)pid, original, target, strerror(errno));
        }
        return 0;
    }
    if (should_skip_ldconfig(target)) {
        char true_path[PATH_MAX];
        if (resolve_guest_program(rootfs, "true", true_path, sizeof(true_path)) == 0) {
            TRACE_LOG("pdocker-direct-trace: pid=%d replace ldconfig exec %s -> %s\n",
                      (int)pid, target, true_path);
            snprintf(target, sizeof(target), "%s", true_path);
        }
    }
    int is_script = file_starts_with(target, "#!");
    char program[PATH_MAX];
    char program_argv0[PATH_MAX];
    char script_interp_arg[PATH_MAX];
    int has_script_interp_arg = 0;
    program[0] = '\0';
    program_argv0[0] = '\0';
    script_interp_arg[0] = '\0';
    if (is_script) {
        char interp[PATH_MAX];
        char interp_arg[PATH_MAX];
        if (parse_shebang(target, interp, sizeof(interp), interp_arg, sizeof(interp_arg)) == 0 &&
            resolve_guest_program(rootfs, interp, program, sizeof(program)) == 0) {
            snprintf(program_argv0, sizeof(program_argv0), "%s", interp);
            if (interp_arg[0]) {
                snprintf(script_interp_arg, sizeof(script_interp_arg), "%s", interp_arg);
                has_script_interp_arg = 1;
            }
        } else if (snprintf(program, sizeof(program), "%s/bin/bash", rootfs) >= (int)sizeof(program) ||
                   access(program, X_OK) != 0) {
            if (snprintf(program, sizeof(program), "%s/bin/sh", rootfs) >= (int)sizeof(program)) {
                return 0;
            }
            snprintf(program_argv0, sizeof(program_argv0), "/bin/sh");
        } else {
            snprintf(program_argv0, sizeof(program_argv0), "/bin/bash");
        }
    }

    if (!is_script && elf_has_interp(target) == 0) {
        unsigned long long scratch_span =
            ((unsigned long long)strlen(target) + 1ULL + EXEC_REWRITE_STACK_SAFETY + 15ULL) & ~15ULL;
        if (scratch_span > EXEC_REWRITE_MAX_SCRATCH || scratch_span >= regs->sp) {
            fprintf(stderr,
                    "pdocker-direct-trace: pid=%d static exec rewrite scratch too large bytes=%llu path=%s\n",
                    (int)pid, scratch_span, original);
            return 0;
        }
        unsigned long long scratch = (regs->sp - scratch_span) & ~15ULL;
        if (write_tracee_string(pid, scratch, target) != 0) return 0;
        regs->regs[0] = scratch;
        if (state) {
            char original_guest[PATH_MAX];
            guest_exec_path(rootfs, original, original_guest, sizeof(original_guest));
            snprintf(state->exec_guest_path, sizeof(state->exec_guest_path), "%s", original_guest);
        }
        TRACE_LOG("pdocker-direct-trace: pid=%d rewrite static execve %s -> %s\n",
                  (int)pid, original, target);
        return 1;
    }

    unsigned long long old_argv = regs->regs[1];
    unsigned long long old_arg_ptrs[EXEC_REWRITE_MAX_ARGC + 1];
    int old_argc = 0;
    for (; old_argc < EXEC_REWRITE_MAX_ARGC; ++old_argc) {
        struct iovec local = {.iov_base = &old_arg_ptrs[old_argc], .iov_len = sizeof(unsigned long long)};
        struct iovec remote = {
            .iov_base = (void *)(uintptr_t)(old_argv + (unsigned long long)old_argc * sizeof(unsigned long long)),
            .iov_len = sizeof(unsigned long long),
        };
        if (pdocker_process_vm_readv(pid, &local, 1, &remote, 1, 0) != (ssize_t)sizeof(unsigned long long)) {
            break;
        }
        if (old_arg_ptrs[old_argc] == 0) break;
    }
    if (old_argc == EXEC_REWRITE_MAX_ARGC) {
        fprintf(stderr, "pdocker-direct-trace: pid=%d execve argv too long to rewrite safely argc>=%d path=%s\n",
                (int)pid, EXEC_REWRITE_MAX_ARGC, original);
        return 0;
    }

    if (basename_is(target, "groupadd") &&
        emulate_groupadd_from_argv(pid, rootfs, old_arg_ptrs, old_argc)) {
        char true_path[PATH_MAX];
        if (resolve_guest_program(rootfs, "true", true_path, sizeof(true_path)) == 0) {
            snprintf(target, sizeof(target), "%s", true_path);
        }
    }

    char old_argv0[PATH_MAX];
    old_argv0[0] = '\0';
    if (old_argc > 0) {
        read_tracee_string(pid, old_arg_ptrs[0], old_argv0, sizeof(old_argv0));
    }
    char original_guest[PATH_MAX];
    if (guest_override[0]) {
        snprintf(original_guest, sizeof(original_guest), "%s", guest_override);
    } else {
        guest_exec_path(rootfs, original, original_guest, sizeof(original_guest));
    }
    const char *argv0_value = old_argv0[0] ? old_argv0 : original_guest;
    if (is_script && program_argv0[0]) argv0_value = program_argv0;

    ExecArgArena copied_arg_arena = {0};
    size_t copied_arg_offsets[EXEC_REWRITE_MAX_ARGC];
    int copied_argc = 0;
    if (old_argc > 1) {
        for (int i = 1; i < old_argc && copied_argc < EXEC_REWRITE_MAX_ARGC; ++i) {
            if (read_tracee_string_to_arena(
                        pid,
                        old_arg_ptrs[i],
                        &copied_arg_arena,
                        &copied_arg_offsets[copied_argc]) != 0) {
                free_exec_arg_arena(&copied_arg_arena);
                return 0;
            }
            copied_argc++;
        }
    }

    unsigned long long string_bytes =
        (unsigned long long)strlen(loader) + 1ULL +
        (unsigned long long)strlen("--library-path") + 1ULL +
        (unsigned long long)strlen(libpath) + 1ULL +
        (unsigned long long)strlen("--argv0") + 1ULL +
        (unsigned long long)strlen(argv0_value) + 1ULL +
        (unsigned long long)strlen(is_script ? program : target) + 1ULL;
    if (is_script) {
        string_bytes += (unsigned long long)strlen(original_guest) + 1ULL;
    }
    if (has_script_interp_arg) {
        string_bytes += (unsigned long long)strlen(script_interp_arg) + 1ULL;
    }
    for (int i = 0; i < copied_argc; ++i) {
        const char *arg = copied_arg_arena.data + copied_arg_offsets[i];
        string_bytes += (unsigned long long)strlen(arg) + 1ULL;
    }
    unsigned long long argv_entries =
        6ULL + (has_script_interp_arg ? 1ULL : 0ULL) + (is_script ? 1ULL : 0ULL) +
        (unsigned long long)copied_argc + 1ULL;
    unsigned long long payload_bytes =
        ((string_bytes + 15ULL) & ~15ULL) + argv_entries * (unsigned long long)sizeof(unsigned long long);
    unsigned long long scratch_span = (payload_bytes + EXEC_REWRITE_STACK_SAFETY + 15ULL) & ~15ULL;
    if (scratch_span > EXEC_REWRITE_MAX_SCRATCH || scratch_span >= regs->sp) {
        fprintf(stderr,
                "pdocker-direct-trace: pid=%d execve argv rewrite scratch too large bytes=%llu argc=%d path=%s\n",
                (int)pid, scratch_span, old_argc, original);
        free_exec_arg_arena(&copied_arg_arena);
        return 0;
    }
    unsigned long long scratch = (regs->sp - scratch_span) & ~15ULL;
    unsigned long long cursor = scratch;
    unsigned long long loader_addr = cursor;
    if (write_tracee_string(pid, loader_addr, loader) != 0) {
        free_exec_arg_arena(&copied_arg_arena);
        return 0;
    }
    cursor += strlen(loader) + 1;
    unsigned long long library_path_flag_addr = cursor;
    if (write_tracee_string(pid, library_path_flag_addr, "--library-path") != 0) {
        free_exec_arg_arena(&copied_arg_arena);
        return 0;
    }
    cursor += strlen("--library-path") + 1;
    unsigned long long libpath_addr = cursor;
    if (write_tracee_string(pid, libpath_addr, libpath) != 0) {
        free_exec_arg_arena(&copied_arg_arena);
        return 0;
    }
    cursor += strlen(libpath) + 1;
    unsigned long long argv0_flag_addr = cursor;
    if (write_tracee_string(pid, argv0_flag_addr, "--argv0") != 0) {
        free_exec_arg_arena(&copied_arg_arena);
        return 0;
    }
    cursor += strlen("--argv0") + 1;
    unsigned long long argv0_addr = cursor;
    if (write_tracee_string(pid, argv0_addr, argv0_value) != 0) {
        free_exec_arg_arena(&copied_arg_arena);
        return 0;
    }
    cursor += strlen(argv0_value) + 1;
    unsigned long long target_addr = cursor;
    if (write_tracee_string(pid, target_addr, is_script ? program : target) != 0) {
        free_exec_arg_arena(&copied_arg_arena);
        return 0;
    }
    cursor += strlen(is_script ? program : target) + 1;
    unsigned long long script_addr = 0;
    if (is_script) {
        script_addr = cursor;
        if (write_tracee_string(pid, script_addr, original_guest) != 0) {
            free_exec_arg_arena(&copied_arg_arena);
            return 0;
        }
        cursor += strlen(original_guest) + 1;
    }
    unsigned long long script_interp_arg_addr = 0;
    if (has_script_interp_arg) {
        script_interp_arg_addr = cursor;
        if (write_tracee_string(pid, script_interp_arg_addr, script_interp_arg) != 0) {
            free_exec_arg_arena(&copied_arg_arena);
            return 0;
        }
        cursor += strlen(script_interp_arg) + 1;
    }
    unsigned long long copied_arg_ptrs[EXEC_REWRITE_MAX_ARGC];
    for (int i = 0; i < copied_argc; ++i) {
        const char *arg = copied_arg_arena.data + copied_arg_offsets[i];
        copied_arg_ptrs[i] = cursor;
        if (write_tracee_string(pid, cursor, arg) != 0) {
            free_exec_arg_arena(&copied_arg_arena);
            return 0;
        }
        cursor += strlen(arg) + 1;
    }
    free_exec_arg_arena(&copied_arg_arena);
    cursor = (cursor + 15u) & ~15ULL;

    unsigned long long new_argv[EXEC_REWRITE_MAX_ARGC + 10];
    int n = 0;
    new_argv[n++] = loader_addr;
    new_argv[n++] = library_path_flag_addr;
    new_argv[n++] = libpath_addr;
    new_argv[n++] = argv0_flag_addr;
    new_argv[n++] = argv0_addr;
    new_argv[n++] = target_addr;
    if (has_script_interp_arg) new_argv[n++] = script_interp_arg_addr;
    if (is_script) new_argv[n++] = script_addr;
    for (int i = 0; i < copied_argc && n < (int)(sizeof(new_argv) / sizeof(new_argv[0]) - 1); ++i) {
        new_argv[n++] = copied_arg_ptrs[i];
    }
    new_argv[n++] = 0;
    if (write_tracee_data(pid, cursor, new_argv, (size_t)n * sizeof(unsigned long long)) != 0) {
        return 0;
    }

    regs->regs[0] = loader_addr;
    regs->regs[1] = cursor;
    if (state) {
        snprintf(state->exec_guest_path, sizeof(state->exec_guest_path), "%s", original_guest);
    }
    if (g_trace_exec) {
        fprintf(stderr, "pdocker-direct-exec: pid=%d rewrite %s -> %s\n",
                (int)pid, original, target);
    }
    TRACE_LOG("pdocker-direct-trace: pid=%d rewrite execve via loader %s -> %s\n",
              (int)pid, original, target);
    return 1;
}

static int rewrite_syscall_paths(pid_t pid, struct user_pt_regs *regs, TraceeState *state, long nr,
                                 const char *rootfs, const char *loader, const char *libpath) {
    switch (nr) {
        case 5:   /* setxattr(path, name, value, size, flags) */
        case 6:   /* lsetxattr(path, name, value, size, flags) */
        case 8:   /* getxattr(path, name, value, size) */
        case 9:   /* lgetxattr(path, name, value, size) */
        case 11:  /* listxattr(path, list, size) */
        case 12:  /* llistxattr(path, list, size) */
        case 14:  /* removexattr(path, name) */
        case 15:  /* lremovexattr(path, name) */
            return rewrite_path_arg(pid, regs, 0, rootfs, syscall_name(nr));
        case 33:  /* mknodat(dirfd, pathname, mode, dev) */
        case 34:  /* mkdirat(dirfd, pathname, mode) */
        case 35:  /* unlinkat(dirfd, pathname, flags) */
        case 48:  /* faccessat(dirfd, pathname, mode) */
        case 53:  /* fchmodat(dirfd, pathname, mode, flags) */
        case 54:  /* fchownat(dirfd, pathname, owner, group, flags) */
        case 56:  /* openat(dirfd, pathname, flags, mode) */
        case 79:  /* newfstatat(dirfd, pathname, statbuf, flags) */
        case 88:  /* utimensat(dirfd, pathname, times, flags) */
        case 291: /* statx(dirfd, pathname, flags, mask, statxbuf) */
        case 437: /* openat2(dirfd, pathname, how, size) */
        case 439: /* faccessat2(dirfd, pathname, mode, flags) */
            return rewrite_at_path_arg(pid, regs, 0, 1, rootfs, syscall_name(nr), 8192u);
        case 78:  /* readlinkat(dirfd, pathname, buf, bufsiz) */
            return rewrite_path_arg(pid, regs, 1, rootfs, syscall_name(nr));
        case 36:  /* symlinkat(target, newdirfd, linkpath) */
            return rewrite_at_path_arg(pid, regs, 1, 2, rootfs, syscall_name(nr), 8192u);
        case 37:  /* linkat(olddirfd, oldpath, newdirfd, newpath, flags) */
            return rewrite_at_path_args(pid, regs, 0, 1, 2, 3, rootfs, syscall_name(nr));
        case 38:  /* renameat(olddirfd, oldpath, newdirfd, newpath) */
        case 276: /* renameat2(olddirfd, oldpath, newdirfd, newpath, flags) */
            return rewrite_at_path_args(pid, regs, 0, 1, 2, 3, rootfs, syscall_name(nr));
        case 43:  /* statfs(pathname, buf) */
            return rewrite_path_arg(pid, regs, 0, rootfs, syscall_name(nr));
        case 49:  /* chdir(pathname) */
            return rewrite_chdir_arg(pid, regs, state, rootfs);
        case 200: /* bind(sockfd, addr, addrlen) */
        case 203: /* connect(sockfd, addr, addrlen) */
            return rewrite_unix_sockaddr_arg(pid, regs, rootfs, syscall_name(nr));
        case 221: /* execve(pathname, argv, envp) */
            return rewrite_execve_arg(pid, regs, state, rootfs, loader, libpath);
        case 281: /* execveat(dirfd, pathname, argv, envp, flags) */
            return rewrite_at_path_arg(pid, regs, 0, 1, rootfs, syscall_name(nr), 8192u);
        default:
            return 0;
    }
}

static unsigned long long memory_request_bytes(long nr, const unsigned long long args[6],
                                               const TraceeState *state) {
    switch (nr) {
        case 222: /* mmap(addr, length, prot, flags, fd, offset) */
            return args[1];
        case 216: /* mremap(old_address, old_size, new_size, flags, new_address) */
            return args[2];
        case 214: /* brk(addr) */
            if (args[0] == 0 || !state || state->last_brk == 0 || args[0] <= state->last_brk) {
                return 0;
            }
            return args[0] - state->last_brk;
        default:
            return 0;
    }
}

static int maybe_guard_memory_syscall(pid_t pid, struct user_pt_regs *regs,
                                      TraceeState *state, int *completed_in_userland) {
    if (!g_memory_guard || !is_memory_trace_syscall(state->last_nr)) return 0;
    unsigned long long requested = memory_request_bytes(state->last_nr, state->last_args, state);
    if (!requested) return 0;
    unsigned long long available = 0;
    unsigned long long swap_free = 0;
    if (!memory_guard_would_deny(requested, &available, &swap_free)) return 0;

    g_memory_stats.denied++;
    g_memory_stats.last_denied_bytes = requested;
    g_memory_stats.last_available = available;
    g_memory_stats.last_swap_free = swap_free;
    g_memory_stats.last_denied_nr = state->last_nr;
    g_memory_stats.last_denied_errno = ENOMEM;
    snprintf(g_memory_stats.last_denied_syscall,
             sizeof(g_memory_stats.last_denied_syscall), "%s",
             syscall_name(state->last_nr));
    if (state->last_nr == 214 && state->last_brk != 0) {
        state->emulated_result = state->last_brk;
    } else {
        state->emulated_result = (unsigned long long)-ENOMEM;
    }
    fprintf(stderr,
            "pdocker-direct-memory: deny pid=%d nr=%ld(%s) requested=%llu available=%llu swap_free=%llu min_available=%llu min_swap=%llu\n",
            (int)pid, state->last_nr, syscall_name(state->last_nr), requested,
            available, swap_free, g_memory_guard_min_available,
            g_memory_guard_min_swap);
    if (complete_emulated_syscall(pid, regs, state->emulated_result) == 0) {
        if (completed_in_userland) *completed_in_userland = 1;
        state->emulated_nr = state->last_nr;
        return 1;
    }
    fprintf(stderr, "pdocker-direct-memory: pid=%d setregs deny failed: %s\n",
            (int)pid, strerror(errno));
    return 0;
}

static void record_memory_syscall_exit(TraceeState *state, unsigned long long result) {
    if (!g_trace_memory || !state || !is_memory_trace_syscall(state->last_nr)) return;
    int failed = syscall_failed_result(result);
    unsigned long long bytes = 0;
    switch (state->last_nr) {
        case 222:
            bytes = state->last_args[1];
            update_memory_stat(&g_memory_stats.mmap_, bytes, failed);
            break;
        case 216:
            bytes = state->last_args[2];
            update_memory_stat(&g_memory_stats.mremap, bytes, failed);
            break;
        case 214:
            if (!failed && result != 0) {
                if (state->last_brk != 0 && result > state->last_brk) {
                    bytes = result - state->last_brk;
                }
                state->last_brk = result;
            }
            update_memory_stat(&g_memory_stats.brk, bytes, failed);
            break;
        case 215:
            bytes = state->last_args[1];
            update_memory_stat(&g_memory_stats.munmap_, bytes, failed);
            break;
        case 226:
            bytes = state->last_args[1];
            update_memory_stat(&g_memory_stats.mprotect_, bytes, failed);
            break;
        case 233:
            bytes = state->last_args[1];
            update_memory_stat(&g_memory_stats.madvise_, bytes, failed);
            break;
        default:
            break;
    }
    if (g_trace_memory_verbose ||
        (!failed && bytes >= g_trace_memory_threshold) ||
        (failed && (state->last_nr == 222 || state->last_nr == 216 || state->last_nr == 214))) {
        fprintf(stderr,
                "pdocker-direct-memory: pid=%d nr=%ld(%s) result=%lld bytes=%llu failed=%d addr=%llx\n",
                (int)state->pid, state->last_nr, syscall_name(state->last_nr),
                (long long)result, bytes, failed, state->last_args[0]);
    }
}

static int handle_syscall_entry(pid_t pid, struct user_pt_regs *regs, TraceeState *state,
                                const char *rootfs, const char *loader, const char *libpath,
                                int events, int *completed_in_userland) {
    if (completed_in_userland) *completed_in_userland = 0;
    state->last_nr = (long)regs->regs[8];
    record_syscall_stat(state->last_nr);
    for (int i = 0; i < 6; ++i) state->last_args[i] = regs->regs[i];
    int forced_emulation = 0;
    int mutation_invalidates_cache = path_syscall_invalidates_cache(state->last_nr);
    if (mutation_invalidates_cache) {
        begin_path_cache_mutation(state);
        state->pending_path_cache_invalidation = 1;
    }
    if (state->last_nr == 222 && maybe_prepare_managed_mmap(pid, regs, state) < 0) {
        fprintf(stderr, "pdocker-direct-managed-pager: prepare mmap failed pid=%d: %s\n",
                (int)pid, strerror(errno));
    }
    if (!state->managed_pending_len &&
            maybe_guard_memory_syscall(pid, regs, state, completed_in_userland)) {
        forced_emulation = 1;
    } else if (state->last_nr == 17 &&
        emulate_getcwd(pid, regs, state, rootfs, &state->emulated_result)) {
        forced_emulation = 1;
        if (complete_emulated_syscall(pid, regs, state->emulated_result) == 0) {
            if (completed_in_userland) *completed_in_userland = 1;
            state->emulated_nr = state->last_nr;
        } else {
            fprintf(stderr, "pdocker-direct-trace: pid=%d setregs getcwd emulation failed: %s\n",
                    (int)pid, strerror(errno));
        }
    } else if (state->last_nr == 78 &&
               emulate_proc_self_exe_readlinkat(pid, regs, state, &state->emulated_result)) {
        forced_emulation = 1;
        if (complete_emulated_syscall(pid, regs, state->emulated_result) == 0) {
            if (completed_in_userland) *completed_in_userland = 1;
            state->emulated_nr = state->last_nr;
        } else {
            fprintf(stderr, "pdocker-direct-trace: pid=%d setregs readlinkat emulation failed: %s\n",
                    (int)pid, strerror(errno));
        }
    } else if (state->last_nr >= 425 && state->last_nr <= 427) {
        forced_emulation = 1;
        state->emulated_result = (unsigned long long)-ENOSYS;
        if (complete_emulated_syscall(pid, regs, state->emulated_result) == 0) {
            if (completed_in_userland) *completed_in_userland = 1;
            state->emulated_nr = state->last_nr;
        } else {
            fprintf(stderr, "pdocker-direct-trace: pid=%d setregs io_uring emulation failed: %s\n",
                    (int)pid, strerror(errno));
        }
    } else if (state->last_nr == 57 && (int)regs->regs[0] == g_rootfs_fd) {
        forced_emulation = 1;
        state->emulated_result = 0;
        if (complete_emulated_syscall(pid, regs, state->emulated_result) == 0) {
            if (completed_in_userland) *completed_in_userland = 1;
            state->emulated_nr = state->last_nr;
        } else {
            fprintf(stderr, "pdocker-direct-trace: pid=%d setregs close(rootfs_fd) emulation failed: %s\n",
                    (int)pid, strerror(errno));
        }
    } else if (state->last_nr == 37 &&
        emulate_linkat_copy(pid, regs, rootfs, &state->emulated_result)) {
        forced_emulation = 1;
        if (complete_emulated_syscall(pid, regs, state->emulated_result) == 0) {
            if (completed_in_userland) *completed_in_userland = 1;
            state->emulated_nr = state->last_nr;
        } else {
            fprintf(stderr, "pdocker-direct-trace: pid=%d setregs linkat emulation failed: %s\n",
                    (int)pid, strerror(errno));
        }
    } else if (state->last_nr == 36 &&
               emulate_runtime_tmp_symlinkat(pid, regs, rootfs, &state->emulated_result)) {
        forced_emulation = 1;
        if (complete_emulated_syscall(pid, regs, state->emulated_result) == 0) {
            if (completed_in_userland) *completed_in_userland = 1;
            state->emulated_nr = state->last_nr;
        } else {
            fprintf(stderr, "pdocker-direct-trace: pid=%d setregs symlinkat emulation failed: %s\n",
                    (int)pid, strerror(errno));
        }
    } else if ((state->last_nr == 48 || state->last_nr == 439) &&
               emulate_faccessat_path(pid, regs, rootfs, &state->emulated_result)) {
        forced_emulation = 1;
        if (complete_emulated_syscall(pid, regs, state->emulated_result) == 0) {
            if (completed_in_userland) *completed_in_userland = 1;
            state->emulated_nr = state->last_nr;
        } else {
            fprintf(stderr, "pdocker-direct-trace: pid=%d setregs faccess emulation failed: %s\n",
                    (int)pid, strerror(errno));
        }
    }
    int rewrote = 0;
    int remapped = 0;
    if (!forced_emulation) {
        rewrote = rewrite_syscall_paths(pid, regs, state, state->last_nr, rootfs, loader, libpath);
        long remapped_nr = syscall_remap_number(state->last_nr);
        remapped = remapped_nr != state->last_nr;
        if (remapped) {
            TRACE_LOG(
                    "pdocker-direct-trace: pid=%d remap nr=%ld(%s) -> %ld(%s)\n",
                    (int)pid, state->last_nr, syscall_name(state->last_nr),
                    remapped_nr, syscall_name(remapped_nr));
            regs->regs[8] = (unsigned long long)remapped_nr;
        }
    }
    int emulated_errno = 0;
    if (!forced_emulation && syscall_emulate_errno(state->last_nr, &emulated_errno)) {
        state->emulated_result = (unsigned long long)-emulated_errno;
        TRACE_LOG(
                "pdocker-direct-trace: pid=%d emulate-errno nr=%ld(%s) errno=%d via skipped syscall\n",
                (int)pid, state->last_nr, syscall_name(state->last_nr), emulated_errno);
        if (complete_emulated_syscall(pid, regs, state->emulated_result) == 0) {
            if (completed_in_userland) *completed_in_userland = 1;
            state->emulated_nr = state->last_nr;
        } else {
            fprintf(stderr, "pdocker-direct-trace: pid=%d setregs errno emulation failed: %s\n",
                    (int)pid, strerror(errno));
        }
    } else if (!forced_emulation && syscall_emulate_success(state->last_nr)) {
        state->emulated_result = prepare_emulated_result(pid, state, state->last_nr);
        TRACE_LOG(
                "pdocker-direct-trace: pid=%d emulate-success nr=%ld(%s) result=%llu via skipped syscall\n",
                (int)pid, state->last_nr, syscall_name(state->last_nr),
                state->emulated_result);
        if (complete_emulated_syscall(pid, regs, state->emulated_result) == 0) {
            if (completed_in_userland) *completed_in_userland = 1;
            state->emulated_nr = state->last_nr;
        } else {
            fprintf(stderr, "pdocker-direct-trace: pid=%d setregs entry failed: %s\n",
                    (int)pid, strerror(errno));
        }
    } else if (rewrote || remapped) {
        if (set_regs(pid, regs) != 0) {
            fprintf(stderr, "pdocker-direct-trace: pid=%d setregs rewrite/remap failed: %s\n",
                    (int)pid, strerror(errno));
        }
    }
    if (mutation_invalidates_cache && completed_in_userland && *completed_in_userland) {
        finish_path_cache_mutation(state);
        state->pending_path_cache_invalidation = 0;
    }
    if (events < 80 || state->last_nr == 221 || state->last_nr == 281 ||
        state->last_nr == 293 || state->last_nr == 439 || state->last_nr == 449) {
        TRACE_LOG(
                "pdocker-direct-trace: pid=%d enter #%d nr=%ld(%s) args=%llx,%llx,%llx,%llx,%llx,%llx\n",
                (int)pid, events, state->last_nr, syscall_name(state->last_nr),
                state->last_args[0], state->last_args[1], state->last_args[2],
                state->last_args[3], state->last_args[4], state->last_args[5]);
    }
    return 0;
}

static int trace_and_exec(char *const exec_argv[], const char *rootfs, const char *libpath) {
#define TRACE_RETURN(rc_) do { print_memory_stats("trace-return", (rc_)); g_trace_child_pgid = -1; return (rc_); } while (0)
    pid_t child = fork();
    if (child < 0) {
        perror("pdocker-direct-executor: fork tracer");
        return 126;
    }
    if (child == 0) {
        setpgid(0, 0);
        if (ptrace(PTRACE_TRACEME, 0, NULL, NULL) != 0) {
            perror("pdocker-direct-executor: PTRACE_TRACEME");
            _exit(126);
        }
        raise(SIGSTOP);
        if (g_selective_trace && install_selective_seccomp_trace_filter() != 0) {
            perror("pdocker-direct-executor: seccomp selective trace");
            _exit(126);
        }
        execve(exec_argv[0], exec_argv, environ);
        perror("pdocker-direct-executor: execve loader");
        _exit(126);
    }
    setpgid(child, child);
    if (isatty(STDIN_FILENO)) {
        struct sigaction ignore_ttou;
        struct sigaction old_ttou;
        memset(&ignore_ttou, 0, sizeof(ignore_ttou));
        ignore_ttou.sa_handler = SIG_IGN;
        sigaction(SIGTTOU, &ignore_ttou, &old_ttou);
        tcsetpgrp(STDIN_FILENO, child);
        sigaction(SIGTTOU, &old_ttou, NULL);
    }
    g_trace_child_pgid = child;
    install_tracer_signal_handlers();

    int status = 0;
    if (waitpid(child, &status, 0) < 0) {
        perror("pdocker-direct-executor: wait initial tracee");
        TRACE_RETURN(126);
    }
    if (!WIFSTOPPED(status)) {
        fprintf(stderr, "pdocker-direct-trace: child did not stop before exec status=0x%x\n", status);
        TRACE_RETURN(126);
    }

    TraceeState tracees[MAX_TRACEES];
    memset(tracees, 0, sizeof(tracees));
    TraceeState *child_state = add_tracee(tracees, child);
    if (!child_state) {
        fprintf(stderr, "pdocker-direct-trace: tracee table exhausted before start\n");
        TRACE_RETURN(126);
    }
    const char *initial_guest_cwd = getenv("PDOCKER_GUEST_CWD");
    if (initial_guest_cwd && initial_guest_cwd[0]) {
        normalize_guest_path("/", initial_guest_cwd, child_state->guest_cwd,
                             sizeof(child_state->guest_cwd));
    }
    int root_opts = set_trace_options(child);
    if (g_trace_exec) {
        fprintf(stderr, "pdocker-direct-exec: root setopts pid=%d rc=%d\n", (int)child, root_opts);
    }

    int events = 0;
    int root_done = 0;
    int root_rc = 126;
    if (g_stats) {
        memset(g_syscall_counts, 0, sizeof(g_syscall_counts));
        g_stop_count = 0;
        clock_gettime(CLOCK_MONOTONIC, &g_stats_start);
    }
    if (continue_tracee(child, 0) != 0) {
        perror("pdocker-direct-trace: initial PTRACE_SYSCALL");
        TRACE_RETURN(126);
    }

    while (1) {
        pid_t got = waitpid(-1, &status, __WALL);
        if (got < 0) {
            if (errno == EINTR) {
                continue;
            }
            if (errno == ECHILD && root_done) {
                print_syscall_stats("echild-root-done", root_rc);
                TRACE_RETURN(root_rc);
            }
            if (errno == ECHILD) {
                int alive = prune_dead_tracees(tracees, getpid());
                fprintf(stderr,
                        "pdocker-direct-trace: no waitable tracees remain before root exit was observed (tracked=%d)\n",
                        alive);
                print_syscall_stats("echild-no-waitable-tracees", 126);
                TRACE_RETURN(126);
            }
            perror("pdocker-direct-trace: waitpid");
            TRACE_RETURN(126);
        }
        if (got == 0) {
            int alive = prune_dead_tracees(tracees, getpid());
            if (root_done && alive == 0) {
                print_syscall_stats("root-done-idle", root_rc);
                TRACE_RETURN(root_rc);
            }
            if (!root_done && alive == 0) {
                fprintf(stderr,
                        "pdocker-direct-trace: no live tracees remain before root exit was observed\n");
                print_syscall_stats("no-live-tracees", 126);
                TRACE_RETURN(126);
            }
            continue;
        }
        TraceeState *state = find_tracee(tracees, got);
        if (!state) {
            state = add_tracee(tracees, got);
            if (!state) {
                fprintf(stderr, "pdocker-direct-trace: tracee table exhausted for pid=%d\n", (int)got);
                continue_tracee(got, SIGKILL);
                continue;
            }
        }
        if (WIFEXITED(status)) {
            int rc = WEXITSTATUS(status);
            TRACE_LOG(
                    "pdocker-direct-trace: pid=%d exited rc=%d events=%d last_syscall=%ld(%s) active=%d\n",
                    (int)got, rc, events, state->last_nr, syscall_name(state->last_nr),
                    tracee_count(tracees) - 1);
            remove_tracee(tracees, got);
            if (got == child) {
                root_done = 1;
                root_rc = rc;
            }
            if (root_done && tracee_count(tracees) == 0) {
                print_syscall_stats("root-exited", root_rc);
                TRACE_RETURN(root_rc);
            }
            continue;
        }
        if (WIFSIGNALED(status)) {
            int sig = WTERMSIG(status);
            if (got == child || g_trace_verbose) {
                fprintf(stderr,
                        "pdocker-direct-trace: pid=%d signaled sig=%d events=%d last_syscall=%ld(%s) args=%llx,%llx,%llx,%llx,%llx,%llx active=%d\n",
                        (int)got, sig, events, state->last_nr, syscall_name(state->last_nr),
                        state->last_args[0], state->last_args[1], state->last_args[2],
                        state->last_args[3], state->last_args[4], state->last_args[5],
                        tracee_count(tracees) - 1);
            }
            remove_tracee(tracees, got);
            if (got == child) {
                root_done = 1;
                root_rc = 128 + sig;
            }
            if (root_done && tracee_count(tracees) == 0) {
                print_syscall_stats("root-signaled", root_rc);
                TRACE_RETURN(root_rc);
            }
            continue;
        }
        if (!WIFSTOPPED(status)) continue;
        if (g_stats) g_stop_count++;

        if (g_validate_tracees && !tracee_is_still_owned(getpid(), got)) {
            char summary[160];
            tracee_status_summary(got, summary, sizeof(summary));
            fprintf(stderr,
                    "pdocker-direct-trace: dropping detached stopped tracee pid=%d status=0x%x %s last=%ld(%s)\n",
                    (int)got, status, summary, state->last_nr,
                    syscall_name(state->last_nr));
            remove_tracee(tracees, got);
            continue;
        }

        int sig = WSTOPSIG(status);
        unsigned int event = (unsigned int)status >> 16;
        events++;

        if (sig == SIGSEGV && g_managed_memory_pager) {
            siginfo_t info;
            struct user_pt_regs fault_regs;
            memset(&info, 0, sizeof(info));
            memset(&fault_regs, 0, sizeof(fault_regs));
            if (ptrace(PTRACE_GETSIGINFO, got, NULL, &info) == 0 &&
                    get_regs(got, &fault_regs) == 0) {
                int handled = handle_managed_memory_fault(
                        got, state, &fault_regs,
                        (unsigned long long)(uintptr_t)info.si_addr);
                if (handled > 0) {
                    if (continue_tracee(got, 0) != 0) break;
                    continue;
                }
                if (handled < 0) {
                    fprintf(stderr,
                            "pdocker-direct-managed-pager: fault handling failed pid=%d addr=%p\n",
                            (int)got, info.si_addr);
                }
            }
        }

        if (sig == (SIGTRAP | 0x80)) {
            struct user_pt_regs regs;
            if (get_regs(got, &regs) == 0) {
                int completed_in_userland = 0;
                if (!state->in_syscall) {
                    handle_syscall_entry(got, &regs, state, rootfs, exec_argv[0], libpath,
                                         events, &completed_in_userland);
                } else if (state->emulated_nr >= 0) {
                    regs.regs[0] = state->emulated_result;
                    if (set_regs(got, &regs) != 0) {
                        fprintf(stderr, "pdocker-direct-trace: pid=%d setregs exit failed: %s\n",
                                (int)got, strerror(errno));
                    } else {
                        TRACE_LOG(
                                "pdocker-direct-trace: pid=%d emulate-success return nr=%ld(%s) -> %llu\n",
                                (int)got, state->emulated_nr, syscall_name(state->emulated_nr),
                                state->emulated_result);
                    }
                    state->last_emulated_nr = state->emulated_nr;
                    state->emulated_nr = -1;
                } else if (state->last_nr == 49 && (long long)regs.regs[0] == 0 &&
                           state->pending_guest_cwd[0]) {
                    snprintf(state->guest_cwd, sizeof(state->guest_cwd), "%s",
                             state->pending_guest_cwd);
                    state->pending_guest_cwd[0] = '\0';
                } else if (state->in_syscall && is_memory_trace_syscall(state->last_nr)) {
                    record_memory_syscall_exit(state, regs.regs[0]);
                    if (state->last_nr == 222) {
                        maybe_finish_managed_mmap(got, &regs, state, regs.regs[0]);
                    }
                }
                if (state->in_syscall && state->pending_path_cache_invalidation) {
                    finish_path_cache_mutation(state);
                    state->pending_path_cache_invalidation = 0;
                }
                    state->in_syscall = completed_in_userland ? 1 : !state->in_syscall;
            }
            if (continue_tracee(got, 0) != 0) break;
            continue;
        }

        if (sig == SIGSYS) {
            struct user_pt_regs regs;
            if (get_regs(got, &regs) == 0) {
                long current_nr = (long)regs.regs[8];
                int completed_current = current_nr == -1 &&
                                        syscall_completed_in_userland(state->last_nr);
                int suppressible = state->emulated_nr >= 0 ||
                                   (state->last_emulated_nr >= 0 &&
                                    syscall_completed_in_userland(state->last_emulated_nr)) ||
                                   completed_current;
                int emulated_errno = 0;
                if (!suppressible && current_nr == 17 &&
                    emulate_getcwd(got, &regs, state, rootfs, &state->emulated_result)) {
                    state->last_nr = current_nr;
                    for (int i = 0; i < 6; ++i) state->last_args[i] = regs.regs[i];
                    if (complete_emulated_syscall(got, &regs, state->emulated_result) == 0) {
                        state->last_emulated_nr = current_nr;
                        state->emulated_nr = -1;
                        state->in_syscall = 0;
                        suppressible = 1;
                    } else {
                        fprintf(stderr, "pdocker-direct-trace: pid=%d setregs direct getcwd SIGSYS emulation failed: %s\n",
                                (int)got, strerror(errno));
                    }
                } else if (!suppressible && (current_nr == 48 || current_nr == 439) &&
                           emulate_faccessat_path(got, &regs, rootfs, &state->emulated_result)) {
                    state->last_nr = current_nr;
                    for (int i = 0; i < 6; ++i) state->last_args[i] = regs.regs[i];
                    if (complete_emulated_syscall(got, &regs, state->emulated_result) == 0) {
                        state->last_emulated_nr = current_nr;
                        state->emulated_nr = -1;
                        state->in_syscall = 0;
                        suppressible = 1;
                    } else {
                        fprintf(stderr, "pdocker-direct-trace: pid=%d setregs direct faccess SIGSYS emulation failed: %s\n",
                                (int)got, strerror(errno));
                    }
                } else if (!suppressible && current_nr == 78 &&
                           emulate_proc_self_exe_readlinkat(got, &regs, state, &state->emulated_result)) {
                    state->last_nr = current_nr;
                    for (int i = 0; i < 6; ++i) state->last_args[i] = regs.regs[i];
                    if (complete_emulated_syscall(got, &regs, state->emulated_result) == 0) {
                        state->last_emulated_nr = current_nr;
                        state->emulated_nr = -1;
                        state->in_syscall = 0;
                        suppressible = 1;
                    } else {
                        fprintf(stderr, "pdocker-direct-trace: pid=%d setregs direct readlinkat SIGSYS emulation failed: %s\n",
                                (int)got, strerror(errno));
                    }
                } else if (!suppressible && syscall_emulate_errno(current_nr, &emulated_errno)) {
                    state->last_nr = current_nr;
                    for (int i = 0; i < 6; ++i) state->last_args[i] = regs.regs[i];
                    state->emulated_result = (unsigned long long)-emulated_errno;
                    if (complete_emulated_syscall(got, &regs, state->emulated_result) == 0) {
                        state->last_emulated_nr = current_nr;
                        state->emulated_nr = -1;
                        state->in_syscall = 0;
                        suppressible = 1;
                    } else {
                        fprintf(stderr, "pdocker-direct-trace: pid=%d setregs direct errno SIGSYS emulation failed: %s\n",
                                (int)got, strerror(errno));
                    }
                } else if (!suppressible && syscall_emulate_success(current_nr)) {
                    state->last_nr = current_nr;
                    for (int i = 0; i < 6; ++i) state->last_args[i] = regs.regs[i];
                    state->emulated_result = prepare_emulated_result(got, state, current_nr);
                    if (complete_emulated_syscall(got, &regs, state->emulated_result) == 0) {
                        state->last_emulated_nr = current_nr;
                        state->emulated_nr = -1;
                        state->in_syscall = 0;
                        suppressible = 1;
                    } else {
                        fprintf(stderr, "pdocker-direct-trace: pid=%d setregs direct SIGSYS emulation failed: %s\n",
                                (int)got, strerror(errno));
                    }
                } else if (!suppressible && current_nr >= 425 && current_nr <= 427) {
                    state->last_nr = current_nr;
                    for (int i = 0; i < 6; ++i) state->last_args[i] = regs.regs[i];
                    state->emulated_result = (unsigned long long)-ENOSYS;
                    if (complete_emulated_syscall(got, &regs, state->emulated_result) == 0) {
                        state->last_emulated_nr = current_nr;
                        state->emulated_nr = -1;
                        state->in_syscall = 0;
                        suppressible = 1;
                    } else {
                        fprintf(stderr, "pdocker-direct-trace: pid=%d setregs direct io_uring SIGSYS emulation failed: %s\n",
                                (int)got, strerror(errno));
                    }
                } else if (!suppressible && current_nr == 57 && (int)regs.regs[0] == g_rootfs_fd) {
                    state->last_nr = current_nr;
                    for (int i = 0; i < 6; ++i) state->last_args[i] = regs.regs[i];
                    state->emulated_result = 0;
                    if (complete_emulated_syscall(got, &regs, state->emulated_result) == 0) {
                        state->last_emulated_nr = current_nr;
                        state->emulated_nr = -1;
                        state->in_syscall = 0;
                        suppressible = 1;
                    } else {
                        fprintf(stderr, "pdocker-direct-trace: pid=%d setregs direct close(rootfs_fd) SIGSYS emulation failed: %s\n",
                                (int)got, strerror(errno));
                    }
                }
                if (suppressible) {
                    if (state->emulated_nr >= 0) {
                        regs.regs[0] = state->emulated_result;
                        if (set_regs(got, &regs) != 0) {
                            fprintf(stderr, "pdocker-direct-trace: pid=%d setregs SIGSYS suppression failed: %s\n",
                                    (int)got, strerror(errno));
                        }
                        state->last_emulated_nr = state->emulated_nr;
                        state->emulated_nr = -1;
                        state->in_syscall = 0;
                    } else if (completed_current) {
                        regs.regs[0] = state->emulated_result;
                        if (set_regs(got, &regs) != 0) {
                            fprintf(stderr, "pdocker-direct-trace: pid=%d setregs completed SIGSYS suppression failed: %s\n",
                                    (int)got, strerror(errno));
                        }
                        state->last_emulated_nr = state->last_nr;
                        state->in_syscall = 0;
                    }
                    TRACE_LOG(
                            "pdocker-direct-trace: pid=%d SIGSYS nr=%llu(%s) pc=%llx args=%llx,%llx,%llx,%llx,%llx,%llx last=%ld(%s)\n",
                            (int)got,
                            (unsigned long long)regs.regs[8], syscall_name((long)regs.regs[8]),
                            (unsigned long long)regs.pc,
                            (unsigned long long)regs.regs[0], (unsigned long long)regs.regs[1],
                            (unsigned long long)regs.regs[2], (unsigned long long)regs.regs[3],
                            (unsigned long long)regs.regs[4], (unsigned long long)regs.regs[5],
                            state->last_nr, syscall_name(state->last_nr));
                } else {
                    fprintf(stderr,
                            "pdocker-direct-trace: pid=%d SIGSYS nr=%llu(%s) pc=%llx args=%llx,%llx,%llx,%llx,%llx,%llx last=%ld(%s)\n",
                            (int)got,
                            (unsigned long long)regs.regs[8], syscall_name((long)regs.regs[8]),
                            (unsigned long long)regs.pc,
                            (unsigned long long)regs.regs[0], (unsigned long long)regs.regs[1],
                            (unsigned long long)regs.regs[2], (unsigned long long)regs.regs[3],
                            (unsigned long long)regs.regs[4], (unsigned long long)regs.regs[5],
                            state->last_nr, syscall_name(state->last_nr));
                }
                if (suppressible) {
                    TRACE_LOG(
                            "pdocker-direct-trace: pid=%d suppress SIGSYS after emulated nr=%ld(%s)\n",
                            (int)got, state->last_emulated_nr, syscall_name(state->last_emulated_nr));
                    state->last_emulated_nr = -1;
                    if (continue_tracee(got, 0) != 0) break;
                    continue;
                }
            } else {
                fprintf(stderr, "pdocker-direct-trace: pid=%d SIGSYS getregs failed: %s last=%ld(%s)\n",
                        (int)got, strerror(errno), state->last_nr, syscall_name(state->last_nr));
            }
            if (continue_tracee(got, SIGSYS) != 0) break;
            continue;
        }

        if (event == PTRACE_EVENT_SECCOMP) {
            struct user_pt_regs regs;
            if (get_regs(got, &regs) == 0) {
                int completed_in_userland = 0;
                handle_syscall_entry(got, &regs, state, rootfs, exec_argv[0], libpath,
                                     events, &completed_in_userland);
                if (completed_in_userland) {
                    state->in_syscall = 1;
                }
            } else {
                fprintf(stderr, "pdocker-direct-trace: pid=%d seccomp getregs failed: %s last=%ld(%s)\n",
                        (int)got, strerror(errno), state->last_nr, syscall_name(state->last_nr));
            }
            if (state->in_syscall && state->emulated_nr >= 0) {
                if (continue_tracee_to_syscall_exit(got, 0) != 0) break;
            } else if (g_trace_memory && is_memory_trace_syscall(state->last_nr)) {
                state->in_syscall = 1;
                if (continue_tracee_to_syscall_exit(got, 0) != 0) break;
            } else if (state->pending_path_cache_invalidation) {
                state->in_syscall = 1;
                if (continue_tracee_to_syscall_exit(got, 0) != 0) break;
            } else if (state->last_nr == 49 && state->pending_guest_cwd[0]) {
                state->in_syscall = 1;
                if (continue_tracee_to_syscall_exit(got, 0) != 0) break;
            } else if (continue_tracee(got, 0) != 0) {
                break;
            }
            continue;
        }

        if (event == PTRACE_EVENT_FORK || event == PTRACE_EVENT_VFORK || event == PTRACE_EVENT_CLONE) {
            unsigned long new_pid = 0;
            if (ptrace(PTRACE_GETEVENTMSG, got, NULL, &new_pid) == 0 && new_pid > 0) {
                TraceeState *new_state = add_tracee(tracees, (pid_t)new_pid);
                if (!new_state) {
                    fprintf(stderr, "pdocker-direct-trace: tracee table exhausted for event child=%lu\n", new_pid);
                } else {
                    if (state) {
                        new_state->uid = state->uid;
                        new_state->euid = state->euid;
                        new_state->suid = state->suid;
                        new_state->gid = state->gid;
                        new_state->egid = state->egid;
                        new_state->sgid = state->sgid;
                        snprintf(new_state->exec_guest_path, sizeof(new_state->exec_guest_path),
                                 "%s", state->exec_guest_path);
                        snprintf(new_state->guest_cwd, sizeof(new_state->guest_cwd),
                                 "%s", state->guest_cwd[0] ? state->guest_cwd : "/");
                    }
                    int opt_rc = set_trace_options((pid_t)new_pid);
                    if (g_trace_exec) {
                        fprintf(stderr, "pdocker-direct-exec: event=%u parent=%d new=%lu setopts=%d active=%d\n",
                                event, (int)got, new_pid, opt_rc, tracee_count(tracees));
                    }
                    TRACE_LOG(
                            "pdocker-direct-trace: event=%u parent=%d new_tracee=%lu active=%d\n",
                            event, (int)got, new_pid, tracee_count(tracees));
                }
            } else {
                fprintf(stderr, "pdocker-direct-trace: event=%u parent=%d GETEVENTMSG failed: %s\n",
                        event, (int)got, strerror(errno));
            }
            if (continue_tracee(got, 0) != 0) break;
            continue;
        }

        if (event == PTRACE_EVENT_EXEC || event == PTRACE_EVENT_SECCOMP || event == PTRACE_EVENT_EXIT) {
            TRACE_LOG("pdocker-direct-trace: pid=%d event=%u sig=%d last_syscall=%ld(%s)\n",
                      (int)got, event, sig, state->last_nr, syscall_name(state->last_nr));
            if (continue_tracee(got, 0) != 0) break;
            continue;
        }

        if (sig == SIGSTOP || sig == SIGTRAP) {
            set_trace_options(got);
            if (continue_tracee(got, 0) != 0) break;
            continue;
        }

        if (continue_tracee(got, sig) != 0) break;
    }
    fprintf(stderr, "pdocker-direct-trace: ptrace loop failed: %s\n", strerror(errno));
    print_syscall_stats("ptrace-loop-failed", 126);
    TRACE_RETURN(126);
#undef TRACE_RETURN
}

static int run_command(int argc, char **argv) {
    const char *mode = "run";
    const char *rootfs = NULL;
    const char *workdir = "/";
    const char **env_items = calloc((size_t)argc + 1, sizeof(char *));
    int env_count = 0;
    int bind_count = 0;
    int command_index = -1;
    int use_syscall_tracer = !env_flag_enabled("PDOCKER_DIRECT_DISABLE_SYSCALL_TRACE");
    int trace_syscall_logs = env_flag_enabled("PDOCKER_DIRECT_TRACE_SYSCALLS");
    g_trace_verbose = env_flag_enabled("PDOCKER_DIRECT_TRACE_VERBOSE") || trace_syscall_logs;
    g_trace_linkat = env_flag_enabled("PDOCKER_DIRECT_TRACE_LINKAT");
    g_trace_paths = env_flag_enabled("PDOCKER_DIRECT_TRACE_PATHS") || trace_syscall_logs;
    g_trace_exec = env_flag_enabled("PDOCKER_DIRECT_TRACE_EXEC");
    g_trace_memory = env_flag_enabled("PDOCKER_DIRECT_TRACE_MEMORY");
    g_trace_memory_verbose = env_flag_enabled("PDOCKER_DIRECT_TRACE_MEMORY_VERBOSE");
    g_trace_memory_threshold = env_u64_or_default(
            "PDOCKER_DIRECT_TRACE_MEMORY_THRESHOLD",
            64ULL * 1024ULL * 1024ULL);
    g_memory_guard = env_flag_enabled("PDOCKER_DIRECT_MEMORY_GUARD");
    g_memory_guard_min_request = env_u64_or_default(
            "PDOCKER_DIRECT_MEMORY_GUARD_MIN_REQUEST",
            64ULL * 1024ULL * 1024ULL);
    g_memory_guard_min_available = env_u64_or_default(
            "PDOCKER_DIRECT_MEMORY_GUARD_MIN_AVAILABLE",
            512ULL * 1024ULL * 1024ULL);
    g_memory_guard_min_swap = env_u64_or_default(
            "PDOCKER_DIRECT_MEMORY_GUARD_MIN_SWAP",
            256ULL * 1024ULL * 1024ULL);
    const char *pager_env = getenv("PDOCKER_DIRECT_MEMORY_PAGER");
    if (!pager_env || !pager_env[0]) pager_env = getenv("PDOCKER_MEMORY_PAGER");
    g_managed_memory_pager = pager_env && strcmp(pager_env, "managed") == 0;
    g_managed_memory_pager_min_request = env_u64_or_default(
            "PDOCKER_DIRECT_MEMORY_PAGER_MIN_REQUEST",
            128ULL * 1024ULL * 1024ULL);
    g_managed_memory_pager_max_region = env_u64_or_default(
            "PDOCKER_DIRECT_MEMORY_PAGER_MAX_REGION",
            1024ULL * 1024ULL * 1024ULL);
    g_managed_memory_pager_resident_pages = env_u64_or_default(
            "PDOCKER_DIRECT_MEMORY_PAGER_RESIDENT_PAGES",
            256ULL);
    if (g_managed_memory_pager_resident_pages == 0) g_managed_memory_pager_resident_pages = 1;
    if (g_memory_guard || g_managed_memory_pager) g_trace_memory = 1;
    memset(&g_memory_stats, 0, sizeof(g_memory_stats));
    g_memory_stats_printed = 0;
    g_stats = env_flag_enabled("PDOCKER_DIRECT_STATS");
    g_stats_top = (int)env_u64_or_default("PDOCKER_DIRECT_STATS_TOP", 12);
    g_path_profile = env_flag_enabled("PDOCKER_DIRECT_PATH_PROFILE");
    g_path_cache_enabled = !env_flag_enabled("PDOCKER_DIRECT_DISABLE_PATH_CACHE");
    g_path_cache_store_disabled = 0;
    g_path_cache_mutation_inflight = 0;
    g_path_cache_generation = 1;
    memset(g_path_validation_cache, 0, sizeof(g_path_validation_cache));
    memset(g_path_realpath_cache, 0, sizeof(g_path_realpath_cache));
    memset(&g_path_stats, 0, sizeof(g_path_stats));
    g_rootfd_rewrite = env_flag_enabled("PDOCKER_DIRECT_ROOTFD_REWRITE");
    g_validate_tracees = env_flag_enabled("PDOCKER_DIRECT_VALIDATE_TRACEES");
    g_trace_stat_paths = !env_flag_enabled("PDOCKER_DIRECT_UNTRACED_STAT_PATHS");
    const char *trace_mode = getenv("PDOCKER_DIRECT_TRACE_MODE");
    g_selective_trace = !trace_mode || strcmp(trace_mode, "syscall") != 0;
    const char *sync_env = getenv("PDOCKER_DIRECT_SYNC_USEC");
    if (sync_env && sync_env[0]) {
        g_sync_usec = atoi(sync_env);
        if (g_sync_usec < 0) g_sync_usec = 0;
        if (g_sync_usec > 10000) g_sync_usec = 10000;
    }

    for (int i = 2; i < argc; ++i) {
        if (strcmp(argv[i], "--") == 0) {
            command_index = i + 1;
            break;
        } else if (strcmp(argv[i], "--mode") == 0) {
            mode = value_after(&i, argc, argv, "--mode");
        } else if (strcmp(argv[i], "--rootfs") == 0) {
            rootfs = value_after(&i, argc, argv, "--rootfs");
        } else if (strcmp(argv[i], "--workdir") == 0) {
            workdir = value_after(&i, argc, argv, "--workdir");
        } else if (strcmp(argv[i], "--env") == 0) {
            env_items[env_count] = value_after(&i, argc, argv, "--env");
            env_count += 1;
        } else if (strcmp(argv[i], "--bind") == 0) {
            parse_bind_spec(value_after(&i, argc, argv, "--bind"));
            bind_count += 1;
        } else if (strcmp(argv[i], "--cow-upper") == 0 ||
                   strcmp(argv[i], "--cow-lower") == 0 ||
                   strcmp(argv[i], "--cow-guest") == 0) {
            const char *option = argv[i];
            (void)value_after(&i, argc, argv, option);
        } else {
            fprintf(stderr, "pdocker-direct-executor: unknown option: %s\n", argv[i]);
            usage(stderr);
            return 2;
        }
    }

    if (!rootfs || command_index < 0 || command_index >= argc) {
        fprintf(stderr, "pdocker-direct-executor: --rootfs and command argv are required\n");
        usage(stderr);
        free(env_items);
        return 2;
    }

    char rootfs_abs[PATH_MAX];
    if (!realpath(rootfs, rootfs_abs)) {
        perror("pdocker-direct-executor: realpath rootfs");
        free(env_items);
        return 126;
    }
    rootfs = rootfs_abs;

    char cwd[PATH_MAX];
    if (workdir[0] == '/') {
        int bind_path = 0;
        int resolved = resolve_guest_host_path(rootfs, workdir, cwd, sizeof(cwd), &bind_path);
        if (resolved < 0) {
            fprintf(stderr, "pdocker-direct-executor: workdir path too long\n");
            free(env_items);
            return 126;
        }
        if (resolved == 0) {
            if (snprintf(cwd, sizeof(cwd), "%s/%s", rootfs, workdir + 1) >= (int)sizeof(cwd)) {
                fprintf(stderr, "pdocker-direct-executor: workdir path too long\n");
                free(env_items);
                return 126;
            }
        }
    } else {
        if (snprintf(cwd, sizeof(cwd), "%s/%s", rootfs, workdir) >= (int)sizeof(cwd)) {
            fprintf(stderr, "pdocker-direct-executor: workdir path too long\n");
            free(env_items);
            return 126;
        }
    }
    if (chdir(cwd) != 0 && chdir(rootfs) != 0) {
        perror("pdocker-direct-executor: chdir rootfs/workdir");
        free(env_items);
        return 126;
    }
    if (g_rootfs_fd >= 0) {
        close(g_rootfs_fd);
        g_rootfs_fd = -1;
    }
    if (g_rootfd_rewrite) {
        int rootfd = open(rootfs, O_RDONLY | O_DIRECTORY);
        if (rootfd < 0) {
            perror("pdocker-direct-executor: open rootfs fd");
            free(env_items);
            return 126;
        }
        int high_rootfd = fcntl(rootfd, F_DUPFD, 1000);
        if (high_rootfd >= 0) {
            close(rootfd);
            g_rootfs_fd = high_rootfd;
        } else {
            g_rootfs_fd = rootfd;
        }
    }

    char ldpath[PATH_MAX];
    char helper_path[PATH_MAX];
    char helper_dir[PATH_MAX];
    const char *loader = NULL;
    const char *ld_candidates[] = {
        "lib/aarch64-linux-gnu/ld-linux-aarch64.so.1",
        "lib/ld-linux-aarch64.so.1",
        "lib64/ld-linux-aarch64.so.1",
        "lib/ld-musl-aarch64.so.1",
        "lib64/ld-musl-aarch64.so.1",
        "usr/lib/ld-musl-aarch64.so.1",
        NULL,
    };
    for (int i = 0; ld_candidates[i]; ++i) {
        if (snprintf(ldpath, sizeof(ldpath), "%s/%s", rootfs, ld_candidates[i]) >= (int)sizeof(ldpath)) {
            continue;
        }
        if (access(ldpath, X_OK) == 0) {
            loader = ldpath;
            break;
        }
    }
    const char *allow_helper_loader = getenv("SKYDNIR_DIRECT_ALLOW_HELPER_LOADER");
    if (!allow_helper_loader) allow_helper_loader = getenv("PDOCKER_DIRECT_ALLOW_HELPER_LOADER");
    if (!loader && allow_helper_loader && strcmp(allow_helper_loader, "1") == 0 &&
        realpath(argv[0], helper_path)) {
        strncpy(helper_dir, helper_path, sizeof(helper_dir) - 1);
        helper_dir[sizeof(helper_dir) - 1] = '\0';
        char *slash = strrchr(helper_dir, '/');
        if (slash) {
            *slash = '\0';
            const char *helper_ld_candidates[] = {
                "skydnir-ld-musl-aarch64",
                "libskydnirldmusl.so",
                "libskydnir-ld-musl-aarch64.so",
                "pdocker-ld-linux-aarch64",
                "libpdocker-ld-linux-aarch64.so",
                NULL,
            };
            for (int i = 0; helper_ld_candidates[i]; ++i) {
                if (snprintf(ldpath, sizeof(ldpath), "%s/%s", helper_dir, helper_ld_candidates[i]) < (int)sizeof(ldpath) &&
                    access(ldpath, X_OK) == 0) {
                    loader = ldpath;
                    break;
                }
            }
        }
    }
    if (!loader) {
        fprintf(stderr, "pdocker-direct-executor: rootfs dynamic loader not found under %s\n", rootfs);
        free(env_items);
        return 126;
    }

    if (strcmp(mode, "build") == 0 && !getenv("PDOCKER_DIRECT_NORMALIZE_SYMLINKS")) {
        setenv("PDOCKER_DIRECT_NORMALIZE_SYMLINKS", "always", 1);
    }
    if (!getenv("PDOCKER_DIRECT_PRESERVE_ABSOLUTE_SYMLINKS")) {
        normalize_absolute_symlinks_once(rootfs);
    }

    char target[PATH_MAX];
    const char *cmd0 = argv[command_index];
    if (strchr(cmd0, '/') == NULL) {
        if (resolve_guest_program(rootfs, cmd0, target, sizeof(target)) != 0) {
            fprintf(stderr, "pdocker-direct-executor: command not found in rootfs PATH: %s\n", cmd0);
            free(env_items);
            return 127;
        }
    } else if (cmd0[0] == '/') {
        if (snprintf(target, sizeof(target), "%s%s", rootfs, cmd0) >= (int)sizeof(target)) {
            fprintf(stderr, "pdocker-direct-executor: command path too long\n");
            free(env_items);
            return 126;
        }
        if (access(target, X_OK) != 0) {
            fprintf(stderr, "pdocker-direct-executor: command not executable: %s\n", cmd0);
            free(env_items);
            return errno == ENOENT ? 127 : 126;
        }
    } else {
        if (snprintf(target, sizeof(target), "%s/%s", cwd, cmd0) >= (int)sizeof(target)) {
            fprintf(stderr, "pdocker-direct-executor: command path too long\n");
            free(env_items);
            return 126;
        }
        if (access(target, X_OK) != 0) {
            fprintf(stderr, "pdocker-direct-executor: command not executable: %s\n", cmd0);
            free(env_items);
            return errno == ENOENT ? 127 : 126;
        }
    }

    char libpath[PATH_MAX * 2];
    snprintf(libpath, sizeof(libpath),
             "%s/lib/aarch64-linux-gnu:%s/usr/lib/aarch64-linux-gnu:%s/lib:%s/usr/lib",
             rootfs, rootfs, rootfs, rootfs);
    char shim_preload[PATH_MAX], preload[PATH_MAX * 2];
    snprintf(shim_preload, sizeof(shim_preload), "%s/.pdocker-rootfs-shim.so", rootfs);
    preload[0] = '\0';

    clearenv();
    setenv("PDOCKER_ROOTFS", rootfs, 1);
    setenv("LD_LIBRARY_PATH", libpath, 1);
    setenv("GLIBC_TUNABLES", "glibc.pthread.rseq=0", 0);
    if (access(shim_preload, R_OK) == 0) {
        snprintf(preload, sizeof(preload), "%s", shim_preload);
    }
    if (preload[0]) {
        setenv("LD_PRELOAD", preload, 1);
    }
    setenv("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", 0);
    setenv("SSL_CERT_FILE", "/etc/ssl/certs/ca-certificates.crt", 0);
    setenv("SSL_CERT_DIR", "/etc/ssl/certs", 0);
    setenv("NODE_EXTRA_CA_CERTS", "/etc/ssl/certs/ca-certificates.crt", 0);
    setenv("PWD", workdir, 1);
    setenv("PDOCKER_GUEST_CWD", workdir, 1);
    for (int i = 0; i < env_count; ++i) {
        const char *eq = strchr(env_items[i], '=');
        if (!eq || eq == env_items[i]) continue;
        size_t klen = (size_t)(eq - env_items[i]);
        if (klen == 10 && strncmp(env_items[i], "LD_PRELOAD", 10) == 0) continue;
        if (klen == 15 && strncmp(env_items[i], "LD_LIBRARY_PATH", 15) == 0) continue;
        if (klen == 13 && strncmp(env_items[i], "PDOCKER_ROOTFS", 13) == 0) continue;
        char *key = strndup(env_items[i], klen);
        if (!key) continue;
        setenv(key, eq + 1, 1);
        free(key);
    }
    memory_telemetry_init_from_env();

    int is_script = file_starts_with(target, "#!");
    int is_static_elf = !is_script && elf_has_interp(target) == 0;
    char shell[PATH_MAX];
    char shell_argv0[PATH_MAX];
    char shell_arg[PATH_MAX];
    char script_guest[PATH_MAX];
    const char *program = target;
    int has_shell_arg = 0;
    shell_argv0[0] = '\0';
    shell_arg[0] = '\0';
    script_guest[0] = '\0';
    if (is_script) {
        int bind_guest_rc = bind_host_to_guest_path(target, script_guest, sizeof(script_guest));
        if (bind_guest_rc < 0) {
            fprintf(stderr, "pdocker-direct-executor: script guest path too long: %s\n", target);
            free(env_items);
            return 126;
        }
        if (bind_guest_rc == 0) guest_exec_path(rootfs, target, script_guest, sizeof(script_guest));
        char interp[PATH_MAX];
        char interp_arg[PATH_MAX];
        if (parse_shebang(target, interp, sizeof(interp), interp_arg, sizeof(interp_arg)) == 0 &&
            resolve_guest_program(rootfs, interp, shell, sizeof(shell)) == 0) {
            snprintf(shell_argv0, sizeof(shell_argv0), "%s", interp);
            if (interp_arg[0]) {
                snprintf(shell_arg, sizeof(shell_arg), "%s", interp_arg);
                has_shell_arg = 1;
            }
        } else {
            snprintf(shell, sizeof(shell), "%s/bin/bash", rootfs);
            if (access(shell, X_OK) != 0) {
                snprintf(shell, sizeof(shell), "%s/bin/sh", rootfs);
                snprintf(shell_argv0, sizeof(shell_argv0), "/bin/sh");
            } else {
                snprintf(shell_argv0, sizeof(shell_argv0), "/bin/bash");
            }
        }
        program = shell;
    }

    int cmd_argc = argc - command_index;
    char **nargv = calloc((size_t)cmd_argc + 10, sizeof(char *));
    if (!nargv) {
        free(env_items);
        return 126;
    }
    int n = 0;
    if (is_static_elf) {
        nargv[n++] = (char *)program;
    } else {
        nargv[n++] = (char *)loader;
        nargv[n++] = "--library-path";
        nargv[n++] = libpath;
        nargv[n++] = "--argv0";
        nargv[n++] = is_script && shell_argv0[0] ? shell_argv0 : (char *)cmd0;
        if (preload[0]) {
            nargv[n++] = "--preload";
            nargv[n++] = preload;
        }
        nargv[n++] = (char *)program;
        if (is_script && has_shell_arg) nargv[n++] = shell_arg;
        if (is_script) nargv[n++] = script_guest[0] ? script_guest : target;
    }
    for (int i = command_index + 1; i < argc; ++i) nargv[n++] = argv[i];
    nargv[n] = NULL;

    TRACE_LOG(
            "pdocker-direct-executor: mode=%s rootfs=%s workdir=%s env=%d bind=%d argv0=%s\n",
            mode, rootfs, workdir, env_count, bind_count, cmd0);
    if (use_syscall_tracer) {
        int rc = trace_and_exec(nargv, rootfs, libpath);
        free(nargv);
        free(env_items);
        return rc;
    }
    execve(loader, nargv, environ);
    perror("pdocker-direct-executor: execve loader");
    free(nargv);
    free(env_items);
    return 126;
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--pdocker-direct-probe") == 0) {
        puts("pdocker-direct-executor:1");
        puts("cow-bind=0");
        puts("bind-path-rewrite=1");
        if (getenv("PDOCKER_DIRECT_EXPERIMENTAL_PROCESS_EXEC")) {
            puts("process-exec=1");
        } else {
            puts("process-exec=0");
        }
        return 0;
    }

    if (argc == 2 && strcmp(argv[1], "--pdocker-memory-pager-probe") == 0) {
        return run_memory_pager_probe();
    }

    if (argc == 2 && strcmp(argv[1], "--pdocker-memory-pager-poc") == 0) {
        return run_memory_pager_poc();
    }

    if (argc == 2 && strcmp(argv[1], "--pdocker-memory-pager-managed-poc") == 0) {
        memory_telemetry_init_from_env();
        int rc = run_memory_pager_managed_poc();
        if (g_memory_telemetry_path[0]) {
            memory_telemetry_append_sample("managed-poc", rc == 0 ? "not_lmk_suspected" : "unknown", "managed-poc");
            (void)memory_telemetry_atomic_write_summary("managed-poc", rc, rc == 0 ? "not_lmk_suspected" : "unknown");
        }
        return rc;
    }

    if (argc == 2 && strcmp(argv[1], "--pdocker-memory-pager-transparent-poc") == 0) {
        memory_telemetry_init_from_env();
        int rc = run_memory_pager_transparent_poc();
        if (g_memory_telemetry_path[0]) {
            memory_telemetry_append_sample("transparent-poc", rc == 0 ? "not_lmk_suspected" : "unknown", "transparent-poc");
            (void)memory_telemetry_atomic_write_summary("transparent-poc", rc, rc == 0 ? "not_lmk_suspected" : "unknown");
        }
        return rc;
    }

    if (argc >= 2 && strcmp(argv[1], "run") == 0) {
        return run_command(argc, argv);
    }

    usage(stderr);
    return 2;
}
