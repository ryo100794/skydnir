# Maintenance script implementations

This directory holds implementation paths for generated-documentation and
evidence-maintenance scripts. Prefer the stable top-level wrapper in durable
docs and commands until a focused inventory migration updates every caller.

| Implementation | Stable wrapper | Role |
|---|---|---|
| `summarize-llama-gpu-artifacts.py` | [`../summarize-llama-gpu-artifacts.py`](../summarize-llama-gpu-artifacts.py) | Summarizes existing llama GPU artifacts using the current verifier rules. |
| `clean-development-artifacts.py` | none | Safe dry-run/apply cleanup for repository-local development caches and scratch files; protects ignored APK/runtime payloads. |

Policy:

- Prefer stable top-level wrappers in runbooks, docs, and CI commands.
- Keep wrappers until repository references have deliberately migrated.
- Update `scripts/script-inventory.json`, [`../README.md`](../README.md), and
  verification guards only in focused inventory changes.
- Use `clean-development-artifacts.py` before manual deletion. Its default
  dry-run protects ignored APK/runtime payloads that `git clean -Xdf` would
  remove.
