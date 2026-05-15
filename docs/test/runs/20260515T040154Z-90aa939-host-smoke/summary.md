# pdocker Test Run 20260515T040154Z-90aa939-host-smoke

- Status: `pass`
- Git: `90aa9392b8539af3cb6e6d81adc447ad5b789933`
- Branch: `main`
- Lanes: `host-smoke`
- Commands: `16`
- Artifacts: `13`

## Commands

| Lane | Command | Status | Seconds | Log |
|---|---|---:|---:|---|
| host-smoke | `python3 -m unittest discover -s tests -p 'test_*.py'` | pass | 39.46 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/001-host-smoke-unittest-all.log` |
| host-smoke | `python3 docker-proot-setup/scripts/verify_runtime_contract.py` | pass | 7.182 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/002-host-smoke-verify-runtime-contract.log` |
| host-smoke | `python3 scripts/verify_direct_syscall_contracts.py` | pass | 0.899 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/003-host-smoke-verify-direct-syscall.log` |
| host-smoke | `python3 scripts/verify-build-profile.py` | pass | 5.236 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/004-host-smoke-verify-build-profile.log` |
| host-smoke | `python3 scripts/verify-service-truth-plan.py` | pass | 1.069 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/005-host-smoke-verify-service-truth-plan.log` |
| host-smoke | `python3 scripts/verify-image-pull-crash-safety.py` | pass | 3.645 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/006-host-smoke-verify-image-pull-crash-safety.log` |
| host-smoke | `python3 scripts/verify-project-library.py` | pass | 1.023 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/007-host-smoke-verify-project-library.log` |
| host-smoke | `python3 scripts/verify-input-validation.py --write-artifact docs/test/input-validation-latest.json` | pass | 4.282 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/008-host-smoke-verify-input-validation.log` |
| host-smoke | `python3 scripts/verify-abnormal-events.py --write-artifact docs/test/abnormal-events-latest.json` | pass | 2.775 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/009-host-smoke-verify-abnormal-events.log` |
| host-smoke | `python3 scripts/verify-refactor-resilience.py --write-artifact docs/test/refactor-resilience-latest.json` | pass | 1.533 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/010-host-smoke-verify-refactor-resilience.log` |
| host-smoke | `python3 scripts/verify-input-grammar-coverage.py` | pass | 1.038 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/011-host-smoke-verify-input-grammar-coverage.log` |
| host-smoke | `python3 scripts/verify-stress-regression.py --write-artifact docs/test/stress-regression-latest.json` | pass | 7.893 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/012-host-smoke-verify-stress-regression.log` |
| host-smoke | `python3 scripts/verify-blackbox-requirements.py` | pass | 1.597 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/013-host-smoke-verify-blackbox-requirements.log` |
| host-smoke | `python3 scripts/verify-feature-scenarios.py` | pass | 1.388 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/014-host-smoke-verify-feature-scenarios.log` |
| host-smoke | `python3 scripts/verify-ui-actions.py` | pass | 0.782 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/015-host-smoke-verify-ui-actions.log` |
| host-smoke | `python3 scripts/verify_terminal_editor_contracts.py` | pass | 0.73 | `docs/test/runs/20260515T040154Z-90aa939-host-smoke/016-host-smoke-verify-terminal-editor.log` |

## Artifacts

- `tests/direct_syscall_coverage.json` (34273 bytes, sha256 `5db1aeca67ef2624fde3bd148279b37686a4a5f01c1ce05a0082771fe1d3ca0b`)
- `docs/test/input-validation-latest.json` (3506 bytes, sha256 `8946918649d6f89098590479e857f8ea43554a3e6c1aa07ae930c990ec6d62a6`)
- `tests/input_validation_cases.json` (3414 bytes, sha256 `6f1235fc8151c7c2973a2808bd0369396558f036254ee3815f2614e658df1d2b`)
- `tests/input_grammar_coverage.json` (7025 bytes, sha256 `1803a498a5102f19a4dae8a29658eb4b3f1e5daa2a651311713b10dbab23420c`)
- `docs/test/abnormal-events-latest.json` (20702 bytes, sha256 `c774f8098c4e74dd314c0c3a49f5dfb6a6b9e88e10e6ad084cafd37f816eb1f2`)
- `tests/abnormal_event_cases.json` (13824 bytes, sha256 `f2b42246c013cf1ceaa0094f315192c8b28b19a8924e7eaa4a3cc8381572707b`)
- `docs/test/refactor-resilience-latest.json` (5959 bytes, sha256 `cd0529858991ab58a0e4429584ce50add4a7288a382f2d5a5efd1fc514287935`)
- `tests/refactor_resilience_cases.json` (6244 bytes, sha256 `be28349a5c554bc862b07abf5c9577f6cf7b4e894d4bd6887ed65b2ebfee2d52`)
- `tests/input_grammar_coverage.json` (7025 bytes, sha256 `1803a498a5102f19a4dae8a29658eb4b3f1e5daa2a651311713b10dbab23420c`)
- `docs/test/stress-regression-latest.json` (1033 bytes, sha256 `a179801c7982fb7b36531aa4a7e8f819a39e006fbff3008d18b64aa37bd901ec`)
- `tests/stress_regression_cases.json` (3997 bytes, sha256 `1691416b873a0bb2ea049da079ce9fb4790116b679bfa25440b6d2fbc5335b69`)
- `tests/blackbox_requirements.json` (11336 bytes, sha256 `d4b9af4dfd1fedc8aea81bc7b06de52bfec4b0d945db95e04055fa4612f4e649`)
- `tests/feature_scenarios.json` (34727 bytes, sha256 `742c81287d4cb6a4d69631ce4466172fecd0127487ecfce98012bf638f274e52`)
