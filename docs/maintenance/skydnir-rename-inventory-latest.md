# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `20181`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4248 |
| `PDocker` | 9 |
| `pDocker` | 7 |
| `pdocker` | 13949 |
| `pdocker-android` | 176 |
| `pdockerd` | 1792 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 328 |
| `artifact_schema` | 119 |
| `cli_command` | 49 |
| `config_path` | 355 |
| `daemon_binary_or_service` | 718 |
| `documentation_reference` | 331 |
| `environment_variable` | 2985 |
| `historical_evidence` | 10836 |
| `internal_reference` | 2171 |
| `public_branding` | 167 |
| `socket_or_storage_path` | 657 |
| `test_fixture` | 1465 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 10955 |
| `phase-0-guard` | 1465 |
| `phase-1-or-historical-context` | 331 |
| `phase-1-public-branding` | 167 |
| `phase-1-ui-copy-or-phase-4-package` | 328 |
| `phase-2-cli-alias` | 49 |
| `phase-2-daemon-alias` | 718 |
| `phase-3-config-migration` | 355 |
| `phase-4-or-later-migration-required` | 657 |
| `phase-5-dual-read-required` | 2985 |
| `phase-5-internal-namespace` | 2171 |

## Next Action

Start with `phase-1-public-branding` and `documentation_reference` rows.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
