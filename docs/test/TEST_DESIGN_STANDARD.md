# Test Design Standard

Snapshot date: 2026-05-05.

This document defines the minimum test design standard for pdocker-android.
The standard is executable. `scripts/pdocker-test-driver.py` is the canonical
test entrypoint, and `scripts/verify-test-design-criteria.py` checks the
ledgers, documentation, driver wiring, and check density on strict governance
runs.

## Quality Bar

Every implementation code token must be backed by structural and semantic test
design evidence. The selected check item count must be at least **two times**
the implementation-code token count.

The implementation-code token scope is intentionally narrow and excludes tests,
documentation, generated binaries, assets, and Android resources:

- `app/src/main/kotlin`
- `app/src/main/cpp`
- `app/src/main/python`
- `docker-proot-setup/bin/pdockerd`
- `docker-proot-setup/src`

The verifier counts semantic checks:

- feature scenarios;
- blackbox positive and negative requirements;
- abnormal event cases and their replay/evidence records;
- refactor-resilience external contract cases;
- input validation cases;
- stress, monkey, random, and variance scenarios;
- direct syscall hooks, coverage entries, boundary entries, and branch entries;
- static assertion checks in the UI, terminal/editor, memory pager, and project
  library verifiers.

It also counts structural coverage design obligations:

- C0 statement items: executable statement-like source lines.
- C1 branch outcome items: two outcomes for each branch decision.
- C2 condition outcome items: two outcomes for each condition atom.

This is still a design gate, not a proof that every item has been executed on
every device. Falling below the literal token ratio means the test design is too
thin to accept.

The C0/C1/C2 evidence is emitted as a machine-readable artifact with file-level
counts:

```sh
python3 scripts/verify-test-design-criteria.py \
  --write-artifact docs/test/test-design-criteria-latest.json
```

That artifact records the structural counting method, per-file C0 statement
items, C1 branch decisions and outcomes, and C2 condition atoms and outcomes.
It is structural design evidence, not a replacement for instrumented runtime
coverage.

All automated lanes and their artifact outputs are declared in one file:

```sh
tests/test_driver_manifest.json
```

Run manifests are also single-sourced. Each driver invocation writes
`docs/test/test-run-latest.json` and an immutable copy under
`docs/test/runs/<run-id>/manifest.json`, including command lines, log paths,
artifact paths, sha256 hashes, git commit, build flavor, and device metadata.

## Planned-gap and missing device artifact honesty

Ledger or verifier success may mean "the planned gap is still explicitly
tracked"; it does **not** mean the corresponding device behavior passed.
For every Android/device-gated row, absence of the named real-device artifact is
classified as missing evidence and remains non-promoting. A placeholder,
schema-only, host-only, skipped, blocked, or `status=planned-gap` artifact must
keep `success=false` and must not be summarized as pass.

Release/checkpoint records must therefore distinguish these states:

- **passed device evidence**: the required artifact exists, was produced by the
  named device lane on the required installed APK/device, reports pass/success,
  and satisfies the ledger promotion condition;
- **planned gap**: the lane is designed but the real device proof is not yet
  implemented or not yet collected;
- **missing artifact**: the ledger names a required device artifact, but the
  artifact is absent from the release/checkpoint evidence bundle;
- **blocked/skipped/failed**: the artifact or lane explicitly says it did not
  complete successfully.

Only the first state may be counted as a stable checkpoint. The other states are
release-blocker evidence or scoped limitations, even when a host verifier exits
zero after checking that the non-promoting state is honestly documented.

Instrumented Python coverage is generated separately:

```sh
bash scripts/run-python-coverage.sh
```

That command writes:

```text
docs/test/python-coverage-latest.json
```

The design verifier reads this artifact and adds covered Python lines and
covered Python branch outcomes to the measured coverage evidence. Native C/C++
and Android/Kotlin runtime coverage remain open work; until those are
instrumented, the 2x token bar should continue to fail rather than being
satisfied by structural counting alone.

## Required Axes

Each release-bound feature set must cover these axes:

