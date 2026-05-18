#!/usr/bin/env python3
"""Host-side contract for Android UI build-context tar compatibility.

This gate is intentionally local and deterministic.  It does not run a device
or claim byte-for-byte parity with Docker yet; it prevents the Android
`DockerEngineClient.createTar` path from regressing back to a regular-file-only
tar writer while the remaining device/build corpus is developed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENGINE_CLIENT = ROOT / "app/src/main/kotlin/io/github/ryo100794/pdocker/DockerEngineClient.kt"
SCENARIOS = ROOT / "tests/feature_scenarios.json"
TODO = ROOT / "docs/plan/TODO.md"


REQUIRED_FIXTURES = (
    "regular files",
    "directories",
    "symlinks",
    "executable modes",
    "long paths/PAX",
    "mtimes",
    ".dockerignore parity",
)


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def require(
    checks: list[dict[str, Any]],
    name: str,
    condition: bool,
    failure_detail: str = "",
    pass_detail: str = "",
) -> None:
    detail = pass_detail if condition else failure_detail
    checks.append({"name": name, "status": "pass" if condition else "fail", "detail": detail})
    if not condition:
        fail(f"{name}: {failure_detail}")
    print(f"ok: {name}")


def scenario_by_id(data: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list):
        fail("feature_scenarios.json must contain scenarios[]")
    for scenario in scenarios:
        if isinstance(scenario, dict) and scenario.get("id") == scenario_id:
            return scenario
    fail(f"missing feature scenario {scenario_id}")
    return {}


def verify(out: Path | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    source = ENGINE_CLIENT.read_text(encoding="utf-8")
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    todo = TODO.read_text(encoding="utf-8")

    require(checks, "TODO records build-context tar compatibility", "Build context tar compatibility" in todo)
    scenario = scenario_by_id(scenarios, "build.context.tar-compatibility")
    require(checks, "feature scenario is fast-local", scenario.get("lane") == "fast-local")
    require(checks, "feature scenario is runnable", scenario.get("status") == "runnable")
    require(
        checks,
        "feature scenario uses this verifier",
        scenario.get("command") == "python3 scripts/verify-build-context-tar-compat.py",
    )
    require(
        checks,
        "feature scenario records latest artifact target",
        scenario.get("evidence_target") == "docs/test/build-context-tar-compat-latest.json",
    )
    acceptance = " ".join(scenario.get("acceptance_scope") or [])
    for fixture in REQUIRED_FIXTURES:
        require(checks, f"scenario covers {fixture}", fixture.replace(" files", "") in acceptance or fixture in acceptance)

    required_tokens = {
        "recursive writer does not filter to files only": ".filter { it.isFile }",
        "explicit path walker": "private fun addTarPath(",
        "symlink detection": "Files.isSymbolicLink(path)",
        "symlink target recording": "Files.readSymbolicLink(path)",
        "nofollow metadata": "LinkOption.NOFOLLOW_LINKS",
        "directory tar entries": "typeFlag = '5'",
        "symlink tar entries": "typeFlag = '2'",
        "regular tar entries": "typeFlag = '0'",
        "executable fallback mode": "file.canExecute()",
        "mode parameter": "mode: Int",
        "link name parameter": "linkName: String",
        "pax path support": 'records["path"] = name',
        "pax linkpath support": 'records["linkpath"] = linkName',
        "pax header writer": "writePaxHeaderIfNeeded",
        "pax record length calculator": "private fun paxRecord(",
        "ustar prefix splitter": "private fun splitUstarName(",
        "ustar prefix field": "putString(header, 345, 155, prefix)",
        "mtime nofollow helper": "private fun linkAwareMtime(",
        "dockerignore still applied": "DockerIgnore.load(root)",
    }
    for name, token in required_tokens.items():
        if name == "recursive writer does not filter to files only":
            require(checks, name, token not in source, "regular-file-only filter must not be present")
        else:
            require(checks, name, token in source, f"missing token {token!r}", f"found token {token!r}")

    require(
        checks,
        "tar entries are deterministic",
        "?.sortedBy { it.name }" in source,
        "context entries must be sorted for reproducible build requests",
    )
    require(
        checks,
        "tar stream has end-of-archive blocks",
        "out.write(ByteArray(1024))" in source,
    )

    result = {
        "schema": "pdocker.build-context-tar-compat.v1",
        "status": "pass",
        "checks": checks,
        "fixtures": list(REQUIRED_FIXTURES),
        "source": str(ENGINE_CLIENT.relative_to(ROOT)),
        "scenario": "build.context.tar-compatibility",
        "limitations": [
            "Host gate is a source contract and compile gate; connected-device build-context byte corpus is still required for release promotion.",
            "PAX path/linkpath support is required for long names, but upstream Docker differential parity is a later corpus gate.",
        ],
    }
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    result = verify(args.out)
    if args.out is not None:
        print(f"build-context-tar-compat: PASS {args.out}")
    else:
        print(f"build-context-tar-compat: PASS ({len(result['checks'])} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
