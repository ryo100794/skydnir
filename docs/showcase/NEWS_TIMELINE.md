# pdocker-android News Timeline

Source snapshot: managed build commit `dd3ce31`, with build evidence in
[`../release/builds/20260505.1/`](../release/builds/20260505.1/).

This page is concise announcement copy for GitHub releases, pinned issues, and
project news posts. It should stay factual: green items need repository
evidence, and open items stay named until a follow-up artifact closes them.

## 2026-05-05: Fixed Android Build and Full Device Smoke

Build `20260505.1` is the current fixed build record for public update copy.
It records `versionName` `0.5.3`, `versionCode` `24`, and build time
`2026-05-05T23:20:33Z`. The managed source snapshot for this news entry is
`dd3ce31`; the build metadata file records `c194f2b3cd82` as the baseline used
when the fixed build number was prepared.

What is now green:

- Compat and modern debug APKs built successfully.
- Compat and modern unsigned release APKs built successfully.
- Android quick smoke passed on device: APK install, Docker version path,
  direct runtime probe, and memory-pager probes.
- Android full smoke passed on device: Dockerfile build, Compose up/down,
  `docker exec`, and Engine API `exec -it`. The bracket argv regression did
  not reproduce in this lane.
- JVM unit-test Gradle tasks completed successfully, with no current JVM test
  sources in those variants.

What remains blocked or not release-complete:

- `verify-fast`, scenario verification, and test-design criteria still fail at
  the literal test-density gate: `43154 / 257036 = 0.168x`, below the required
  `2.0x` threshold.
- Host backend heavy checks still fail because the repository backend path
  expects a host-compatible `pdocker-direct` helper that is not staged there.
- Release APKs are unsigned by design; signing material must stay outside Git.
- Existing Android packaging, Gradle deprecation, and native C warnings remain
  visible follow-ups, not hidden release notes.

## Current Announcement Summary

pdocker-android now has a fixed `20260505.1` build record with successful
compat/modern APK outputs and a full Android smoke pass covering build,
Compose, direct execution, logs, and interactive exec. The build is suitable
for honest tester-facing project news, but not for a no-caveat release claim:
the test-density gate, host backend direct-executor lane, release signing, and
some warning cleanup remain open.

## Next Milestones

- llama GPU: keep llama.cpp unmodified, run forced Vulkan only as a measured
  path, and require compare artifacts that report speedup, target status, GPU
  layer count, blocker, device, and thermal metadata. Meaningful evidence must
  show repeating transformer layers offloaded, not only the output layer.
- Direct executor: preserve PTY allocation and argv semantics for `-it` paths,
  keep `/bin/sh`, scripts, and `/usr/bin/[` argument behavior covered by a
  focused smoke, and prove stop/cleanup tears down process trees and GPU
  executors.
- Service truth: make `18080` and `18081` health depend on the current Engine
  container ID, real listener evidence, health state, and matching logs rather
  than Compose metadata or stale names.
- Tests: either raise recorded C0/C1/C2 plus semantic test items enough to pass
  the literal-density gate, or change that unrealistic `2.0x` threshold only
  through an explicit project decision.
- Release readiness: split host backend checks into metadata-only and
  process-exec lanes or stage a host-compatible direct helper before expecting
  backend heavy verification to run containers.

## Short GitHub Post Draft

`pdocker-android` has a new fixed build record: `20260505.1` on the managed
`dd3ce31` snapshot. Compat/modern APKs build, and the Android full smoke now
passes Dockerfile build, Compose up/down, `docker exec`, and Engine API
interactive exec on device. The remaining blockers are explicit: the
test-density gate is still below target, host backend heavy checks need a
host-compatible direct executor lane, release signing stays outside Git, and
llama GPU work still needs real transformer-layer offload evidence. Next work
focuses on llama GPU compare artifacts, direct-executor argv/PTY safety,
container-ID-based service health, and release-readiness tests.
