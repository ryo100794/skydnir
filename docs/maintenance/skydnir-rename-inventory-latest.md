# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `19957`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4306 |
| `PDocker` | 10 |
| `pDocker` | 8 |
| `pdocker` | 13748 |
| `pdocker-android` | 90 |
| `pdockerd` | 1795 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 237 |
| `artifact_schema` | 365 |
| `cli_command` | 45 |
| `config_path` | 166 |
| `daemon_binary_or_service` | 610 |
| `documentation_reference` | 45 |
| `environment_variable` | 2947 |
| `historical_evidence` | 11028 |
| `internal_reference` | 2172 |
| `socket_or_storage_path` | 790 |
| `test_fixture` | 1552 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 11393 |
| `phase-0-guard` | 1552 |
| `phase-1-or-historical-context` | 45 |
| `phase-1-ui-copy-or-phase-4-package` | 237 |
| `phase-2-cli-alias` | 45 |
| `phase-2-daemon-alias` | 610 |
| `phase-3-config-migration` | 166 |
| `phase-4-or-later-migration-required` | 790 |
| `phase-5-dual-read-required` | 2947 |
| `phase-5-internal-namespace` | 2172 |

## Next Action

Start with `phase-1-public-branding` and `documentation_reference` rows.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
