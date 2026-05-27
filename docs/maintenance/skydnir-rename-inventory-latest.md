# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `20218`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4269 |
| `PDocker` | 9 |
| `pDocker` | 7 |
| `pdocker` | 13961 |
| `pdocker-android` | 169 |
| `pdockerd` | 1803 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 328 |
| `artifact_schema` | 119 |
| `cli_command` | 50 |
| `config_path` | 360 |
| `daemon_binary_or_service` | 720 |
| `documentation_reference` | 331 |
| `environment_variable` | 2993 |
| `historical_evidence` | 10836 |
| `internal_reference` | 2164 |
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
| `phase-2-cli-alias` | 50 |
| `phase-2-daemon-alias` | 720 |
| `phase-3-config-migration` | 360 |
| `phase-4-or-later-migration-required` | 657 |
| `phase-5-dual-read-required` | 2993 |
| `phase-5-internal-namespace` | 2164 |

## Next Action

Start with `phase-1-public-branding` and `documentation_reference` rows.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
