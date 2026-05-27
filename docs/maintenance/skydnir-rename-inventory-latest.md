# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `20148`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4240 |
| `PDocker` | 8 |
| `pDocker` | 6 |
| `pdocker` | 13936 |
| `pdocker-android` | 174 |
| `pdockerd` | 1784 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 328 |
| `artifact_schema` | 118 |
| `cli_command` | 45 |
| `config_path` | 353 |
| `daemon_binary_or_service` | 712 |
| `documentation_reference` | 331 |
| `environment_variable` | 2979 |
| `historical_evidence` | 10836 |
| `internal_reference` | 2165 |
| `public_branding` | 167 |
| `socket_or_storage_path` | 654 |
| `test_fixture` | 1460 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 10954 |
| `phase-0-guard` | 1460 |
| `phase-1-or-historical-context` | 331 |
| `phase-1-public-branding` | 167 |
| `phase-1-ui-copy-or-phase-4-package` | 328 |
| `phase-2-cli-alias` | 45 |
| `phase-2-daemon-alias` | 712 |
| `phase-3-config-migration` | 353 |
| `phase-4-or-later-migration-required` | 654 |
| `phase-5-dual-read-required` | 2979 |
| `phase-5-internal-namespace` | 2165 |

## Next Action

Start with `phase-1-public-branding` and `documentation_reference` rows.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
