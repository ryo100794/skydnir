# Maintenance script implementations

This directory holds implementation paths for generated-documentation and
evidence-maintenance scripts. Prefer the stable top-level wrapper in durable
docs and commands until a focused inventory migration updates every caller.

| Implementation | Stable wrapper | Role |
|---|---|---|
| `summarize-llama-gpu-artifacts.py` | [`../summarize-llama-gpu-artifacts.py`](../summarize-llama-gpu-artifacts.py) | Summarizes existing llama GPU artifacts using the current verifier rules. |

Policy:

- Prefer stable top-level wrappers in runbooks, docs, and CI commands.
- Keep wrappers until repository references have deliberately migrated.
- Update `scripts/script-inventory.json`, [`../README.md`](../README.md), and
  verification guards only in focused inventory changes.
