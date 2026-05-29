# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `19546`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4184 |
| `PDocker` | 10 |
| `pDocker` | 8 |
| `pdocker` | 13482 |
| `pdocker-android` | 83 |
| `pdockerd` | 1779 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 240 |
| `artifact_schema` | 340 |
| `cli_command` | 30 |
| `config_path` | 163 |
| `daemon_binary_or_service` | 570 |
| `documentation_reference` | 182 |
| `environment_variable` | 2808 |
| `historical_evidence` | 11005 |
| `internal_reference` | 1945 |
| `socket_or_storage_path` | 751 |
| `test_fixture` | 1512 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 11345 |
| `phase-0-guard` | 1512 |
| `phase-1-or-historical-context` | 182 |
| `phase-1-ui-copy-or-phase-4-package` | 240 |
| `phase-2-cli-alias` | 30 |
| `phase-2-daemon-alias` | 570 |
| `phase-3-config-migration` | 163 |
| `phase-4-or-later-migration-required` | 751 |
| `phase-5-dual-read-required` | 2808 |
| `phase-5-internal-namespace` | 1945 |

## Next Action

Start with rows where `change_allowed_now=true`; if none exist, move to
alias/migration work instead of rewriting intentional legacy references.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
