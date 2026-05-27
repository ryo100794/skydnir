# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `19929`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4274 |
| `PDocker` | 10 |
| `pDocker` | 8 |
| `pdocker` | 13752 |
| `pdocker-android` | 93 |
| `pdockerd` | 1792 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 237 |
| `artifact_schema` | 366 |
| `cli_command` | 50 |
| `config_path` | 166 |
| `daemon_binary_or_service` | 613 |
| `documentation_reference` | 45 |
| `environment_variable` | 2937 |
| `historical_evidence` | 11028 |
| `internal_reference` | 2180 |
| `socket_or_storage_path` | 789 |
| `test_fixture` | 1518 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 11394 |
| `phase-0-guard` | 1518 |
| `phase-1-or-historical-context` | 45 |
| `phase-1-ui-copy-or-phase-4-package` | 237 |
| `phase-2-cli-alias` | 50 |
| `phase-2-daemon-alias` | 613 |
| `phase-3-config-migration` | 166 |
| `phase-4-or-later-migration-required` | 789 |
| `phase-5-dual-read-required` | 2937 |
| `phase-5-internal-namespace` | 2180 |

## Next Action

Start with `phase-1-public-branding` and `documentation_reference` rows.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
