# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `19813`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4291 |
| `PDocker` | 10 |
| `pDocker` | 8 |
| `pdocker` | 13626 |
| `pdocker-android` | 87 |
| `pdockerd` | 1791 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 240 |
| `artifact_schema` | 341 |
| `cli_command` | 32 |
| `config_path` | 167 |
| `daemon_binary_or_service` | 576 |
| `documentation_reference` | 174 |
| `environment_variable` | 2905 |
| `historical_evidence` | 11025 |
| `internal_reference` | 2047 |
| `socket_or_storage_path` | 768 |
| `test_fixture` | 1538 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 11366 |
| `phase-0-guard` | 1538 |
| `phase-1-or-historical-context` | 174 |
| `phase-1-ui-copy-or-phase-4-package` | 240 |
| `phase-2-cli-alias` | 32 |
| `phase-2-daemon-alias` | 576 |
| `phase-3-config-migration` | 167 |
| `phase-4-or-later-migration-required` | 768 |
| `phase-5-dual-read-required` | 2905 |
| `phase-5-internal-namespace` | 2047 |

## Next Action

Start with rows where `change_allowed_now=true`; if none exist, move to
alias/migration work instead of rewriting intentional legacy references.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
