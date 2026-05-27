# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `20228`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4270 |
| `PDocker` | 9 |
| `pDocker` | 7 |
| `pdocker` | 13962 |
| `pdocker-android` | 176 |
| `pdockerd` | 1804 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 328 |
| `artifact_schema` | 119 |
| `cli_command` | 49 |
| `config_path` | 360 |
| `daemon_binary_or_service` | 721 |
| `documentation_reference` | 331 |
| `environment_variable` | 2994 |
| `historical_evidence` | 10836 |
| `internal_reference` | 2173 |
| `public_branding` | 167 |
| `socket_or_storage_path` | 657 |
| `test_fixture` | 1493 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 10955 |
| `phase-0-guard` | 1493 |
| `phase-1-or-historical-context` | 331 |
| `phase-1-public-branding` | 167 |
| `phase-1-ui-copy-or-phase-4-package` | 328 |
| `phase-2-cli-alias` | 49 |
| `phase-2-daemon-alias` | 721 |
| `phase-3-config-migration` | 360 |
| `phase-4-or-later-migration-required` | 657 |
| `phase-5-dual-read-required` | 2994 |
| `phase-5-internal-namespace` | 2173 |

## Next Action

Start with `phase-1-public-branding` and `documentation_reference` rows.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
