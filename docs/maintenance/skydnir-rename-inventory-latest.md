# Skydnir Rename Inventory

Snapshot date: 2026-05-27.

This generated ledger classifies tracked `pdocker`-family names before
any public Skydnir rename work proceeds.  It is intentionally an
inventory, not a replacement script.

- Entries: `19750`
- Skipped binary files: `22`

## Counts by Token

| Token | Count |
|---|---:|
| `PDOCKER` | 4294 |
| `PDocker` | 10 |
| `pDocker` | 8 |
| `pdocker` | 13564 |
| `pdocker-android` | 83 |
| `pdockerd` | 1791 |

## Counts by Category

| Category | Count |
|---|---:|
| `android_ui_or_package_surface` | 240 |
| `artifact_schema` | 340 |
| `cli_command` | 30 |
| `config_path` | 167 |
| `daemon_binary_or_service` | 576 |
| `documentation_reference` | 182 |
| `environment_variable` | 2909 |
| `historical_evidence` | 11019 |
| `internal_reference` | 1997 |
| `socket_or_storage_path` | 750 |
| `test_fixture` | 1540 |

## Counts by Phase

| Phase | Count |
|---|---:|
| `do-not-rewrite-history` | 11359 |
| `phase-0-guard` | 1540 |
| `phase-1-or-historical-context` | 182 |
| `phase-1-ui-copy-or-phase-4-package` | 240 |
| `phase-2-cli-alias` | 30 |
| `phase-2-daemon-alias` | 576 |
| `phase-3-config-migration` | 167 |
| `phase-4-or-later-migration-required` | 750 |
| `phase-5-dual-read-required` | 2909 |
| `phase-5-internal-namespace` | 1997 |

## Next Action

Start with rows where `change_allowed_now=true`; if none exist, move to
alias/migration work instead of rewriting intentional legacy references.
Do not rename `environment_variable`, `artifact_schema`,
`socket_or_storage_path`, or Android package/data surfaces until the
Skydnir compatibility aliases and migration tests exist.