| Axis | Source |
|---|---|
| Feature ledger | `tests/feature_scenarios.json` |
| Blackbox positive/negative requirements | `tests/blackbox_requirements.json` |
| Abnormal event evidence | `tests/abnormal_event_cases.json` |
| Refactor resilience | `tests/refactor_resilience_cases.json` |
| API argument validation | `tests/input_validation_cases.json` |
| Input file grammar validation | `tests/input_validation_cases.json`, `tests/input_grammar_coverage.json` |
| Value range validation | `tests/input_validation_cases.json` |
| Mutation testing | `tests/feature_scenarios.json` planned gap |
| Property-based testing | `tests/feature_scenarios.json` planned gap |
| Differential testing | `tests/feature_scenarios.json` planned gap |
| Stateful model testing | `tests/feature_scenarios.json` planned gap |
| Concurrency/race testing | `tests/feature_scenarios.json` planned gap |
| Crash/recovery testing | `tests/feature_scenarios.json` planned gap |
| Fault injection | `tests/feature_scenarios.json` planned gap |
| Golden compatibility corpus | `tests/feature_scenarios.json` planned gap |
| Security/adversarial testing | `tests/feature_scenarios.json` planned gap |
| Performance regression gates | `tests/feature_scenarios.json` planned gap |
| Direct syscall path variants | `tests/direct_syscall_coverage.json` |
| Direct syscall boundary values | `tests/direct_syscall_coverage.json` |
| Direct syscall branch decisions | `tests/direct_syscall_coverage.json` |
| Seeded random checks | `tests/stress_regression_cases.json` |
| Monkey tests | `tests/stress_regression_cases.json` |
| Stress tests | `tests/stress_regression_cases.json` |
| Repeat/variance detection | `tests/stress_regression_cases.json` |
| Build set artifacts | `tests/stress_regression_cases.json` |
| Device lanes | `tests/feature_scenarios.json` |
| Release readiness | `tests/feature_scenarios.json` and `scripts/verify-release-readiness.py` |

## Per-Change Rule

For a new or changed feature, update the design before treating the work as
complete:

1. Add or update one feature scenario.
2. Add an observable acceptance check.
3. Add a negative, boundary, value-range, or failure-mode check.
4. Link the scenario to a doc or machine-readable artifact.
5. If the check cannot run yet, record it as a planned gap with the lane,
   command, evidence target, and reason.

For compatibility-surface logic, static checks are not enough. A change to a
Dockerfile/Compose parser, builder, image puller, archive route, Engine API
handler, terminal transport, filesystem mediation layer, or path rewrite layer
must add or update a small executable compatibility case that exercises the
same public construct through the implementation. Example: accepting a standard
Dockerfile `COPY` glob requires an executable pdockerd build/COPY case, not
only a template syntax check or a file-exists check. The case must include at
least one negative/boundary route such as context escape, malformed pattern,
missing source, invalid JSON, unsupported flag, or bad range as appropriate.
The case must be independent of an existing image/build cache: a cache hit,
static asset check, or previously materialized rootfs is not evidence that the
changed parser/builder/path logic still accepts the public input.

Path-related refactors require an impact matrix before merge. The matrix must
cover at least: absolute and relative paths, `.` and `..`, trailing slashes,
glob metacharacters, symlinks, missing sources, duplicate/multiple matches,
context escape attempts, variable-expanded values, and the bundled project
fixtures that already use the affected construct.

Regression fixes require red/green evidence. Before a fix is accepted, the
test record must show that the proposed scenario fails against the broken
behavior: either by running the previous code, replaying the captured failing
artifact, or using a committed fixture that reproduces the failure. A green run
on the fixed code alone is not enough, because it can prove only that the test
passes, not that it covers the regression.

## Blackbox Requirement Rule

Requirement tests must start from user-visible behavior, not implementation
names. Each blackbox requirement must contain:

- a user story;
- observable surfaces;
- positive given/when/then evidence;
- negative given/when/then behavior;
- a failure oracle checking that "must fail" really fails.

## Input Validation Rule

Input validation must explicitly cover:

- API arguments, including malformed JSON, non-object request bodies, missing
  required values, and bounded query values;
- input file grammar, including Dockerfile syntax and scenario ledger syntax;
- value ranges, including negative bytes, impossible totals, boundary lengths,
  memory thresholds, uid/gid sentinels, and wait/signal status.

`tests/input_grammar_coverage.json` is the BNF-like grammar ledger. Every input
surface must declare productions, positive cases, negative cases, value ranges
where applicable, and an evidence command. Full grammars that are not implemented
must be explicit planned gaps. The current ledger does **not** claim complete
Compose BNF coverage; `compose-file-full-grammar` remains a planned gap until
upstream-compatible Compose validation exists.

## Abnormal Event Rule

Abnormal conditions must not be left as one-off console text. Each expected,
reproduced, or observed abnormal condition must have a structured event record
with category, severity, surface, trigger, expected signal, failure oracle,
evidence source, reproduction command, and retention rule.

The verifier is:

```sh
python3 scripts/verify-abnormal-events.py
python3 scripts/verify-abnormal-events.py \
  --write-artifact docs/test/abnormal-events-latest.json
```

Device-only or heavy abnormal paths may remain outside the fast lane, but they
must be marked as `runnable-with-device` or `planned-gap` with a concrete reason.

## Refactor-Resilience Rule

Refactor-resilience tests protect observable contracts while allowing private
implementation layout to change. They must prefer Engine API shape, definition
file fixtures, state-machine behavior, archive round-trip results, abnormal
event replay, and artifact diffing over source-string checks.

Golden expectations must not fossilize bugs. Each case declares:

