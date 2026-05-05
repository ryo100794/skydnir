#!/usr/bin/env python3
"""Static contract checks for pdocker-direct syscall mediation coverage."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIRECT_EXEC = ROOT / "app" / "src" / "main" / "cpp" / "pdocker_direct_exec.c"
PTY = ROOT / "app" / "src" / "main" / "cpp" / "pty.c"
LIBCOW = ROOT / "docker-proot-setup" / "src" / "overlay" / "libcow.c"
COW_TEST = ROOT / "docker-proot-setup" / "src" / "overlay" / "test_cow.sh"
RUNTIME_CONTRACT = ROOT / "docker-proot-setup" / "scripts" / "verify_runtime_contract.py"
COVERAGE = ROOT / "tests" / "direct_syscall_coverage.json"

REQUIRED_PATH_VARIANTS = {
    "path.absolute-rootfs-rewrite",
    "path.relative-dirfd-preserve",
    "path.rootfs-already-host-path",
    "path.proc-dev-sys-no-rewrite",
    "path.parent-segment-scratch-fallback",
    "path.bind-resolution",
    "path.dual-path-operations",
    "path.af-unix-socket",
    "path.exec-script-and-long-argv",
    "path.rootfs-fd-lifecycle",
}

REQUIRED_BOUNDARY_VALUES = {
    "boundary.read-tracee-string-null-and-cap",
    "boundary.path-max-and-enametoolong",
    "boundary.getcwd-erange",
    "boundary.sockaddr-min-and-sun-path-limit",
    "boundary.exec-argc-and-scratch-limit",
    "boundary.memory-guard-threshold",
    "boundary.uid-gid-minus-one",
    "boundary.wait-exit-signal-code",
}

REQUIRED_BRANCH_DECISIONS = {
    "branch.should-rewrite-path",
    "branch.resolve-guest-host-path",
    "branch.rewrite-at-path-arg",
    "branch.rewrite-unix-sockaddr",
    "branch.rewrite-execve-arg",
    "branch.memory-guard",
    "branch.credentials-minus-one",
    "branch.getcwd-emulation",
    "branch.tracee-lifecycle",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok: {message}")


def read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError as exc:
        fail(f"could not read {path}: {exc}")


def syscall_names(source: str) -> dict[int, str]:
    names: dict[int, str] = {}
    for nr, name in re.findall(r'case\s+(-?\d+):\s+return\s+"([^"]+)"', source):
        names[int(nr)] = name
    return names


def hook_name_from_comment(comment: str) -> str:
    comment = comment.strip().split(":", 1)[0]
    comment = comment.split("(", 1)[0].strip()
    return comment.replace("-", "_")


def add_hook(
    hooks: dict[int, dict[str, object]],
    names: dict[int, str],
    nr: int,
    kind: str,
    comment: str | None = None,
) -> None:
    name = names.get(nr)
    if not name and comment:
        name = hook_name_from_comment(comment)
    if not name:
        name = f"syscall_{nr}"
    entry = hooks.setdefault(nr, {"name": name, "kinds": set()})
    entry["kinds"].add(kind)  # type: ignore[index]


def parse_add_syscall_hooks(source: str, names: dict[int, str]) -> dict[int, dict[str, object]]:
    hooks: dict[int, dict[str, object]] = {}
    pattern = re.compile(
        r"ADD_(TRACE|ERRNO)_SYSCALL\(\s*(\d+)\s*(?:,\s*[A-Z0-9_]+)?\s*\);\s*/\*\s*([^*]+?)\s*\*/"
    )
    for hook_kind, nr_s, comment in pattern.findall(source):
        add_hook(hooks, names, int(nr_s), f"seccomp_{hook_kind.lower()}", comment)
    return hooks


def parse_rewrite_hooks(source: str, hooks: dict[int, dict[str, object]], names: dict[int, str]) -> None:
    match = re.search(
        r"static int rewrite_syscall_paths\(.*?\)\s*\{\s*switch \(nr\) \{(?P<body>.*?)\n\s*default:",
        source,
        flags=re.S,
    )
    if not match:
        fail("rewrite_syscall_paths switch not found")
    for nr_s, comment in re.findall(r"case\s+(\d+):\s*/\*\s*([^*]+?)\s*\*/", match.group("body")):
        add_hook(hooks, names, int(nr_s), "path_rewrite", comment)


def parse_success_hooks(source: str, hooks: dict[int, dict[str, object]], names: dict[int, str]) -> None:
    match = re.search(r"static int syscall_emulate_success\(long nr\) \{(?P<body>.*?)\n\}", source, flags=re.S)
    if not match:
        fail("syscall_emulate_success not found")
    for nr_s, comment in re.findall(r"nr == (\d+)[^/\n]*?/\*\s*([^*]+?)\s*\*/", match.group("body")):
        add_hook(hooks, names, int(nr_s), "emulate_success", comment)


def parse_errno_hooks(source: str, hooks: dict[int, dict[str, object]], names: dict[int, str]) -> None:
    if "(nr >= 235 && nr <= 239) || nr == 450" not in source:
        fail("NUMA errno range hook changed without updating the verifier")
    for nr in list(range(235, 240)) + [450]:
        add_hook(hooks, names, nr, "emulate_errno")


def parse_forced_emulations(hooks: dict[int, dict[str, object]], names: dict[int, str]) -> None:
    forced = {
        17: "getcwd",
        36: "symlinkat",
        37: "linkat",
        48: "faccessat",
        57: "close",
        78: "readlinkat",
        425: "io_uring_setup",
        426: "io_uring_enter",
        427: "io_uring_register",
        439: "faccessat2",
    }
    for nr, name in forced.items():
        names.setdefault(nr, name)
        add_hook(hooks, names, nr, "forced_userland")


def discover_hooks(source: str) -> dict[int, dict[str, object]]:
    names = syscall_names(source)
    hooks = parse_add_syscall_hooks(source, names)
    parse_rewrite_hooks(source, hooks, names)
    parse_success_hooks(source, hooks, names)
    parse_errno_hooks(source, hooks, names)
    parse_forced_emulations(hooks, names)
    return hooks


def load_manifest() -> dict[str, object]:
    try:
        data = json.loads(COVERAGE.read_text())
    except json.JSONDecodeError as exc:
        fail(f"{COVERAGE} is not valid JSON: {exc}")
    if data.get("schema") != 1:
        fail("direct syscall coverage schema must be 1")
    return data


def covered_syscalls(manifest: dict[str, object]) -> set[str]:
    names: set[str] = set()
    for entry in manifest.get("coverage", []):
        if not isinstance(entry, dict):
            fail("coverage entries must be objects")
        syscalls = entry.get("syscalls")
        if not isinstance(syscalls, list) or not syscalls:
            fail(f"coverage entry {entry.get('id')} must name syscalls")
        names.update(str(name) for name in syscalls)
        if not entry.get("tier") or not entry.get("contract"):
            fail(f"coverage entry {entry.get('id')} must include tier and contract")
    return names


def validate_manifest_links(manifest: dict[str, object]) -> None:
    heavy_ids = {str(case.get("id")) for case in manifest.get("heavy_cases", []) if isinstance(case, dict)}
    if not heavy_ids:
        fail("manifest must define heavy_cases")
    for entry in list(manifest.get("coverage", [])) + list(manifest.get("non_syscall_contracts", [])):
        if not isinstance(entry, dict):
            fail("manifest contract entries must be objects")
        heavy_case = entry.get("heavy_case")
        if heavy_case and heavy_case not in heavy_ids:
            fail(f"{entry.get('id')} references missing heavy case {heavy_case!r}")
    for case in manifest.get("heavy_cases", []):
        if not isinstance(case, dict):
            fail("heavy cases must be objects")
        if not case.get("command") or not case.get("checks"):
            fail(f"heavy case {case.get('id')} must include command and checks")


def validate_matrix(
    manifest: dict[str, object],
    matrix_name: str,
    required_ids: set[str],
    heavy_ids: set[str],
) -> None:
    raw = manifest.get(matrix_name)
    if not isinstance(raw, list) or not raw:
        fail(f"manifest must define non-empty {matrix_name}")
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            fail(f"{matrix_name} entries must be objects")
        entry_id = str(entry.get("id") or "")
        applies_to = entry.get("applies_to") or ([entry.get("function")] if entry.get("function") else None)
        cases = entry.get("cases")
        contract = str(entry.get("contract") or "")
        if not entry_id or not isinstance(applies_to, list) or not applies_to:
            fail(f"{matrix_name} entry must include id and applies_to: {entry!r}")
        if not isinstance(cases, list) or not cases:
            fail(f"{matrix_name} entry {entry_id} must reference at least one case")
        if len(contract) < 24:
            fail(f"{matrix_name} entry {entry_id} must include a meaningful contract")
        missing_cases = sorted(str(case) for case in cases if str(case) not in heavy_ids)
        if missing_cases:
            fail(f"{matrix_name} entry {entry_id} references missing case(s): {', '.join(missing_cases)}")
        seen.add(entry_id)
    missing = sorted(required_ids - seen)
    if missing:
        fail(f"{matrix_name} missing required entries: {', '.join(missing)}")


def validate_path_and_boundary_matrices(manifest: dict[str, object]) -> None:
    heavy_ids = {str(case.get("id")) for case in manifest.get("heavy_cases", []) if isinstance(case, dict)}
    validate_matrix(manifest, "path_variant_matrix", REQUIRED_PATH_VARIANTS, heavy_ids)
    validate_matrix(manifest, "boundary_value_matrix", REQUIRED_BOUNDARY_VALUES, heavy_ids)
    validate_matrix(manifest, "branch_decision_matrix", REQUIRED_BRANCH_DECISIONS, heavy_ids)
    for entry in manifest.get("branch_decision_matrix", []):
        if not isinstance(entry, dict):
            continue
        branches = entry.get("branches")
        if not isinstance(branches, list) or len(branches) < 2:
            fail(f"branch_decision_matrix entry {entry.get('id')} must include at least two branches")
        if not entry.get("function"):
            fail(f"branch_decision_matrix entry {entry.get('id')} must name the function")


def validate_required_areas(manifest: dict[str, object]) -> None:
    required = set(str(item) for item in manifest.get("required_areas", []))
    seen: set[str] = set()
    for section in ("coverage", "non_syscall_contracts"):
        for entry in manifest.get(section, []):
            if not isinstance(entry, dict):
                continue
            seen.update(str(area) for area in entry.get("areas", []))
    missing = sorted(required - seen)
    if missing:
        fail(f"required direct syscall areas missing coverage: {', '.join(missing)}")


def require_contains(label: str, source: str, needles: list[str]) -> None:
    missing = [needle for needle in needles if needle not in source]
    if missing:
        fail(f"{label} missing required marker(s): {missing}")


def require_order(label: str, source: str, first: str, second: str) -> None:
    first_at = source.find(first)
    second_at = source.find(second)
    if first_at < 0 or second_at < 0 or first_at >= second_at:
        fail(f"{label} must keep {first!r} before {second!r}")


def validate_static_contract_markers(source: str) -> None:
    pty = read(PTY)
    libcow = read(LIBCOW)
    cow_test = read(COW_TEST)
    runtime_contract = read(RUNTIME_CONTRACT)

    require_contains(
        "direct getcwd/access emulation",
        source,
        [
            "*result = (unsigned long long)-ERANGE;",
            "emulate_getcwd",
            "emulate_faccessat_path",
            "state->pending_guest_cwd",
        ],
    )
    require_contains(
        "direct path boundary primitives",
        source,
        [
            "if (!rootfs || !rootfs[0] || !path || path[0] != '/') return 0;",
            "return -ENAMETOOLONG;",
            "path_has_parent_segment",
            "original[1] != '/'",
            "should_skip_unix_socket_rewrite",
            "strlen(rewritten) >= sizeof(addr.sun_path)",
        ],
    )
    require_contains(
        "direct exec argv/rootfs rewrite",
        source,
        [
            "rewrite_execve_arg",
            "write_tracee_string(pid, library_path_flag_addr, \"--library-path\")",
            "write_tracee_string(pid, argv0_flag_addr, \"--argv0\")",
            "state->exec_guest_path",
            "parse_shebang",
        ],
    )
    require_contains(
        "direct exec snapshots argv before tracee scratch writes",
        source,
        [
            "copied_arg_values = calloc",
            "read_tracee_string(pid, old_arg_ptrs[i],",
            "write_tracee_string(pid, loader_addr, loader)",
            "write_tracee_string(pid, cursor, copied_arg_values[i])",
        ],
    )
    require_contains(
        "direct exec handles long link argv without fixed 32k scratch",
        source,
        [
            "EXEC_REWRITE_STACK_SAFETY",
            "EXEC_REWRITE_MAX_SCRATCH",
            "string_bytes",
            "payload_bytes",
            "scratch_span",
            "regs->sp - scratch_span",
            "execve argv rewrite scratch too large",
        ],
    )
    require_contains(
        "direct boundary value guards",
        source,
        [
            "if (!addr || cap == 0) return -1;",
            "buf[cap - 1] = '\\0';",
            "EXEC_REWRITE_MAX_ARGC",
            "EXEC_REWRITE_MAX_SCRATCH",
            "memory_guard_would_deny",
            "is_minus_one_arg",
            "root_rc = 128 + sig;",
        ],
    )
    require_contains(
        "pdockerd isolates Dockerfile RUN process groups",
        runtime_contract,
        [
            "Dockerfile RUN process-group isolation",
            "preexec_fn=build_child_preexec",
            "oom_score_adj",
        ],
    )
    require_order(
        "direct exec argv snapshot ordering",
        source,
        "copied_arg_values = calloc",
        "write_tracee_string(pid, loader_addr, loader)",
    )
    require_contains(
        "direct AF_UNIX bind/connect rewrite",
        source,
        [
            "rewrite_unix_sockaddr_arg",
            "addr.sun_family != AF_UNIX",
            "case 200: /* bind(sockfd, addr, addrlen) */",
            "ADD_TRACE_SYSCALL(200)",
            "regs->regs[2] = (unsigned long long)(offsetof(struct sockaddr_un, sun_path) + strlen(rewritten) + 1)",
        ],
    )
    require_contains(
        "direct wait/exit/signal status",
        source,
        [
            "WIFEXITED(status)",
            "root_rc = rc;",
            "WIFSIGNALED(status)",
            "root_rc = 128 + sig;",
            "PTRACE_EVENT_EXIT",
        ],
    )
    require_contains(
        "direct tracee pid ownership",
        source,
        [
            "tracee_is_still_owned",
            "PTRACE_EVENT_FORK",
            "PTRACE_EVENT_VFORK",
            "PTRACE_EVENT_CLONE",
        ],
    )
    require_contains(
        "pty exec/wait status",
        pty,
        ["forkpty(&master", "execve(cmd, argv, envp)", "waitpid(pid, &status, 0)", "WTERMSIG(status)"],
    )
    require_contains(
        "runtime pid reconcile tests",
        runtime_contract,
        ["test_start_container_reconciles_live_pid", "test_start_container_rejects_reused_pid", "PidStartTime"],
    )
    require_contains(
        "libcow path operations",
        libcow,
        [
            "int open(const char *path, int flags, ...)",
            "int openat(int dirfd, const char *path, int flags, ...)",
            "int chmod(const char *path, mode_t mode)",
            "int fchmod(int fd, mode_t mode)",
            "int setxattr(const char *path",
            "int dup2(int oldfd, int newfd)",
        ],
    )
    require_contains(
        "libcow local test",
        cow_test,
        ["hardlink clone", "chmod isolated", "utimes isolated", "fchmod(fd) emulated via path"],
    )


def validate_known_gaps(manifest: dict[str, object], source: str) -> None:
    if "case 203: /* connect(sockfd, addr, addrlen) */" not in source:
        fail("connect rewrite hook is missing")
    if "case 200: /* bind(sockfd, addr, addrlen) */" not in source:
        fail("bind rewrite hook is missing")
    gaps = manifest.get("known_gaps", [])
    bind_gap = [
        gap for gap in gaps
        if isinstance(gap, dict) and gap.get("id") == "direct.socket.unix-bind-rewrite"
    ]
    if bind_gap:
        fail("AF_UNIX bind rewrite is active; remove the stale known_gap")


def main() -> None:
    source = read(DIRECT_EXEC)
    manifest = load_manifest()
    hooks = discover_hooks(source)
    coverage_names = covered_syscalls(manifest)

    missing = []
    for nr, entry in sorted(hooks.items()):
        name = str(entry["name"])
        if name not in coverage_names:
            kinds = ",".join(sorted(entry["kinds"]))  # type: ignore[arg-type]
            missing.append(f"{nr}:{name} ({kinds})")
    if missing:
        fail("syscall hooks missing coverage entries: " + "; ".join(missing))

    extra = sorted(coverage_names - {str(entry["name"]) for entry in hooks.values()})
    allowed_extra: set[str] = set()
    unexpected = [name for name in extra if name not in allowed_extra]
    if unexpected:
        fail("coverage names not found in active hook inventory: " + ", ".join(unexpected))

    validate_manifest_links(manifest)
    validate_path_and_boundary_matrices(manifest)
    validate_required_areas(manifest)
    validate_static_contract_markers(source)
    validate_known_gaps(manifest, source)

    hook_count = len(hooks)
    case_count = len(manifest.get("coverage", []))
    heavy_count = len(manifest.get("heavy_cases", []))
    ok(f"direct syscall hook inventory has {hook_count} active syscall hooks covered by {case_count} entries")
    ok(f"direct syscall contract scaffold defines {heavy_count} heavy/local cases")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(1)
