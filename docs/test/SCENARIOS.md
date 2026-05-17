# Feature Test Scenarios

Snapshot date: 2026-05-15.

`tests/feature_scenarios.json` is the machine-readable ledger for feature-level
test coverage. It records the feature area, execution lane, command, status,
documentation, and checks for each scenario. The verifier is:

```sh
python3 scripts/verify-feature-scenarios.py
python3 scripts/verify-abnormal-events.py
python3 scripts/verify-test-design-criteria.py
python3 scripts/verify-input-grammar-coverage.py
python3 scripts/verify-input-validation.py
python3 scripts/verify-stress-regression.py
python3 scripts/verify-blackbox-requirements.py
python3 scripts/verify-refactor-resilience.py
```

Run automated lanes through the canonical driver:

```sh
scripts/pdocker-test-driver.py --lane host-smoke
```

Run Python coverage evidence through the same driver:

```sh
scripts/pdocker-test-driver.py --lane python-coverage
```

Run the compose/Dockerfile based test-suite on an Android device with:

```sh
scripts/pdocker-test-driver.py --lane android-test-suite
```

Each run writes the single machine-readable artifact manifest at
`docs/test/test-run-latest.json`, plus an immutable copy under
`docs/test/runs/<run-id>/manifest.json`. The older `verify-*.sh` scripts are
compatibility wrappers or narrow helper scripts; new automation should be added
to `tests/test_driver_manifest.json` first.

## Stable checkpoint policy

The scenario ledger separates "the check is wired" from "the feature is stable."
`planned-gap`, `blocked`, `failed`, `skip`, `skipped`, and device-gated entries
without promoted artifacts are **non-complete** for release purposes. A host
verifier may pass because it confirmed that an item is still an explicit
planned gap; that pass must not be counted as a stable checkpoint.

The driver manifest records this with `stable_checkpoint_rule`,
`non_promoting_statuses`, and per-lane `stable_checkpoint_eligible=false`
metadata. The `release-honesty` lane runs host-only release hygiene checks; it
does not promote a build until the release blocker checklist and CI gate ledger
show passing device evidence or an explicit scoped-out/unsupported decision.

For scenario and checkpoint summaries, "artifact not present" is a separate
non-complete state. If a scenario is device-gated and its required artifact was
not produced by the named device lane on the required installed APK/device, the
scenario remains **missing device evidence** even if a host/static verifier
passed. Missing device evidence must be reported as planned-gap, blocked, or
missing-artifact evidence, never as a passing scenario.

## Lanes

| Lane | Purpose |
|---|---|
| `fast-local` | Host-only checks for normal builds and small patches. |
| `heavy-backend` | Longer backend compatibility and runtime regressions. |
| `heavy-container` | Rootfs/container probes that need a prepared rootfs. |
| `android-quick` | Short ADB smoke tests on an installed APK. |
| `android-full` | Longer ADB smoke tests on an installed APK. |
| `android-documents` | SAF Documents mediator tests. |
| `android-runtime-teardown` | Focused Android runtime teardown proof and reducer evidence. |
| `android-service-truth` | Focused Android planned-gap service truth evidence. |
| `android-gpu` | GPU executor and bridge measurements. |
| `android-llama` | llama.cpp container and benchmark measurements. |
| `release` | Publication, payload, secret-readiness, and checkpoint-honesty checks. Host-only release hygiene is non-promoting while P0 planned-gap/device gates remain open. |

## Required Areas

The ledger must represent these areas before release planning can evaluate
coverage status; planned-gap entries remain non-complete until executable
evidence exists:

| Area | Current Scenario |
|---|---|
| Abnormal event evidence | `abnormal.events.evidence-ledger` |
| Archive API | `archive.api.compatibility` |
| Backend runtime contract | `backend.runtime-contract` |
| Dockerfile build | `build.dockerfile.standard` |
| Engine API compatibility | `compat.engine-api.static-audit` |
| Compose orchestrator | `compose.orchestrator.ui-route` |
| COW/overlay storage | `cow.overlay.local` |
| Direct syscalls | `direct.syscall.contracts` |
| Direct container probe | `direct.container.probe` |
| Documents SAF mediator | `documents.saf.mediator.device` |
| GPU bridge | `gpu.bridge.smoke`, `gpu.host.native.bench`, `gpu.host-container.compare` |
| Image/layer storage | `image.layer.storage.prune`, `metadata.index.rebuild` |
| Input validation | `input.validation.api-file-range` |
| llama runtime | `llama.cpu.bench`, `llama.gpu.compare` |
| Media bridge | `media.bridge.contract` |
| Memory pager | `memory.pager.design-probe` |
| Runtime OOM survival | `runtime.oom-survival-large-workload` |
| SAF UnixFS backend | `saf.unixfs.layered-backend-contract` |
| Network metadata | `network.metadata.hostlike` |
| Project library | `project.library.templates` |
| Random/stress regression | `random.stress.regression-process` |
| Requirement blackbox tests | `requirements.blackbox.negative-oracles` |
| Release readiness | `release.readiness` |
| Refactor resilience | `refactor.resilience.external-contracts` |
| Storage metrics | `storage.metrics.accounting` |
| Terminal/editor UI | `terminal.editor.ui-contracts` |
| Test-suite container | `pdocker.test-suite.container-exec` |
| Test design governance | `test.design.criteria` |
| TTY/exec | `tty.exec.engine-it` |
| VS Code workspace | `vscode.workspace.default` |

## Direct Runtime Coverage

The direct-runtime syscall ledger has its own lower-level manifest:

```sh
tests/direct_syscall_coverage.json
```

It is checked by:

```sh
python3 scripts/verify_direct_syscall_contracts.py
python3 -m unittest discover -s tests/direct_syscall -p 'test_*.py'
python3 scripts/run_direct_syscall_scenarios.py --lane local
```

The manifest separates three coverage axes:

| Axis | Manifest Field | Purpose |
|---|---|---|
| Path variants | `path_variant_matrix` | Guest absolute paths, validated relative dirfd paths, unsafe relative escape denial, bind paths, pseudo-filesystems, dual-path operations, AF_UNIX paths, exec argv paths, and rootfs-fd lifecycle. |
| Boundary values | `boundary_value_matrix` | `PATH_MAX`, `sockaddr_un.sun_path`, short `getcwd` buffers, exec argv/scratch limits, memory guard thresholds, uid/gid `-1` sentinels, and wait/signal status. |
| Branch decisions | `branch_decision_matrix` | Required true/false/error branches for path rewriting, socket rewriting, exec rewriting, memory guard, credential emulation, cwd recovery, and tracee lifecycle. |

This is not a claim that every Linux syscall is Docker-compatible. It is a
regression gate that every active direct-runtime hook and every declared
branch/boundary obligation is either covered by a runnable scenario or recorded
as a planned heavy scenario.

## Containerized Test Suite

`pdocker-test-suite` is the canonical repeatable container route for bundled
runtime scenarios. The container starts idle through Compose, and every suite
run is invoked through Docker exec so the same route can be used from the UI,
ADB, or another development host:

```sh
docker exec pdocker-test-suite run-pdocker-test-suite --scenario all
```

The runner writes structured evidence to both `/reports` and the selected
Documents exchange folder:

```text
/documents/pdocker-exports/pdocker-test-suite/latest.json
/documents/pdocker-exports/pdocker-test-suite/latest.log
```

Scenario selectors are `all`, `smoke`, `direct`, `io`, `archive`, and
`documents`. The current suite covers Documents/shared mount checks, direct
runtime argv/proc/path behavior, the reusable direct-runtime probe payload,
file-I/O smoke, tar/archive round-trip, and invalid-input rejection. New
runtime-facing scenarios should be added here first unless they require a
specialized heavyweight image such as llama.cpp or Blender.

## Blackbox Requirements

`tests/blackbox_requirements.json` records requirement-level tests from the
user-visible side of the product. These entries describe:

- the user story;
- the observable surface, such as Engine API, Dockerfile, Compose, terminal,
  storage, Documents, GPU, release, or UI;
- a positive given/when/then scenario;
- a negative given/when/then scenario;
- a failure oracle checking that "must fail" cases actually fail.

The verifier intentionally rejects implementation tokens in requirement text so
requirements do not become tests of internal function names. It also runs
negative self-tests with corrupt fixtures and invalid Dockerfile syntax to check
that the fail-expected path is executable, not only documented.

## Input Validation

`tests/input_validation_cases.json` records a separate validation gate for three
classes of input:

- Engine API arguments, including malformed JSON, non-object JSON bodies,
  missing required query arguments, and bounded query values.