- `intended` for behavior that should remain stable;
- `documented-limitation` for scoped compatibility differences;
- `known-bug-blocker` for bugs or incomplete behavior that must not become a
  passing runnable invariant.

The verifier is:

```sh
python3 scripts/verify-refactor-resilience.py
python3 scripts/verify-refactor-resilience.py \
  --write-artifact docs/test/refactor-resilience-latest.json
```

## Random, Monkey, Stress, And Variance Rule

Randomized tests must be deterministic by default:

- record the seed;
- generate a stable case fingerprint;
- compare repeated status summaries;
- fail on server errors or repeat drift.

Monkey, stress, benchmark, and variance tests may be too heavy for every build,
but they must still be designed and managed:

- declare the lane;
- declare the command;
- declare the artifact path or artifact policy;
- avoid rotation events unless the test is specifically about rotation;
- record a build set artifact with git commit, build flavor, timestamp, command,
  seed when applicable, fingerprint, and summary.

## Build Set Artifact Rule

Fast checks run on every normal build. Long device runs are explicit, but their
results must be reproducible and tied to the build set.

## Advanced Method Rule

The following methods are required test design axes. If a method is not
implemented yet, it must appear as an explicit planned gap in
`tests/feature_scenarios.json` with a lane, command, evidence target, and
acceptance scope.

| Method | Required Scope |
|---|---|
| Mutation testing | Kill condition inversion, error-path deletion, boundary mutation, path rewrite mutation, and storage accounting mutation. |
| Property-based testing | Generate API JSON, Dockerfile syntax, Compose environment expansion, path rewrite invariants, and storage accounting invariants. |
| Differential testing | Compare Docker-compatible API shapes, Dockerfile parsing, Compose expansion, tar/archive behavior, and command output against upstream Docker fixtures. |
| Stateful model testing | Model image, container, layer, project, job, daemon operation, card, and `docker ps` transitions. |
| Concurrency/race testing | Exercise compose up with stop, retry, log follow, UI restart, daemon reconnect, Documents sync, and `docker ps` refresh. |
| Crash/recovery testing | Recover after daemon kill, UI kill, process residue, interrupted build, interrupted pull, interrupted Documents sync, and stale job replay. |
| Fault injection | Inject ENOSPC, EACCES, OOM-near allocation, socket timeout, partial write, corrupt metadata, broken layer, and unavailable GPU/media executor failures. |
| Golden compatibility corpus | Preserve fixed Dockerfiles, Compose files, Engine API requests/responses, tar archives, `docker ps` outputs, and external project templates. |
| Security/adversarial testing | Attempt path traversal, symlink escape, tar bomb, zip slip, malicious Dockerfile, huge JSON, long argv, unsafe archive paths, and SAF mediator escapes. |
| Performance regression gates | Track ptrace, COW, build, apt, GPU bridge, llama, UI log rendering, and storage scan p50/p95/p99, variance, and previous-artifact deltas. |

Useful artifact commands:

```sh
python3 scripts/verify-test-design-criteria.py \
  --write-artifact docs/test/test-design-criteria-latest.json

python3 scripts/verify-abnormal-events.py \
  --write-artifact docs/test/abnormal-events-latest.json

python3 scripts/verify-refactor-resilience.py \
  --write-artifact docs/test/refactor-resilience-latest.json

python3 scripts/verify-input-validation.py \
  --write-artifact docs/test/input-validation-latest.json

python3 scripts/verify-stress-regression.py \
  --write-artifact docs/test/stress-regression-latest.json
```

## Automation

The standard is enforced by:

```sh
python3 scripts/verify-test-design-criteria.py
python3 scripts/verify-input-grammar-coverage.py
```

The command is part of both:

```sh
scripts/verify-fast.sh
scripts/verify-scenarios.sh
```

If the ledgers drift, a required axis disappears, instrumented coverage
evidence is missing, the literal token ratio falls below two times, or the
runner stops invoking the design gate, the fast verification fails. The current
baseline intentionally fails this 2x literal token gate until instrumented
coverage for Python, native, and Android/Kotlin paths closes the evidence
shortfall.

## Current Baseline

The literal token rule is intentionally not satisfied yet. The current measured
baseline is:

| Metric | Value |
|---|---:|
| Implementation-code tokens | 267,199 |
| Semantic check items | 858 |
| Instrumented Python coverage items | 2,487 |
| Python covered lines | 1,913 / 6,625 |
| Python covered branches | 574 / 2,932 |
| C0 statement items | 17,215 |
| C1 branch outcome items | 12,822 |
| C2 condition outcome items | 14,420 |
| Selected check items | 47,802 |
| Required by literal 2x token rule | 534,398 |
| Current literal ratio | 0.179x |

This keeps the shortfall visible and prevents the project from claiming the
2x-token test design bar until native C/C++ and Android/Kotlin coverage joins
the Python coverage evidence.
