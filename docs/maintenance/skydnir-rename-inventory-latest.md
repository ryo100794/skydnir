# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `20717`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4212 |
| `PDocker` | 12 |
| `Pdocker` | 1263 |
| `pDocker` | 10 |
| `pdocker` | 13400 |
| `pdocker-android` | 84 |
| `pdockerd` | 1736 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 173 |
| `artifact_schema` | 912 |
| `cli_command` | 29 |
| `config_path` | 137 |
| `daemon_binary_or_service` | 533 |
| `documentation_reference` | 177 |
| `environment_variable` | 2827 |
| `historical_evidence` | 11601 |
| `internal_reference` | 1935 |
| `socket_or_storage_path` | 758 |
| `test_fixture` | 1635 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 12513 |
| `phase-0-guard` | 1635 |
| `phase-1-or-historical-context` | 177 |
| `phase-1-ui-copy-or-phase-4-package` | 173 |
| `phase-2-cli-alias` | 29 |
| `phase-2-daemon-alias` | 533 |
| `phase-3-config-migration` | 137 |
| `phase-4-or-later-migration-required` | 758 |
| `phase-5-dual-read-required` | 2827 |
| `phase-5-internal-namespace` | 1935 |

## Next Action

Start with rows where `change_allowed_now=true`; if none exist, move to
alias/migration work instead of rewriting intentional legacy references.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
