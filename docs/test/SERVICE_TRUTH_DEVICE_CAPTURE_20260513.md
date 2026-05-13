# Service Truth Device Capture - 2026-05-13

This note records the sanitized outcome of the local device service-truth
capture.  The raw device archive is intentionally kept out of git under
`docs/test/device-captures/` because it can contain volatile process, project,
and log snippets from the test device.

## Device Run

- Target: `llama`
- Artifact kind: `service-truth`
- Artifact status: `planned-gap`
- Success: `false`

## Observed Summary

| Field | Value |
| --- | --- |
| Engine CLI exit code | `0` |
| Engine API `/containers/json` status | `HTTP/1.0 200 OK` |
| Running Engine container IDs | `0` |
| Configured ports seen in project metadata | `18080 18081 5901 8080 8081 8083` |
| Selected Engine container ID | `null` |
| Persisted state matched selected Engine ID | `false` |
| Listener `/proc/net/tcp` matched ports | empty |

## Interpretation

The capture proves that the service-truth runner can collect Engine API,
`docker ps`, persisted state, listener, process-table, and UI-input evidence on
device, but it does **not** prove a live llama service.  The result must remain
non-passing until the UI rendered card, Engine API, persisted state, process
table, listener probe, and current container logs all agree on one current
Engine container ID.

This is expected to remain a release-blocking planned gap for service health
truth until a real same-container-ID artifact is produced.
