# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `19869`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4289 |
| `PDocker` | 10 |
| `pDocker` | 8 |
| `pdocker` | 13684 |
| `pdocker-android` | 87 |
| `pdockerd` | 1791 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 238 |
| `artifact_schema` | 344 |
| `cli_command` | 32 |
| `config_path` | 167 |
| `daemon_binary_or_service` | 576 |
| `documentation_reference` | 171 |
| `environment_variable` | 2903 |
| `historical_evidence` | 11028 |
| `internal_reference` | 2091 |
| `socket_or_storage_path` | 772 |
| `test_fixture` | 1547 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 11372 |
| `phase-0-guard` | 1547 |
| `phase-1-or-historical-context` | 171 |
| `phase-1-ui-copy-or-phase-4-package` | 238 |
| `phase-2-cli-alias` | 32 |
| `phase-2-daemon-alias` | 576 |
| `phase-3-config-migration` | 167 |
| `phase-4-or-later-migration-required` | 772 |
| `phase-5-dual-read-required` | 2903 |
| `phase-5-internal-namespace` | 2091 |

## Next Action

Start with rows where `change_allowed_now=true`; if none exist, move to
alias/migration work instead of rewriting intentional legacy references.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
