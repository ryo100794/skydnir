# Refactor Resilience

Snapshot date: 2026-05-05.

Refactor-resilience tests protect externally observable contracts while allowing
the implementation to move. They must not lock private function names, temporary
paths, source layout, or accidental behavior.

The machine-readable ledger is:

```sh
tests/refactor_resilience_cases.json
```

The fast verifier is:

```sh
python3 scripts/verify-refactor-resilience.py
python3 scripts/verify-refactor-resilience.py \
  --write-artifact docs/test/refactor-resilience-latest.json
```

## Bug Fossilization Guard

Golden expectations can accidentally turn a bug into a permanent contract. Every
refactor-resilience case therefore declares `contract_class`:

| Class | Meaning |
|---|---|
| `intended` | The behavior is intended to remain stable. |
| `documented-limitation` | The behavior is a scoped compatibility limitation and must stay explicitly documented. |
| `known-bug-blocker` | The behavior is a bug or incomplete feature and must not become a passing runnable invariant. |

A runnable case cannot use `known-bug-blocker`. Known bugs must remain planned
gaps or failing blockers until fixed.

## Required Axes

| Axis | Purpose |
|---|---|
| Engine API golden corpus | Stable Docker-shaped API status classes, keys, and error bodies. |
| Compose/Dockerfile fixtures | Portable project definitions and standard Dockerfile syntax. |
| Archive round-trip | Tar/stat/file metadata compatibility across storage refactors. |
| State-machine contract | Container, image, job, UI card, daemon, and `docker ps` consistency. |
| Abnormal replay | Stable failure triggers, signals, and evidence artifacts. |
| Artifact diff | Previous accepted artifacts compared with explicit drift classification. |

The current fast lane records runnable checks for Engine API shape, bundled
Compose/Dockerfile fixtures, and abnormal-event replay. Archive round-trip,
state-machine consistency, and artifact diff are still explicit planned gaps.
