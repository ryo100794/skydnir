# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `39894`
- Skipped binary files: `23`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 12400 |
| `PDocker` | 12 |
| `Pdocker` | 9222 |
| `pDocker` | 10 |
| `pdocker` | 16372 |
| `pdocker-android` | 105 |
| `pdockerd` | 1773 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 174 |
| `artifact_schema` | 7564 |
| `cli_command` | 29 |
| `config_path` | 138 |
| `daemon_binary_or_service` | 542 |
| `documentation_reference` | 188 |
| `environment_variable` | 8601 |
| `historical_evidence` | 11767 |
| `internal_reference` | 3778 |
| `socket_or_storage_path` | 838 |
| `test_fixture` | 6275 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 19331 |
| `phase-0-guard` | 6275 |
| `phase-1-or-historical-context` | 188 |
| `phase-1-ui-copy-or-phase-4-package` | 174 |
| `phase-2-cli-alias` | 29 |
| `phase-2-daemon-alias` | 542 |
| `phase-3-config-migration` | 138 |
| `phase-4-or-later-migration-required` | 838 |
| `phase-5-dual-read-required` | 8601 |
| `phase-5-internal-namespace` | 3778 |

## Next Action

Start with rows where `change_allowed_now=true`; if none exist, move to
alias/migration work instead of rewriting intentional legacy references.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
