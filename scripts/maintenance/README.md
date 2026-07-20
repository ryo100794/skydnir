# Maintenance script implementations

This directory holds implementation paths for generated documentation,
evidence maintenance, and guarded repository hygiene scripts. Prefer stable
top-level wrappers in durable docs and commands until a focused inventory
migration updates every caller.

| Implementation | Stable wrapper | Role |
|---|---|---|
| `analyze-q6-stage-trace-spvasm.py` | none | Static Q6 SPIR-V assembly trace summarizer used by llama GPU bridge diagnostics. Historical evidence analysis only; it must not become a llama.cpp- or hash-specialized runtime path. |
| `clean-development-artifacts.py` | none | Safe dry-run/apply cleanup for repository-local development caches and scratch files. It protects ignored APK/runtime payloads, machine-local config, and directories containing tracked evidence. |
| `generate-skydnir-rename-inventory.py` | none | Regenerates the Skydnir rename inventory artifacts under `docs/maintenance/`. |
| `summarize-llama-gpu-artifacts.py` | [`../summarize-llama-gpu-artifacts.py`](../summarize-llama-gpu-artifacts.py) | Summarizes existing llama GPU artifacts using the current verifier rules. |
| `summarize-q6k-evidence.py` | none | Produces the Q6_K evidence inventory used by static GPU bridge review and test evidence ledgers. |
| `verify-q6-workgroup-lowering-preflight.py` | none | Preflight verifier for planned Q6 workgroup/lowering evidence. It lives here because it maintains diagnostic evidence quality rather than invoking a device run. |

Policy:

- Prefer stable top-level wrappers in runbooks, docs, and CI commands.
- Keep wrappers until repository references have deliberately migrated.
- Update `scripts/script-inventory.json`, [`../README.md`](../README.md), and
  verification guards only in focused inventory changes.
- Use `clean-development-artifacts.py` before manual deletion. Its default
  dry-run protects ignored APK/runtime payloads that `git clean -Xdf` would
  remove.
- Do not delete tracked evidence just because it has a generated-looking name or
  sits under an ignored evidence prefix. Evidence pruning requires a separate
  retention-policy change that updates producers, consumers, and indexes.
