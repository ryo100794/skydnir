# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `19851`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4289 |
| `PDocker` | 10 |
| `pDocker` | 8 |
| `pdocker` | 13666 |
| `pdocker-android` | 87 |
| `pdockerd` | 1791 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 239 |
| `artifact_schema` | 344 |
| `cli_command` | 32 |
| `config_path` | 167 |
| `daemon_binary_or_service` | 576 |
| `documentation_reference` | 174 |
| `environment_variable` | 2903 |
| `historical_evidence` | 11025 |
| `internal_reference` | 2081 |
| `socket_or_storage_path` | 770 |
| `test_fixture` | 1540 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 11369 |
| `phase-0-guard` | 1540 |
| `phase-1-or-historical-context` | 174 |
| `phase-1-ui-copy-or-phase-4-package` | 239 |
| `phase-2-cli-alias` | 32 |
| `phase-2-daemon-alias` | 576 |
| `phase-3-config-migration` | 167 |
| `phase-4-or-later-migration-required` | 770 |
| `phase-5-dual-read-required` | 2903 |
| `phase-5-internal-namespace` | 2081 |

## Next Action

Start with rows where `change_allowed_now=true`; if none exist, move to
alias/migration work instead of rewriting intentional legacy references.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
