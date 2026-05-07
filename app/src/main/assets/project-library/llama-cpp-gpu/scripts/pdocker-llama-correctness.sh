#!/usr/bin/env bash
set -euo pipefail

port="${LLAMA_ARG_PORT:-18081}"
out="${LLAMA_CORRECTNESS_FILE:-/workspace/logs/pdocker-llama-correctness.json}"
profile_copy="${LLAMA_CORRECTNESS_PROFILE_COPY:-/profiles/pdocker-llama-correctness.json}"
timeout="${LLAMA_CORRECTNESS_TIMEOUT:-90}"

mkdir -p "$(dirname "$out")"
if [[ -n "$profile_copy" ]]; then
  mkdir -p "$(dirname "$profile_copy")"
fi

python3 - "$port" "$out" "$profile_copy" "$timeout" <<'PY'
import json
import os
import sys
import time
import urllib.request


port, out_path, profile_copy, timeout_text = sys.argv[1:5]
timeout = float(timeout_text)
base_url = f"http://127.0.0.1:{port}"
created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def request_json(path, payload=None, request_timeout=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base_url + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=request_timeout or timeout) as response:
        body = response.read().decode("utf-8", "replace")
        return response.status, json.loads(body) if body else {}


health = {"ok": False, "status_code": None, "error": None}
try:
    code, body = request_json("/health", request_timeout=10)
    health.update({"ok": 200 <= code < 300, "status_code": code, "body": body})
except Exception as exc:  # exercised in the Android/container runtime
    health["error"] = f"{type(exc).__name__}: {exc}"

probes = [
    {
        "name": "simple_addition",
        "prompt": "2+3=",
        "expected_prefixes": ["5"],
        "required": True,
    },
    {
        "name": "simple_multiplication",
        "prompt": "12*7=",
        "expected_prefixes": ["84", "8"],
        "required": False,
    },
]

results = []
for probe in probes:
    payload = {
        "prompt": probe["prompt"],
        "n_predict": 1,
        "temperature": 0,
        "top_k": 1,
        "top_p": 1,
        "cache_prompt": False,
        "stop": ["\n"],
    }
    result = {
        "name": probe["name"],
        "prompt": probe["prompt"],
        "expected_prefixes": probe["expected_prefixes"],
        "required": probe["required"],
        "passed": False,
        "content": "",
        "error": None,
        "duration_ms": None,
    }
    started = time.monotonic()
    try:
        code, body = request_json("/completion", payload, request_timeout=timeout)
        content = str(body.get("content", ""))
        normalized = content.lstrip()
        result.update(
            {
                "status_code": code,
                "content": content,
                "passed": any(
                    normalized.startswith(prefix)
                    for prefix in probe["expected_prefixes"]
                ),
            }
        )
    except Exception as exc:  # exercised in the Android/container runtime
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["duration_ms"] = round((time.monotonic() - started) * 1000.0, 3)
    results.append(result)

required_failures = [
    item for item in results if item["required"] and not item["passed"]
]
optional_failures = [
    item for item in results if not item["required"] and not item["passed"]
]
gpu_requested = os.environ.get("PDOCKER_GPU_MODE", "").lower() not in {
    "",
    "cpu",
    "none",
    "off",
}
try:
    gpu_layers = int(os.environ.get("LLAMA_ARG_N_GPU_LAYERS", "") or "0")
except ValueError:
    gpu_layers = -1
gpu_mode_active = gpu_requested and gpu_layers != 0

if required_failures:
    correctness = "fail"
elif health["ok"]:
    correctness = "pass"
else:
    correctness = "unknown"

report = {
    "schema": "pdocker.llama.correctness.v1",
    "created_at": created_at,
    "service": {
        "base_url": base_url,
        "health": health,
    },
    "runtime": {
        "gpu_requested": gpu_requested,
        "gpu_layers": gpu_layers,
        "pdocker_gpu_mode": os.environ.get("PDOCKER_GPU_MODE", ""),
        "llama_gpu_backend": os.environ.get("LLAMA_GPU_BACKEND", ""),
        "vulkan_icd_kind": os.environ.get("PDOCKER_VULKAN_ICD_KIND", ""),
        "model": os.environ.get("LLAMA_ARG_MODEL", ""),
    },
    "probes": results,
    "summary": {
        "correctness": correctness,
        "gpu_correctness": correctness if gpu_mode_active else "not-applicable",
        "required_failures": len(required_failures),
        "optional_failures": len(optional_failures),
        "benchmark_claim_allowed": correctness == "pass",
    },
}

text = json.dumps(report, indent=2, sort_keys=True)
with open(out_path, "w", encoding="utf-8") as handle:
    handle.write(text + "\n")
if profile_copy:
    with open(profile_copy, "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
print(text)
if correctness != "pass":
    raise SystemExit(1)
PY
