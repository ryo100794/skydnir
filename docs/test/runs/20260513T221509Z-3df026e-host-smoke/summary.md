# pdocker Test Run 20260513T221509Z-3df026e-host-smoke

- Status: `pass`
- Git: `3df026ee337698a1021055b5ad13b13b7fbb6d34`
- Branch: `main`
- Lanes: `host-smoke`
- Commands: `16`
- Artifacts: `13`

## Commands

| Lane | Command | Status | Seconds | Log |
|---|---|---:|---:|---|
| host-smoke | `python3 -m unittest discover -s tests -p 'test_*.py'` | pass | 34.528 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/001-host-smoke-unittest-all.log` |
| host-smoke | `python3 docker-proot-setup/scripts/verify_runtime_contract.py` | pass | 7.198 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/002-host-smoke-verify-runtime-contract.log` |
| host-smoke | `python3 scripts/verify_direct_syscall_contracts.py` | pass | 1.323 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/003-host-smoke-verify-direct-syscall.log` |
| host-smoke | `python3 scripts/verify-build-profile.py` | pass | 5.878 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/004-host-smoke-verify-build-profile.log` |
| host-smoke | `python3 scripts/verify-service-truth-plan.py` | pass | 1.339 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/005-host-smoke-verify-service-truth-plan.log` |
| host-smoke | `python3 scripts/verify-image-pull-crash-safety.py` | pass | 5.822 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/006-host-smoke-verify-image-pull-crash-safety.log` |
| host-smoke | `python3 scripts/verify-project-library.py` | pass | 1.189 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/007-host-smoke-verify-project-library.log` |
| host-smoke | `python3 scripts/verify-input-validation.py --write-artifact docs/test/input-validation-latest.json` | pass | 5.342 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/008-host-smoke-verify-input-validation.log` |
| host-smoke | `python3 scripts/verify-abnormal-events.py --write-artifact docs/test/abnormal-events-latest.json` | pass | 2.876 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/009-host-smoke-verify-abnormal-events.log` |
| host-smoke | `python3 scripts/verify-refactor-resilience.py --write-artifact docs/test/refactor-resilience-latest.json` | pass | 1.586 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/010-host-smoke-verify-refactor-resilience.log` |
| host-smoke | `python3 scripts/verify-input-grammar-coverage.py` | pass | 1.049 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/011-host-smoke-verify-input-grammar-coverage.log` |
| host-smoke | `python3 scripts/verify-stress-regression.py --write-artifact docs/test/stress-regression-latest.json` | pass | 7.677 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/012-host-smoke-verify-stress-regression.log` |
| host-smoke | `python3 scripts/verify-blackbox-requirements.py` | pass | 1.754 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/013-host-smoke-verify-blackbox-requirements.log` |
| host-smoke | `python3 scripts/verify-feature-scenarios.py` | pass | 0.939 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/014-host-smoke-verify-feature-scenarios.log` |
| host-smoke | `python3 scripts/verify-ui-actions.py` | pass | 1.125 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/015-host-smoke-verify-ui-actions.log` |
| host-smoke | `python3 scripts/verify_terminal_editor_contracts.py` | pass | 0.95 | `docs/test/runs/20260513T221509Z-3df026e-host-smoke/016-host-smoke-verify-terminal-editor.log` |

## Artifacts

- `tests/direct_syscall_coverage.json` (34273 bytes, sha256 `5db1aeca67ef2624fde3bd148279b37686a4a5f01c1ce05a0082771fe1d3ca0b`)
- `docs/test/input-validation-latest.json` (3506 bytes, sha256 `d4e5ac76bc1e92679bd780bd97ea9913dfbf7c8c0d70006fbbb888ec7ca12525`)
- `tests/input_validation_cases.json` (3414 bytes, sha256 `6f1235fc8151c7c2973a2808bd0369396558f036254ee3815f2614e658df1d2b`)
- `tests/input_grammar_coverage.json` (7025 bytes, sha256 `1803a498a5102f19a4dae8a29658eb4b3f1e5daa2a651311713b10dbab23420c`)
- `docs/test/abnormal-events-latest.json` (15425 bytes, sha256 `1eaaea027fe0cbd4367d1c989ecb2d60db9c7b58c5c0e3ad099ec13c3b2f3328`)
- `tests/abnormal_event_cases.json` (10398 bytes, sha256 `7e5b6917c7a737aa6af7812c08bc2f3e96922acf8db652793084974f1743eca0`)
- `docs/test/refactor-resilience-latest.json` (5959 bytes, sha256 `7c33c62978f8f82302f45e7bae23542530657232ec784511253d62fe6505e068`)
- `tests/refactor_resilience_cases.json` (6244 bytes, sha256 `be28349a5c554bc862b07abf5c9577f6cf7b4e894d4bd6887ed65b2ebfee2d52`)
- `tests/input_grammar_coverage.json` (7025 bytes, sha256 `1803a498a5102f19a4dae8a29658eb4b3f1e5daa2a651311713b10dbab23420c`)
- `docs/test/stress-regression-latest.json` (1033 bytes, sha256 `8c52087bcf2e9ee3f15f4924ac5e9711f7c9b2da1d36a909fbfad013fe2f8793`)
- `tests/stress_regression_cases.json` (3997 bytes, sha256 `1691416b873a0bb2ea049da079ce9fb4790116b679bfa25440b6d2fbc5335b69`)
- `tests/blackbox_requirements.json` (11336 bytes, sha256 `d4b9af4dfd1fedc8aea81bc7b06de52bfec4b0d945db95e04055fa4612f4e649`)
- `tests/feature_scenarios.json` (34727 bytes, sha256 `742c81287d4cb6a4d69631ce4466172fecd0127487ecfce98012bf638f274e52`)