- Input file grammar, including standard Dockerfile instructions and the feature
  scenario ledger structure.
- Numeric and boundary values, including negative storage bytes, impossible
  free/total sizes, double-counted image views, and direct-runtime boundary
  obligations such as path length, AF_UNIX path length, exec argv size, memory
  thresholds, uid/gid sentinels, and wait/signal status.

`tests/input_grammar_coverage.json` records the BNF-like grammar surfaces that
back those checks. Each grammar entry declares productions, positive cases,
negative cases, value ranges, and the command that checks the declared coverage. Full
Compose grammar coverage is still a planned gap, so the project must not claim
complete BNF coverage yet.

Run it directly with:

```sh
python3 scripts/verify-input-grammar-coverage.py
python3 scripts/verify-input-validation.py
python3 scripts/verify-input-validation.py \
  --write-artifact docs/test/input-validation-latest.json
```

This gate is intentionally negative-test heavy. It checks that declared invalid
inputs are rejected rather than only documenting that they should be rejected.

## Abnormal Events

`tests/abnormal_event_cases.json` is the abnormal-condition ledger. It separates
expected/reproduced abnormal conditions from normal pass/fail logs and requires
every case to carry:

- category, severity, and affected surface;
- trigger and expected signal;
- failure oracle;
- evidence source and reproduction command;
- retention rule.

Run it directly with:

```sh
python3 scripts/verify-abnormal-events.py
python3 scripts/verify-abnormal-events.py \
  --write-artifact docs/test/abnormal-events-latest.json
```

The current ledger includes fast runnable abnormal cases for API input,
storage, runtime availability, readonly terminal behavior, GPU unsupported
dispatch, and the active test-design gate failure. Heavy build memory-guard,
full network negative corpus, and some device-only abnormal cases remain
explicit planned or device-lane work rather than being hidden.

## Refactor Resilience

`tests/refactor_resilience_cases.json` records the tests that should survive
internal rewrites. These checks protect external contracts rather than private
implementation names:

- Engine API golden response shape;
- bundled Compose and Dockerfile fixture portability;
- abnormal-event replay and evidence stability;
- planned archive round-trip, state-machine, and artifact-diff contracts.

Every case carries a `contract_class` so golden tests do not freeze accidental
bugs as permanent behavior. `known-bug-blocker` cases cannot be runnable passing
contracts.

Run it directly with:

```sh
python3 scripts/verify-refactor-resilience.py
python3 scripts/verify-refactor-resilience.py \
  --write-artifact docs/test/refactor-resilience-latest.json
```

## Random, Monkey, Stress, And Variance

`tests/stress_regression_cases.json` records the randomized and long-running
test process:

- Seeded random tests must record an explicit seed and deterministic case
  fingerprint.
- Monkey and stress tests live in device or heavy lanes, not in the normal
  host-only fast lane.
- Every monkey, stress, benchmark, or variance run must produce a
  machine-readable artifact tied to the build set: git commit, build flavor,
  timestamp, command, seed when applicable, case fingerprint, and summary.
- Runtime OOM survival stress must distinguish safe early `ENOMEM`, opt-in
  Large Workload Mode behavior, and Android LMK/down classification instead of
  treating all exits as the same failure.
- Repeated runs compare stable summaries. A drift in status vectors, health
  states, or benchmark envelopes must fail, warn, or create a recorded blocker
  instead of being lost in console output.

The fast reproducibility gate is:

```sh
python3 scripts/verify-stress-regression.py
```

It starts a temporary daemon, generates a seeded Engine API fuzz sequence, runs
that sequence repeatedly, and fails on server errors or repeat drift. To preserve
a build-set artifact for a specific run:

```sh
python3 scripts/verify-stress-regression.py \
  --write-artifact docs/test/stress-regression-latest.json
```

Heavy lanes remain explicit:

```sh
scripts/verify-scenarios.sh --include-device
scripts/verify-scenarios.sh --include-long-device
```

## Known Gaps

- `archive.api.compatibility` is intentionally marked `planned-gap` until tar
  stat headers, archive copy-in/out, and merged lower/upper file tests are
  implemented as executable checks.
- Long GPU and llama checks are not part of the default lane because they can
  occupy the device and alter benchmark state. They are still part of the single
  scenario runner through `--include-long-device`.
