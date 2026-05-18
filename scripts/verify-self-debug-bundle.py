#!/usr/bin/env python3
"""Lightweight verifier for pdocker ADB-free self-debug bundles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA = "pdocker.self-debug.bundle.v1"
LATEST_DOCUMENTS_TARGET = "pdocker/diagnostics/self-debug-bundle-latest.json"
EVIDENCE_DOCUMENTS_PREFIX = "pdocker/diagnostics/self-debug-bundle-"
EVIDENCE_DOCUMENTS_SUFFIX = ".json"
MAX_BUNDLE_JOBS = 10
MAX_ACTIVE_OPERATIONS = 10
MAX_JOB_OUTPUT_LINES = 20
MAX_JOB_LOG_EXCERPT_BYTES = 32 * 1024


class VerificationError(AssertionError):
    pass


def _fail(message: str) -> None:
    raise VerificationError(message)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _obj(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{path} must be an object")
    return value


def _arr(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{path} must be an array")
    return value


def _nonempty_str(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{path} must be a non-empty string")
    return value


def _bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{path} must be a boolean")
    return value


def _number(value: Any, path: str) -> int | float:
    if not _is_number(value):
        _fail(f"{path} must be numeric")
    return value


def _has_explicit_error(obj: dict[str, Any]) -> bool:
    for key in ("Error", "Reason", "Message", "Detail"):
        if isinstance(obj.get(key), str) and obj[key].strip():
            return True
    for attempt in obj.get("Attempts", []):
        if isinstance(attempt, dict) and _has_explicit_error(attempt):
            return True
    return False


def _first_present(obj: dict[str, Any], names: tuple[str, ...]) -> tuple[str, Any] | tuple[None, None]:
    for name in names:
        if name in obj:
            return name, obj[name]
    return None, None


def _utf8_len(value: str) -> int:
    return len(value.encode("utf-8"))


def _verify_app_owned_job_log_path(value: Any, path: str, package_name: str, job_id: str | None = None) -> None:
    log_path = _nonempty_str(value, path)
    if "\x00" in log_path or ".." in Path(log_path).parts:
        _fail(f"{path} must be an app-owned job log path")
    allowed_prefixes = (
        f"/data/user/0/{package_name}/files/pdocker/logs/jobs/",
        f"/data/data/{package_name}/files/pdocker/logs/jobs/",
    )
    if not any(log_path.startswith(prefix) for prefix in allowed_prefixes):
        _fail(f"{path} must be an app-owned job log path")
    name = Path(log_path).name
    if not name.endswith(".log") or name in (".log", ""):
        _fail(f"{path} must point at a .log file")
    if job_id:
        safe_job_id = job_id.replace("/", "_").replace("\\", "_")
        if name != f"{safe_job_id}.log":
            _fail(f"{path} must match the job id log file")


def _verify_active_operations(value: Any) -> None:
    active = _obj(value, "active_operations")
    if _has_explicit_error(active):
        return
    key, items = _first_present(active, ("Items", "Operations", "ActiveOperations"))
    if key is None:
        _fail("active_operations.Items is required unless an explicit collection error is present")
    operations = _arr(items, f"active_operations.{key}")
    if len(operations) > MAX_ACTIVE_OPERATIONS:
        _fail(f"active_operations.{key} must contain at most {MAX_ACTIVE_OPERATIONS} operations")
    if "Count" in active:
        count = _number(active.get("Count"), "active_operations.Count")
        if int(count) < len(operations):
            _fail("active_operations.Count must be greater than or equal to the included operation count")
    if "Source" in active:
        _nonempty_str(active.get("Source"), "active_operations.Source")
    for index, operation in enumerate(operations):
        item = _obj(operation, f"active_operations.{key}[{index}]")
        if not any(isinstance(item.get(name), str) and item.get(name).strip() for name in ("Id", "ID", "id", "OperationId")):
            _fail(f"active_operations.{key}[{index}] must include an operation id")
        for field in ("StartedAt", "UpdatedAt"):
            if field in item:
                _number(item.get(field), f"active_operations.{key}[{index}].{field}")


def _verify_job_output_lines(value: Any, path: str) -> None:
    lines = _arr(value, path)
    if len(lines) > MAX_JOB_OUTPUT_LINES:
        _fail(f"{path} must contain at most {MAX_JOB_OUTPUT_LINES} lines")
    total_bytes = 0
    for index, line in enumerate(lines):
        text = _nonempty_str(line, f"{path}[{index}]")
        total_bytes += _utf8_len(text) + 1
    if total_bytes > MAX_JOB_LOG_EXCERPT_BYTES:
        _fail(f"{path} must be at most {MAX_JOB_LOG_EXCERPT_BYTES} bytes")


def _verify_job_excerpt_text(value: Any, path: str) -> None:
    text = _nonempty_str(value, path)
    if _utf8_len(text) > MAX_JOB_LOG_EXCERPT_BYTES:
        _fail(f"{path} must be at most {MAX_JOB_LOG_EXCERPT_BYTES} bytes")


def _verify_job_excerpt_object(value: Any, path: str, package_name: str, job_id: str | None) -> None:
    excerpt = _obj(value, path)
    if "Path" in excerpt:
        _verify_app_owned_job_log_path(excerpt.get("Path"), f"{path}.Path", package_name, job_id)
    _bool(excerpt.get("Exists"), f"{path}.Exists")
    _number(excerpt.get("Bytes"), f"{path}.Bytes")
    excerpt_bytes = _number(excerpt.get("ExcerptBytes"), f"{path}.ExcerptBytes")
    if excerpt_bytes > MAX_JOB_LOG_EXCERPT_BYTES:
        _fail(f"{path}.ExcerptBytes must be at most {MAX_JOB_LOG_EXCERPT_BYTES}")
    _bool(excerpt.get("Truncated"), f"{path}.Truncated")
    text = excerpt.get("Text")
    if not isinstance(text, str):
        _fail(f"{path}.Text must be a string")
    if _utf8_len(text) > MAX_JOB_LOG_EXCERPT_BYTES:
        _fail(f"{path}.Text must be at most {MAX_JOB_LOG_EXCERPT_BYTES} bytes")
    if "Error" in excerpt:
        _nonempty_str(excerpt.get("Error"), f"{path}.Error")
        if "Type" in excerpt:
            _nonempty_str(excerpt.get("Type"), f"{path}.Type")


def _verify_jobs(value: Any, package_name: str) -> None:
    jobs = _obj(value, "jobs")
    if _has_explicit_error(jobs):
        return
    policy = jobs.get("JobLogPathPolicy", jobs.get("LogPathPolicy"))
    if policy != "app-owned":
        _fail("jobs.JobLogPathPolicy must be app-owned")
    key, items = _first_present(jobs, ("Items", "Jobs", "DockerJobs"))
    if key is None:
        _fail("jobs.Items is required unless an explicit collection error is present")
    job_items = _arr(items, f"jobs.{key}")
    if len(job_items) > MAX_BUNDLE_JOBS:
        _fail(f"jobs.{key} must contain at most {MAX_BUNDLE_JOBS} jobs")
    if "Count" in jobs:
        count = _number(jobs.get("Count"), "jobs.Count")
        if int(count) < len(job_items):
            _fail("jobs.Count must be greater than or equal to the included job count")
    for index, job in enumerate(job_items):
        item = _obj(job, f"jobs.{key}[{index}]")
        _, raw_id = _first_present(item, ("id", "Id", "ID"))
        job_id = _nonempty_str(raw_id, f"jobs.{key}[{index}].id")
        if "/" in job_id or "\\" in job_id or "\x00" in job_id:
            _fail(f"jobs.{key}[{index}].id must be a safe file name component")
        if not any(isinstance(item.get(name), str) and item.get(name).strip() for name in ("status", "Status")):
            _fail(f"jobs.{key}[{index}] must include status")
        if not any(isinstance(item.get(name), str) and item.get(name).strip() for name in ("command", "Command", "title", "Title")):
            _fail(f"jobs.{key}[{index}] must include command or title")
        saw_excerpt = False
        for line_key in ("output", "Output", "OutputLines", "OutputTail", "LogExcerptLines"):
            if line_key in item:
                _verify_job_output_lines(item.get(line_key), f"jobs.{key}[{index}].{line_key}")
                saw_excerpt = True
        for text_key in ("OutputExcerpt",):
            if text_key in item:
                _verify_job_excerpt_text(item.get(text_key), f"jobs.{key}[{index}].{text_key}")
                saw_excerpt = True
        if "LogExcerpt" in item:
            log_excerpt = item.get("LogExcerpt")
            if isinstance(log_excerpt, dict):
                _verify_job_excerpt_object(log_excerpt, f"jobs.{key}[{index}].LogExcerpt", package_name, job_id)
            else:
                _verify_job_excerpt_text(log_excerpt, f"jobs.{key}[{index}].LogExcerpt")
            saw_excerpt = True
        if "LogExcerptBytes" in item:
            size = _number(item.get("LogExcerptBytes"), f"jobs.{key}[{index}].LogExcerptBytes")
            if size > MAX_JOB_LOG_EXCERPT_BYTES:
                _fail(f"jobs.{key}[{index}].LogExcerptBytes must be at most {MAX_JOB_LOG_EXCERPT_BYTES}")
        for path_key in ("LogPath", "LogFile"):
            if path_key in item:
                _verify_app_owned_job_log_path(item.get(path_key), f"jobs.{key}[{index}].{path_key}", package_name, job_id)
        if not saw_excerpt:
            _fail(f"jobs.{key}[{index}] must include bounded log excerpt evidence")


def _verify_engine_probe(name: str, value: Any) -> None:
    probe = _obj(value, f"engine.{name}")
    if "Error" in probe:
        _nonempty_str(probe.get("Error"), f"engine.{name}.Error")
        if "Type" in probe:
            _nonempty_str(probe.get("Type"), f"engine.{name}.Type")
        return
    if name == "Ping":
        _number(probe.get("Status"), "engine.Ping.Status")
        _nonempty_str(probe.get("Text"), "engine.Ping.Text")
        return
    if name == "ContainersAll":
        _number(probe.get("Status"), "engine.ContainersAll.Status")
        _arr(probe.get("Items"), "engine.ContainersAll.Items")
        return
    if "Status" in probe:
        _number(probe.get("Status"), f"engine.{name}.Status")
        _nonempty_str(probe.get("Error"), f"engine.{name}.Error")
        return
    _number(probe.get("_HttpStatus"), f"engine.{name}._HttpStatus")


def _verify_documents_export(key: str, value: Any, expected_target: str | None) -> None:
    export = _obj(value, key)
    _nonempty_str(export.get("Source"), f"{key}.Source")
    target = _nonempty_str(export.get("Target"), f"{key}.Target")
    _nonempty_str(export.get("MimeType"), f"{key}.MimeType")
    if export["MimeType"] != "application/json":
        _fail(f"{key}.MimeType must be application/json")
    if expected_target is not None and target != expected_target:
        _fail(f"{key}.Target must be {expected_target}")
    if expected_target is None:
        if not (target.startswith(EVIDENCE_DOCUMENTS_PREFIX) and target.endswith(EVIDENCE_DOCUMENTS_SUFFIX)):
            _fail(f"{key}.Target must be timestamped self-debug bundle path")
        stamp = target[len(EVIDENCE_DOCUMENTS_PREFIX):-len(EVIDENCE_DOCUMENTS_SUFFIX)]
        if not stamp.isdigit():
            _fail(f"{key}.Target must be timestamped self-debug bundle path")
    if "PersistedWriteGrant" in export:
        _bool(export.get("PersistedWriteGrant"), f"{key}.PersistedWriteGrant")
    if "PathValidationPolicy" in export:
        if export.get("PathValidationPolicy") != "fail-closed":
            _fail(f"{key}.PathValidationPolicy must be fail-closed")
    success = _bool(export.get("Success"), f"{key}.Success")
    if "Attempts" in export:
        _arr(export.get("Attempts"), f"{key}.Attempts")
    if success:
        _number(export.get("Bytes"), f"{key}.Bytes")
        _nonempty_str(export.get("Mode"), f"{key}.Mode")
    elif not _has_explicit_error(export):
        _fail(f"{key} failed/planned export must include an explicit Error/Reason/Message")


def verify(path: str | Path) -> dict[str, Any]:
    bundle_path = Path(path)
    try:
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"invalid JSON: {exc}")
    bundle = _obj(bundle, "bundle")

    if bundle.get("schema") != SCHEMA:
        _fail(f"schema must be {SCHEMA}")
    if bundle.get("adb_independent") is not True:
        _fail("adb_independent must be true")
    if bundle.get("requires_adb") is not False:
        _fail("requires_adb must be false")
    _number(bundle.get("created_at_epoch_ms"), "created_at_epoch_ms")

    app = _obj(bundle.get("app"), "app")
    for field in ("Package", "Version", "BuildGitCommit", "BuildTimeUtc", "Device", "Abi"):
        _nonempty_str(app.get(field), f"app.{field}")
    package_name = _nonempty_str(app.get("Package"), "app.Package")
    _number(app.get("Uid"), "app.Uid")
    _number(app.get("SdkInt"), "app.SdkInt")

    engine = _obj(bundle.get("engine"), "engine")
    for name in ("Ping", "Version", "Info", "ContainersAll"):
        if name not in engine:
            _fail(f"engine.{name} is required")
        _verify_engine_probe(name, engine[name])

    documents = _obj(bundle.get("documents"), "documents")
    _obj(documents.get("Metadata"), "documents.Metadata")
    grant = _obj(documents.get("PersistedGrant"), "documents.PersistedGrant")
    _bool(grant.get("Read"), "documents.PersistedGrant.Read")
    _bool(grant.get("Write"), "documents.PersistedGrant.Write")

    roots = _arr(bundle.get("debug_roots"), "debug_roots")
    if not roots:
        _fail("debug_roots must not be empty")
    for index, root in enumerate(roots):
        item = _obj(root, f"debug_roots[{index}]")
        for field in ("Label", "Path", "Summary"):
            _nonempty_str(item.get(field), f"debug_roots[{index}].{field}")
        _bool(item.get("Writable"), f"debug_roots[{index}].Writable")
        _bool(item.get("Exists"), f"debug_roots[{index}].Exists")

    layers = _obj(bundle.get("memory_layers"), "memory_layers")
    for field in (
        "OsMemTotal",
        "OsMemAvailable",
        "OsSwapTotal",
        "OsSwapFree",
        "PdockerProcessCount",
        "PdockerRss",
        "PdockerSwap",
        "ManagedReserveBytes",
        "ManagedResidentBytes",
    ):
        _number(layers.get(field), f"memory_layers.{field}")
    _bool(layers.get("TransparentRegistered"), "memory_layers.TransparentRegistered")
    _nonempty_str(layers.get("Source"), "memory_layers.Source")

    for field in ("memory_snapshot_text", "process_snapshot_text", "handle_snapshot_text"):
        _nonempty_str(bundle.get(field), field)

    _verify_active_operations(bundle.get("active_operations"))
    _verify_jobs(bundle.get("jobs"), package_name)

    local = _obj(bundle.get("LocalEvidenceFiles"), "LocalEvidenceFiles")
    for field in ("Latest", "Timestamped"):
        _nonempty_str(local.get(field), f"LocalEvidenceFiles.{field}")

    _verify_documents_export("DocumentsExport", bundle.get("DocumentsExport"), LATEST_DOCUMENTS_TARGET)
    _verify_documents_export("DocumentsEvidenceExport", bundle.get("DocumentsEvidenceExport"), None)
    if "DocumentsExportRetry" in bundle:
        _verify_documents_export("DocumentsExportRetry", bundle.get("DocumentsExportRetry"), LATEST_DOCUMENTS_TARGET)

    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="self-debug-bundle-latest.json to verify")
    args = parser.parse_args(argv)
    try:
        verify(args.bundle)
    except VerificationError as exc:
        print(f"self-debug bundle verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {args.bundle} is a valid {SCHEMA} bundle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
