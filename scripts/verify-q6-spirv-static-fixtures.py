#!/usr/bin/env python3
"""Verify preserved Q6 SPIR-V fixtures with the CPU-only static analyzer.

This gate intentionally does not run ADB, llama.cpp, Docker builds, or Android
Vulkan.  It regenerates analysis for preserved fixture modules and checks that
known static boundaries remain classified the same way after analyzer/refactor
changes.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ANALYZER = ROOT / "scripts" / "analyze-spirv.py"
COMPARE = ROOT / "scripts" / "compare-spirv-dataflow.py"
FIXTURES = {
    "safe": ROOT / "docs" / "test" / "spirv-q6k-safe-current" / "q6k-safe.spv",
    "native": ROOT / "docs" / "test" / "spirv-q6k-native-adb45055" / "native-q6-source.spv",
    "effective": ROOT / "docs" / "test" / "spirv-q6k-native-adb45055" / "effective-q6-local-size-patched.spv",
}


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def require_file(path: Path, errors: list[str]) -> None:
    if not path.is_file():
        errors.append(f"missing fixture: {path.relative_to(ROOT)}")


def analyze(name: str, spv: Path, out_dir: Path) -> tuple[Path | None, dict[str, Any]]:
    out = out_dir / f"{name}.analysis.json"
    result = run(["python3", str(ANALYZER), str(spv), "--json-out", str(out)])
    item: dict[str, Any] = {
        "name": name,
        "spv": str(spv.relative_to(ROOT)),
        "analysis": str(out),
        "returncode": result.returncode,
        "stderr_tail": result.stderr[-2000:],
    }
    if result.returncode != 0:
        return None, item
    try:
        payload = json.loads(out.read_text(encoding="utf-8"))
        module = payload["modules"][0]
        item.update(
            {
                "hash": module.get("hash"),
                "q6_available": ((module.get("q6_probe_targets") or {}).get("available")),
                "final_store_count": (((module.get("q6_probe_targets") or {}).get("final_store_value_flow") or {}).get("final_store_count")),
            }
        )
    except Exception as exc:  # pragma: no cover - reported as verifier data
        item["parse_error"] = str(exc)
        return None, item
    return out, item


def compare_case(name: str, left: Path, right: Path, out_dir: Path) -> dict[str, Any]:
    out = out_dir / f"{name}.compare.json"
    result = run(["python3", str(COMPARE), str(left), str(right), "--json-out", str(out)])
    item: dict[str, Any] = {
        "name": name,
        "returncode": result.returncode,
        "comparison": str(out),
        "stderr_tail": result.stderr[-2000:],
    }
    if out.exists():
        payload = json.loads(out.read_text(encoding="utf-8"))
        item["all_match"] = payload.get("all_match")
        item["q6_static_boundary"] = payload.get("q6_static_boundary")
        item["first_mismatches"] = [
            {
                "name": comparison.get("name"),
                "first_mismatch_path": comparison.get("first_mismatch_path"),
            }
            for comparison in payload.get("comparisons", [])
            if isinstance(comparison, dict) and comparison.get("match") is not True
        ][:8]
    return item


def verify(out_path: Path | None = None) -> tuple[int, dict[str, Any]]:
    errors: list[str] = []
    for fixture in FIXTURES.values():
        require_file(fixture, errors)
    result_payload: dict[str, Any] = {
        "schema": "skydnir.q6-spirv-static-fixtures.v1",
        "fixtures": {},
        "comparisons": {},
        "valid": False,
        "errors": errors,
    }
    if errors:
        return 1, result_payload

    if True:
        if out_path is not None:
            tmp_path = out_path.parent / f"{out_path.stem}-artifacts"
            tmp_path.mkdir(parents=True, exist_ok=True)
        else:
            tmp_path = Path(tempfile.mkdtemp(prefix="skydnir-q6-static-"))
        analyses: dict[str, Path] = {}
        for name, fixture in FIXTURES.items():
            analysis_path, item = analyze(name, fixture, tmp_path)
            result_payload["fixtures"][name] = item
            if analysis_path is None:
                errors.append(f"analysis failed for {name}")
            else:
                analyses[name] = analysis_path
        if not errors:
            result_payload["comparisons"]["safe_self"] = compare_case(
                "safe-self", analyses["safe"], analyses["safe"], tmp_path
            )
            result_payload["comparisons"]["native_effective"] = compare_case(
                "native-effective", analyses["native"], analyses["effective"], tmp_path
            )
            result_payload["comparisons"]["safe_native"] = compare_case(
                "safe-native", analyses["safe"], analyses["native"], tmp_path
            )

            safe_self = result_payload["comparisons"]["safe_self"]
            native_effective = result_payload["comparisons"]["native_effective"]
            safe_native = result_payload["comparisons"]["safe_native"]

            if safe_self.get("returncode") != 0 or safe_self.get("all_match") is not True:
                errors.append("safe fixture self-compare must be q6-static-match")
            if (safe_self.get("q6_static_boundary") or {}).get("summary") != "q6-static-match":
                errors.append("safe fixture self-compare summary changed")
            if native_effective.get("returncode") != 2:
                errors.append("native-vs-effective fixture compare must remain a static mismatch")
            if (native_effective.get("q6_static_boundary") or {}).get("summary") != "q6-final-store-execution-shape":
                errors.append("native-vs-effective summary must stay q6-final-store-execution-shape")
            mismatch_paths = [item.get("first_mismatch_path") for item in native_effective.get("first_mismatches") or []]
            if "local_size[0]" not in mismatch_paths and not any(
                str(path).startswith("q6_final_store_execution_shape") for path in mismatch_paths
            ):
                errors.append("native-vs-effective must expose local-size/final-store execution-shape drift")
            if safe_native.get("returncode") != 2 or safe_native.get("all_match") is not False:
                errors.append("safe-vs-native must remain non-promoting static mismatch")
            if (safe_native.get("q6_static_boundary") or {}).get("summary") == "q6-static-match":
                errors.append("safe-vs-native must not be promoted as q6-static-match")

    result_payload["errors"] = errors
    result_payload["valid"] = not errors
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return (0 if not errors else 1), result_payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    rc, payload = verify(args.json_out)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
