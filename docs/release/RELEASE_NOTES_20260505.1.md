# Release Notes: Build 20260505.1

Date: 2026-05-05 UTC

Managed source snapshot: `dd3ce31`

Evidence: [`builds/20260505.1/`](builds/20260505.1/)

## Summary

Build `20260505.1` is the current fixed Android build record for
pdocker-android. It advances the public status to `versionName` `0.5.3` /
`versionCode` `24` and records successful compat and modern APK outputs.

The strongest release-news signal is the Android full smoke pass: Dockerfile
build, Compose up/down, `docker exec`, and Engine API `exec -it` passed on the
test device. The previously watched bracket argv symptom did not reproduce in
that full Android lane.

## Passed

- Compat debug APK built.
- Modern debug APK built.
- Compat unsigned release APK built.
- Modern unsigned release APK built.
- Android quick smoke passed: install, Docker version, direct probe, and
  memory-pager probes.
- Android full smoke passed: Dockerfile build, Compose up/down, `docker exec`,
  and Engine API interactive exec.
- Gradle unit-test tasks completed successfully for current variants.

## Still Open

- Fast/scenario/test-design gates fail on the literal test-density ratio:
  `43154 / 257036 = 0.168x`, below the required `2.0x`.
- Host backend heavy checks fail because the host regression lane expects a
  direct process executor that is not staged for the repository backend path.
- Release artifacts are unsigned by design; signing keys and credentials must
  remain outside Git.
- Existing Android packaging, Gradle deprecation, and native C warnings remain
  cleanup items.

## Next Release Gates

- Add enough recorded C0/C1/C2 and semantic test material to pass the
  test-density gate, or explicitly revise the threshold as a project decision.
- Split host backend verification into metadata-only and process-exec lanes, or
  stage a host-compatible `pdocker-direct` helper before requiring container
  execution in that lane.
- Keep `verify-heavy-android-full.log` as the current device release-blocking
  smoke artifact until a newer full Android run supersedes it.
- Continue llama GPU work only from measured compare artifacts, with clear
  reporting for target status, GPU layer count, speedup, blocker, and device
  metadata.
