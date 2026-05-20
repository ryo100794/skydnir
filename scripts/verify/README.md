# Verification helper subtrees

Top-level `scripts/verify-*.py` and `scripts/verify-*.sh` files remain the
public verification gates. This subtree is for helper/orchestrator files that
support those gates without expanding the stable top-level script surface.

- Runner helpers: [`runner/README.md`](runner/README.md)
- Stable script inventory and wrapper policy: [`../README.md`](../README.md)

Files under `scripts/verify/runner/` are tracked as `subtree_entries` in
[`../script-inventory.json`](../script-inventory.json), so inventory checks can
classify them without changing public entrypoint counts.
