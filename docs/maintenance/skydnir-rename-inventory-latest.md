# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `19440`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4199 |
| `PDocker` | 10 |
| `pDocker` | 8 |
| `pdocker` | 13385 |
| `pdocker-android` | 83 |
| `pdockerd` | 1755 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 176 |
| `artifact_schema` | 342 |
| `cli_command` | 29 |
| `config_path` | 137 |
| `daemon_binary_or_service` | 543 |
| `documentation_reference` | 184 |
| `environment_variable` | 2820 |
| `historical_evidence` | 11005 |
| `internal_reference` | 1938 |
| `socket_or_storage_path` | 751 |
| `test_fixture` | 1515 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 11347 |
| `phase-0-guard` | 1515 |
| `phase-1-or-historical-context` | 184 |
| `phase-1-ui-copy-or-phase-4-package` | 176 |
| `phase-2-cli-alias` | 29 |
| `phase-2-daemon-alias` | 543 |
| `phase-3-config-migration` | 137 |
| `phase-4-or-later-migration-required` | 751 |
| `phase-5-dual-read-required` | 2820 |
| `phase-5-internal-namespace` | 1938 |

## Next Action

Start with rows where `change_allowed_now=true`; if none exist, move to
alias/migration work instead of rewriting intentional legacy references.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
