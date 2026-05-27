# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `20215`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4280 |
| `PDocker` | 9 |
| `pDocker` | 7 |
| `pdocker` | 13958 |
| `pdocker-android` | 153 |
| `pdockerd` | 1808 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 326 |
| `artifact_schema` | 119 |
| `cli_command` | 52 |
| `config_path` | 370 |
| `daemon_binary_or_service` | 722 |
| `documentation_reference` | 331 |
| `environment_variable` | 2998 |
| `historical_evidence` | 10836 |
| `internal_reference` | 2158 |
| `public_branding` | 143 |
| `socket_or_storage_path` | 658 |
| `test_fixture` | 1502 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 10955 |
| `phase-0-guard` | 1502 |
| `phase-1-or-historical-context` | 331 |
| `phase-1-public-branding` | 143 |
| `phase-1-ui-copy-or-phase-4-package` | 326 |
| `phase-2-cli-alias` | 52 |
| `phase-2-daemon-alias` | 722 |
| `phase-3-config-migration` | 370 |
| `phase-4-or-later-migration-required` | 658 |
| `phase-5-dual-read-required` | 2998 |
| `phase-5-internal-namespace` | 2158 |

## Next Action

Start with `phase-1-public-branding` and `documentation_reference` rows.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
