# Docker CP End-to-End Device Gate

Snapshot date: 2026-05-15.

`docker cp` end-to-end promotion is device-gated. Run:

```bash
scripts/android-device-smoke.sh --docker-cp-e2e <default-workspace|llama>
```

The planned artifact is `files/pdocker/diagnostics/docker-cp-e2e-latest.json`.
Until adb/run-as evidence is reduced by a verifier it must remain:

```text
Status: planned-gap
Success: false
```

A future pass must prove the same Engine container ID across Docker CLI and
archive HEAD/GET/PUT, Byte and `sha256` equality, Hardlink preservation,
Docker-compatible symlink no-follow behavior, mode/mtime and uid/gid policy,
`user.*` xattr feasibility, and `X-Docker-Container-Path-Stat` headers.

The `DeviceGate` requires `HostStaticVerifierCannotPromote`, `NoGpuRequired`,
`NoTerminalRequired`, and `NoNetworkRequired`. Negative cases that are not
sufficient include `negative-cli-exit-zero-only.json`,
`negative-container-name-only.json`, `negative-bytes-only.json`,
`negative-host-only.json`, `negative-network-pull-required.json`, and
`negative-terminal-required.json`.
