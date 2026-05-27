# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `19971`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4281 |
| `PDocker` | 9 |
| `pDocker` | 7 |
| `pdocker` | 13771 |
| `pdocker-android` | 95 |
| `pdockerd` | 1808 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 308 |
| `artifact_schema` | 119 |
| `cli_command` | 50 |
| `config_path` | 311 |
| `daemon_binary_or_service` | 634 |
| `documentation_reference` | 107 |
| `environment_variable` | 2945 |
| `historical_evidence` | 11028 |
| `internal_reference` | 2289 |
| `socket_or_storage_path` | 671 |
| `test_fixture` | 1509 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 11147 |
| `phase-0-guard` | 1509 |
| `phase-1-or-historical-context` | 107 |
| `phase-1-ui-copy-or-phase-4-package` | 308 |
| `phase-2-cli-alias` | 50 |
| `phase-2-daemon-alias` | 634 |
| `phase-3-config-migration` | 311 |
| `phase-4-or-later-migration-required` | 671 |
| `phase-5-dual-read-required` | 2945 |
| `phase-5-internal-namespace` | 2289 |

## Next Action

Start with `phase-1-public-branding` and `documentation_reference` rows.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
