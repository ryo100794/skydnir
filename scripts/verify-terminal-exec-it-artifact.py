#!/usr/bin/env python3
"""Verify the terminal Engine exec -it device artifact.

This is intentionally a host-side verifier: a top-level Success=true JSON file is
not sufficient.  A passing artifact must be paired with the raw
engine-exec-input-latest.jsonl emitted by EngineExecSession on the device, and
that JSONL must prove a real Engine exec stream, input bytes, resize route, and
container/exec-id consistency.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REQUIRED_EVIDENCE = [
    "enter-single-submit",
    "enter-no-duplicate-submit",
    "ctrl-c-interrupts-without-literal-c",
    "jp-en-ctrl-c-isolated-etx",
    "arrow-up-reaches-readline-history",
    "arrow-up-no-escape-text",
    "ime-enter-ctrlc-regression-covered",
    "top-starts-on-tty",
    "top-refresh-observed-before-q",
    "top-repaint-remains-terminal-shaped",
    "q-quits-top",
    "top-q-shell-recovery",
    "resize-route-is-observable",
    "selection-keyboard-suppression",
]

TOP_REFRESH_MARKERS = ("PID", "Tasks:", "Task:", "Mem:", "CPU:", "load average", "Load Avg")
HEX_ARROW_UP_ENTER = "1b 5b 41 0d"
HEX_CTRL_C = "03"
HEX_Q = "71"


class VerificationError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise VerificationError(f"missing UI exec-it artifact: {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid UI exec-it artifact JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise VerificationError("UI exec-it artifact must be a JSON object")
    return data


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise VerificationError(f"missing Engine exec input diagnostics: {path}")
    events: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise VerificationError(f"invalid Engine exec JSONL at line {line_no}: {exc}") from exc
        if not isinstance(event, dict):
            raise VerificationError(f"Engine exec JSONL line {line_no} is not an object")
        events.append(event)
    if not events:
        raise VerificationError("Engine exec input diagnostics are empty")
    return events


def _event_name(event: dict[str, Any]) -> str:
    value = event.get("event", "")
    return value if isinstance(value, str) else ""


def _events(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [event for event in events if _event_name(event) == name]


def _first_index(events: list[dict[str, Any]], name: str) -> int:
    for index, event in enumerate(events):
        if _event_name(event) == name:
            return index
    raise VerificationError(f"Engine exec diagnostics missing event: {name}")


def _hex(event: dict[str, Any]) -> str:
    value = event.get("hex", "")
    return value.lower().strip() if isinstance(value, str) else ""


def _hex_tokens(event: dict[str, Any]) -> list[str]:
    return [token for token in _hex(event).split() if token]


def _text(event: dict[str, Any]) -> str:
    value = event.get("text", "")
    return value if isinstance(value, str) else ""


def _event_body(event: dict[str, Any]) -> str:
    value = event.get("body", "")
    return value if isinstance(value, str) else ""


def _event_exec_id(event: dict[str, Any]) -> str:
    value = event.get("execId", "")
    return value if isinstance(value, str) else ""


def _timestamp_ms(event: dict[str, Any]) -> float:
    value = event.get("timestampMs")
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    name = _event_name(event) or "<unknown>"
    raise VerificationError(f"Engine exec diagnostics event {name} missing numeric timestampMs")


def _validate_input_hex(event: dict[str, Any]) -> None:
    tokens = _hex_tokens(event)
    _require(tokens, "Engine exec input diagnostics event missing hex bytes")
    bad_tokens = [token for token in tokens if not re.fullmatch(r"[0-9a-f]{2}", token)]
    _require(not bad_tokens, "Engine exec input diagnostics contain invalid hex byte tokens: " + ", ".join(bad_tokens))
    byte_count = event.get("bytes")
    _require(isinstance(byte_count, int) and byte_count == len(tokens), "Engine exec input diagnostics bytes count must match hex byte count")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def _verify_planned_skip(artifact: dict[str, Any], require_container: bool) -> None:
    _require(artifact.get("Success") is not True, "UI exec-it planned-skip must never report Success=true")
    _require(
        artifact.get("DeviceProofAttempted") is not True,
        "UI exec-it planned-skip must not claim device proof was attempted",
    )
    raise VerificationError("UI exec-it requires a real container; planned-skip is not a pass")


def _verify_success_json(artifact: dict[str, Any], require_container: bool) -> str:
    _require(artifact.get("Name") == "ui-engine-exec-it", "UI exec-it artifact has wrong or missing Name")
    if require_container:
        _require(bool(artifact.get("Container")), "UI exec-it hard gate artifact is missing Container")
    _require(artifact.get("Success") is True, f"UI exec-it self-test failed: {artifact.get('Error', artifact)}")
    if artifact.get("DeviceProofAttempted") is False:
        raise VerificationError("UI exec-it success artifact claims DeviceProofAttempted=false")
    _require(isinstance(artifact.get("StartedAtMs"), (int, float)) and artifact.get("StartedAtMs") >= 0, "UI exec-it success artifact missing StartedAtMs device timestamp")
    _require(isinstance(artifact.get("DurationMs"), (int, float)) and artifact.get("DurationMs") >= 0, "UI exec-it success artifact missing non-negative DurationMs")
    container = str(artifact.get("Container", "")).strip()
    _require(bool(container), "UI exec-it success artifact is missing Container")

    artifact_required = set(artifact.get("RequiredEvidence") or [])
    evidence = artifact.get("Evidence") or {}
    _require(isinstance(evidence, dict), "UI exec-it Evidence must be an object")
    missing_required = [name for name in REQUIRED_EVIDENCE if name not in artifact_required]
    _require(not missing_required, "UI exec-it artifact RequiredEvidence missing: " + ", ".join(missing_required))
    missing_flags = [name for name in REQUIRED_EVIDENCE if evidence.get(name) is not True]
    _require(not missing_flags, "UI exec-it artifact Evidence flags not true: " + ", ".join(missing_flags))

    tail = str(artifact.get("OutputTail", ""))
    _require(tail.strip(), "UI exec-it success artifact missing OutputTail")
    top_marker_matches = list(re.finditer(r"(?im)(\bPID\b|Tasks?:|Mem:|CPU:|load average|Load Avg)", tail))
    topq_index = tail.find("pdocker-ui-it-topq-ok")
    top_ok_index = tail.find("pdocker-ui-it-top-ok")
    checks = {
        "enter-single-submit": "pdocker-ui-it-ok" in tail,
        "enter-no-duplicate-submit": tail.count("pdocker-ui-it-ok") == 1,
        "ctrl-c-interrupts-without-literal-c": "pdocker-ui-it-ctrlc-ok" in tail and "sleep 15c" not in tail,
        "jp-en-ctrl-c-isolated-etx": "pdocker-ui-it-ctrlc-ok" in tail and "sleep 15c" not in tail,
        "arrow-up-reaches-readline-history": tail.count("pdocker-ui-it-arrow-seed") >= 2 and "\u001b[A" not in tail,
        "arrow-up-no-escape-text": "\u001b[A" not in tail and "^[[A" not in tail,
        "ime-enter-ctrlc-regression-covered": "pdocker-ui-it-ime-enter-ok" in tail and "pdocker-ui-it-ctrlc-ok" in tail and "sleep 15c" not in tail,
        "top-starts-on-tty": "pdocker-ui-it-top-ok" in tail,
        "top-refresh-observed-before-q": len(top_marker_matches) >= 2 and topq_index > top_marker_matches[-1].start(),
        "top-repaint-remains-terminal-shaped": evidence.get("top-repaint-remains-terminal-shaped") is True and len(top_marker_matches) >= 2,
        "q-quits-top": topq_index >= 0 and topq_index > top_ok_index >= 0,
        "top-q-shell-recovery": topq_index >= 0 and topq_index > top_ok_index >= 0,
        # Checked against JSONL below; stream-started alone is not accepted.
        "resize-route-is-observable": True,
        # Selection suppression is a UI-surface artifact flag; static contract tests
        # keep the hook generic and session-neutral.
        "selection-keyboard-suppression": evidence.get("selection-keyboard-suppression") is True,
    }
    missing = [name for name, ok in checks.items() if not ok]
    _require(not missing, "UI exec-it evidence missing from output tail: " + ", ".join(missing))
    _require(not re.search(r"(/usr/bin/)?\[: extra argument", tail), "UI exec-it output contains bracket argv noise")
    _require("\\e[A" not in tail, "UI exec-it output contains textual ArrowUp escape marker")
    _require("pdocker-ui-it-ok\r\n" in tail or "pdocker-ui-it-ok\n" in tail, "UI exec-it did not preserve terminal CRLF line control")
    return container


def _verify_jsonl(events: list[dict[str, Any]], container: str, artifact: dict[str, Any]) -> None:
    timestamps = [_timestamp_ms(event) for event in events]
    _require(timestamps == sorted(timestamps), "Engine exec diagnostics timestampMs values are not monotonic")
    started_at = float(artifact.get("StartedAtMs", 0))
    duration = float(artifact.get("DurationMs", 0))
    completed_at = started_at + duration
    slack_ms = 30_000.0
    _require(
        all(started_at - slack_ms <= ts <= completed_at + slack_ms for ts in timestamps),
        "Engine exec diagnostics timestamps do not overlap the UI device artifact window",
    )

    start_events = _events(events, "start")
    _require(start_events, "Engine exec diagnostics missing start event")
    start_container = str(start_events[0].get("container", "")).strip()
    _require(start_container == container, f"Engine exec start container mismatch: artifact={container} diagnostics={start_container}")

    create_response_index = _first_index(events, "create-response")
    created_index = _first_index(events, "created")
    start_response_index = _first_index(events, "start-response")
    stream_started_index = _first_index(events, "stream-started")
    first_input_index = _first_index(events, "input")
    _require(create_response_index < created_index < start_response_index < stream_started_index < first_input_index,
             "Engine exec diagnostics are not in create/start/stream/input order")

    create_response = events[create_response_index]
    _require(int(create_response.get("status", 0) or 0) in range(200, 300), "Engine exec create-response was not HTTP 2xx")
    create_body = _event_body(create_response)
    try:
        exec_id_from_body = str(json.loads(create_body).get("Id", ""))
    except json.JSONDecodeError as exc:
        raise VerificationError("Engine exec create-response body is not JSON with Id") from exc
    created_exec_id = _event_exec_id(events[created_index])
    _require(bool(created_exec_id), "Engine exec created event missing execId")
    _require(exec_id_from_body == created_exec_id, "Engine exec create-response Id does not match created execId")

    start_response = events[start_response_index]
    _require(_event_exec_id(start_response) == created_exec_id, "Engine exec start-response execId mismatch")
    _require("HTTP/1.1 101" in _event_body(start_response) or "HTTP/1.0 101" in _event_body(start_response),
             "Engine exec start-response did not prove a hijacked 101 stream")
    _require(_event_exec_id(events[stream_started_index]) == created_exec_id, "Engine exec stream-started execId mismatch")

    resize_events = [event for event in events if _event_name(event) in {"resize", "resize-failed"}]
    _require(resize_events, "Engine exec diagnostics missing resize route event")
    resize_ok = any(
        _event_exec_id(event) == created_exec_id
        and "/resize?h=" in _event_body(event)
        and (_event_name(event) == "resize-failed" or int(event.get("status", 0) or 0) in range(200, 300))
        for event in resize_events
    )
    _require(resize_ok, "Engine exec resize route is not observable for the created execId")

    input_events = _events(events, "input")
    _require(input_events, "Engine exec diagnostics missing input events")
    for event in input_events:
        _validate_input_hex(event)
    texts = [_text(event) for event in input_events]
    hexes = [_hex(event) for event in input_events]
    joined_text = "\n".join(texts)
    joined_hex = "\n".join(hexes)

    _require("p=pdocker-ui-it" in joined_text and "echo ${p}-ok" in joined_text and "arrow-seed" in joined_text,
             "Engine exec input diagnostics missing initial shell script markers")
    _require("top -b -n 1" in joined_text and "top" in joined_text,
             "Engine exec input diagnostics missing top commands")
    _require("printf '%s" in joined_text and "ime-enter-ok" in joined_text,
             "Engine exec input diagnostics missing IME Enter command")
    _require("sleep 15" in joined_text, "Engine exec input diagnostics missing Ctrl-C sleep command")
    _require("ctrlc-ok" in joined_text and "exit" in joined_text,
             "Engine exec input diagnostics missing post-Ctrl-C shell recovery command")
    _require(any(HEX_ARROW_UP_ENTER in h for h in hexes),
             "Engine exec input diagnostics missing ArrowUp+Enter bytes (1b 5b 41 0d)")
    _require(any(h == HEX_CTRL_C or f" {HEX_CTRL_C} " in f" {h} " for h in hexes),
             "Engine exec input diagnostics missing Ctrl-C byte (03)")
    _require(any(_hex_tokens(event) == [HEX_Q] for event in input_events),
             "Engine exec input diagnostics missing q byte (71) for top quit")
    _require("stream-started" in [_event_name(event) for event in events] and "/resize?h=" in "\n".join(_event_body(event) for event in resize_events),
             "Engine exec resize proof must be a resize route, not stream-started alone")
    _require("sleep 15c" not in joined_text, "Engine exec input diagnostics show literal c appended to sleep")
    _require(HEX_CTRL_C in joined_hex, "Engine exec input diagnostics missing raw Ctrl-C byte")

    initial_script_index = next((i for i, event in enumerate(input_events) if "p=pdocker-ui-it" in _text(event)), -1)
    ime_command_index = next((i for i, event in enumerate(input_events) if "ime-enter-ok" in _text(event)), -1)
    sleep_index = next((i for i, event in enumerate(input_events) if "sleep 15" in _text(event)), -1)
    recovery_index = next((i for i, event in enumerate(input_events) if "ctrlc-ok" in _text(event)), -1)
    top_index = next((i for i, event in enumerate(input_events) if _text(event) == "top\r"), -1)
    q_index = next((i for i, event in enumerate(input_events) if _hex_tokens(event) == [HEX_Q]), -1)
    arrow_index = next((i for i, event in enumerate(input_events) if HEX_ARROW_UP_ENTER in _hex(event)), -1)
    ctrl_indexes = [i for i, event in enumerate(input_events) if HEX_CTRL_C in _hex_tokens(event)]

    _require(ime_command_index >= 0, "Engine exec input diagnostics missing IME command event")
    _require(ime_command_index + 1 < len(input_events), "Engine exec input diagnostics missing IME Enter byte after command")
    _require(
        _hex_tokens(input_events[ime_command_index + 1]) == ["0d"],
        "Engine exec IME command must be followed by exactly one Enter byte (0d)",
    )
    _require(
        ime_command_index + 2 >= len(input_events) or _hex_tokens(input_events[ime_command_index + 2]) != ["0d"],
        "Engine exec IME command submitted twice; duplicate Enter byte observed",
    )
    enter_ok_input_count = sum(1 for event in input_events if "echo ${p}-ok" in _text(event))
    _require(
        enter_ok_input_count == 1,
        "Engine exec Enter evidence must have exactly one initial submit command",
    )
    _require(
        initial_script_index >= 0 and arrow_index > initial_script_index,
        "Engine exec ArrowUp history proof must occur after the seed shell script",
    )
    _require(
        top_index >= 0 and q_index > top_index,
        "Engine exec q byte must be sent after foreground top starts",
    )
    top_recovery_index = next((i for i, event in enumerate(input_events) if "topq-ok" in _text(event)), -1)
    _require(
        top_recovery_index > q_index > top_index >= 0,
        "Engine exec top q shell recovery command must occur after q quits foreground top",
    )
    _require(
        sleep_index >= 0 and recovery_index > sleep_index,
        "Engine exec Ctrl-C recovery command must occur after sleep command",
    )
    _require(ctrl_indexes, "Engine exec input diagnostics missing isolated Ctrl-C event")
    _require(
        any(sleep_index < index < recovery_index and _hex_tokens(input_events[index]) == [HEX_CTRL_C] for index in ctrl_indexes),
        "Engine exec Ctrl-C must be an isolated ETX byte between sleep and recovery for JP/EN IME routes",
    )
    bad_ctrl_literal = [
        index
        for index, event in enumerate(input_events[sleep_index + 1 : recovery_index], sleep_index + 1)
        if "63" in _hex_tokens(event) or _hex_tokens(event) == [HEX_CTRL_C, "63"]
    ]
    _require(
        not bad_ctrl_literal,
        "Engine exec Ctrl-C interval contains literal c byte before recovery",
    )


def verify(artifact_path: Path, input_jsonl_path: Path, require_container: bool = False) -> None:
    artifact = _read_json(artifact_path)
    status = str(artifact.get("Status", ""))
    if status == "planned-skip":
        _verify_planned_skip(artifact, require_container=require_container)
        return
    if artifact.get("Success") is True and status == "planned-skip":
        raise VerificationError("UI exec-it planned-skip must never be accepted as success")
    container = _verify_success_json(artifact, require_container=require_container)
    events = _read_jsonl(input_jsonl_path)
    _verify_jsonl(events, container=container, artifact=artifact)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="ui-it-selftest-latest.json")
    parser.add_argument("input_jsonl", type=Path, help="engine-exec-input-latest.jsonl")
    parser.add_argument("--require-container", action="store_true", help="require a non-empty container in success artifacts; planned-skip always fails")
    args = parser.parse_args(argv)
    try:
        verify(args.artifact, args.input_jsonl, require_container=args.require_container)
    except VerificationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
