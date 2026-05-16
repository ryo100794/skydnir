#!/usr/bin/env python3
"""Host-only archive API / docker cp compatibility gate.

This gate intentionally avoids network, GPU, terminal, ADB, and container
execution.  It runs the focused fail-closed unit tests for:

- Docker container archive tar PUT/GET helpers.
- cow_bind lower/upper directory merge behavior.
- Overlay whiteout hiding and recreation through PUT.
- Hardlink, symlink, chunked upload, and feasible uid/gid/xattr coverage.
- Metadata preservation for copied files.
- Static planned device gate for future Docker CLI `docker cp` end-to-end proof.
- Path traversal and reserved whiteout injection rejection.
"""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(argv):
    print("+", " ".join(argv), flush=True)
    return subprocess.run(argv, cwd=ROOT).returncode


def main():
    checks = [
        [sys.executable, "-m", "py_compile", "docker-proot-setup/bin/pdockerd"],
        [sys.executable, "-m", "unittest", "tests.test_archive_api_compat"],
        [sys.executable, "-m", "unittest", "tests.test_docker_cp_device_gate"],
    ]
    for argv in checks:
        rc = run(argv)
        if rc:
            return rc
    left = ROOT / "docker-proot-setup" / "bin" / "pdockerd"
    right = ROOT / "app" / "src" / "main" / "assets" / "pdockerd" / "pdockerd"
    if left.read_bytes() != right.read_bytes():
        print(f"asset pdockerd is stale: {right}", file=sys.stderr)
        return 1
    print("archive-api-compat: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
