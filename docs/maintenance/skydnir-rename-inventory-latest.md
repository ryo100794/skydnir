# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `19882`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4288 |
| `PDocker` | 10 |
| `pDocker` | 8 |
| `pdocker` | 13694 |
| `pdocker-android` | 90 |
| `pdockerd` | 1792 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 217 |
| `artifact_schema` | 351 |
| `cli_command` | 45 |
| `config_path` | 173 |
| `daemon_binary_or_service` | 608 |
| `documentation_reference` | 45 |
| `environment_variable` | 2929 |
| `historical_evidence` | 11028 |
| `internal_reference` | 2150 |
| `socket_or_storage_path` | 785 |
| `test_fixture` | 1551 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 11379 |
| `phase-0-guard` | 1551 |
| `phase-1-or-historical-context` | 45 |
| `phase-1-ui-copy-or-phase-4-package` | 217 |
| `phase-2-cli-alias` | 45 |
| `phase-2-daemon-alias` | 608 |
| `phase-3-config-migration` | 173 |
| `phase-4-or-later-migration-required` | 785 |
| `phase-5-dual-read-required` | 2929 |
| `phase-5-internal-namespace` | 2150 |

## Next Action

Start with `phase-1-public-branding` and `documentation_reference` rows.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
