# Direct syscall Phase 2 coverage gate

This document mirrors `tests/direct_syscall_coverage.json` and is intentionally
about coverage completeness, not feature closure.

Fast gate:

```sh
python3 scripts/run_direct_syscall_scenarios.py --lane local
```

The gate now requires explicit entries for these open Direct syscall Phase 2
lanes:

- attach / PTY / signal semantics, including attach-before-start, PTY resize,
  Ctrl-C/SIGINT, EOF drain, and `128+signal` wait/inspect evidence;
- syscall-specific Linux errno parity instead of permissive success answers;
- one path-mediation contract for rootfs, binds, project volumes, named volumes,
  and AF_UNIX socket paths;
- `linkat` hardlink/inode semantics, currently still a copy-fallback gap;
- `/proc/self/exe` readlink without mutating rootfs temp symlinks;
- Dockerfile `RUN` changed-path manifests so snapshots can avoid broad rootfs
  walks after traced mutations.

Current fast implementation coverage was added for the conservative Dockerfile
`RUN chmod +x /usr/local/bin/pdocker-*` path manifest. General traced RUN
mutation manifests and the device-only attach/PTY/path mediation artifacts
remain planned gaps in the manifest's `phase2_contracts` and `known_gaps`.
