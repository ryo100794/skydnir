# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `19517`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4184 |
| `PDocker` | 10 |
| `pDocker` | 8 |
| `pdocker` | 13465 |
| `pdocker-android` | 83 |
| `pdockerd` | 1767 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 230 |
| `artifact_schema` | 340 |
| `cli_command` | 30 |
| `config_path` | 160 |
| `daemon_binary_or_service` | 558 |
| `documentation_reference` | 182 |
| `environment_variable` | 2808 |
| `historical_evidence` | 11005 |
| `internal_reference` | 1942 |
| `socket_or_storage_path` | 751 |
| `test_fixture` | 1511 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 11345 |
| `phase-0-guard` | 1511 |
| `phase-1-or-historical-context` | 182 |
| `phase-1-ui-copy-or-phase-4-package` | 230 |
| `phase-2-cli-alias` | 30 |
| `phase-2-daemon-alias` | 558 |
| `phase-3-config-migration` | 160 |
| `phase-4-or-later-migration-required` | 751 |
| `phase-5-dual-read-required` | 2808 |
| `phase-5-internal-namespace` | 1942 |

## Next Action

Start with rows where `change_allowed_now=true`; if none exist, move to
alias/migration work instead of rewriting intentional legacy references.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
