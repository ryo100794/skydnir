# GitHub Promotion Kit

Snapshot date: 2026-05-05.

## Purpose

This document keeps the public-facing message consistent across the GitHub
repository, releases, demos, and social posts. For the operating workflow that
keeps issues, releases, README highlights, and Wiki mirrors current, see
[`NEWSFLOW.md`](NEWSFLOW.md).

## Scope

Use this page for reusable public wording, release-note phrasing, demo
checklists, and issue/release intake labels.

## Canonical Sources

- Use [`NEWSFLOW.md`](NEWSFLOW.md) for when and where to publish.
- Use [`../plan/STATUS.md`](../plan/STATUS.md) for current facts.
- Use [`../test/COMPATIBILITY.md`](../test/COMPATIBILITY.md) for measured
  compatibility.
- Use [`../design/DOCKER_COMPAT_SCOPE.md`](../design/DOCKER_COMPAT_SCOPE.md)
  for Android platform limits and unsupported Docker features.

## Repository Tagline

Not a Container. Still Contains.

## Short Description

Skydnir is an experimental Android app that embeds a selected Docker
Engine API-compatible daemon, Compose/Dockerfile workspace UI, image/container
file browser, persistent build logs, and interactive terminal/editor tools
inside a normal APK.

## Current Showcase Facts

Use these facts for the README, GitHub repository description, pinned issues,
release notes, and demo captions until a newer verification record replaces
[`../release/builds/20260505.1/README.md`](../release/builds/20260505.1/README.md):

- Build `20260505.1` has PASS records for compat/modern debug APKs and
  unsigned compat/modern release APKs. The verification commit is `dd3ce31`.
- The historical 2026-05-05 Android full smoke passed device Dockerfile build,
  Compose up/down, `docker exec`, and a basic Engine API `exec -it` path. Do
  not present this as current terminal promotion evidence; the live gate still
  needs fresh UI/JSONL device artifacts.
- Android quick smoke passed install, `docker version`, direct runtime probe,
  and memory-pager probes.
- `verify-fast`, scenario, and test-design logs currently fail at the
  intentional literal test-density gate.
- Host backend quick/full logs currently fail because the repository host path
  expects a staged `pdocker-direct` helper; do not describe that lane as green.
- The literal test-density gate is still open; describe it as an explicit
  quality/process blocker, not as an APK smoke failure.
- The compatibility audit records 69 PASS / 0 FAIL static and packaging checks,
  including APK payload checks that omit upstream Docker CLI/Compose, PRoot,
  proot-loader, and talloc.
- GPU bridge evidence includes host-native and host/container Vulkan probe
  artifacts, but the llama.cpp showcase must still call out the current blocker:
  llama.cpp served while only the output layer was offloaded.

## One-Minute Pitch

Docker was built for Linux hosts with namespaces, cgroups, overlayfs, and
bridge networking. Android apps do not get those primitives. Skydnir
explores how far a Docker-compatible workflow can go inside the Android app
sandbox: Engine API metadata, image pull/extraction, Compose orchestration,
container files, build logs, `-it`-style terminals, VS Code Server templates,
and a direct syscall-mediated executor path for SDK28 compatibility.

The project is useful as a mobile development workbench, Android container
runtime experiment, and compatibility testbed. It is intentionally clear about
the unsupported parts so users can see what is real, what is emulated, and what
is a Skydnir-specific extension.

## Honest Current-State Message

Use this framing whenever writing a release note, issue update, README
highlight, or demo caption:

- Skydnir is a Docker-compatible Android APK experiment, not upstream
  Docker for Android.
- The app provides a native Compose/Dockerfile workspace UI, Skydnir daemon
  API compatibility for the supported subset, image/container browsing,
  persistent jobs/logs, editor tabs, and PTY-backed terminal sessions.
- The product APK does not bundle upstream Docker CLI or Docker Compose; those
  tools are compatibility-test aids only when staged separately.
- The default product APK does not use PRoot, proot-loader, or talloc.
- Project templates target practical developer workflows such as VS Code
  Server, Continue, Codex, Claude Code, and llama.cpp workspaces.
- The direct Android executor and syscall/path mediation are active in-tree
  work. Treat compatibility claims as device-tested snapshots, not universal
  Android guarantees.
- GPU work is a Skydnir bridge effort. Current public wording should say
  "GPU bridge experiments" or "Vulkan/OpenCL bridge work", not general Docker
  GPU parity.
- Android app-sandbox limits remain real: no normal unrooted app can promise
  Linux namespace, cgroup, overlayfs, bridge networking, privileged device, or
  BuildKit parity.

## README Hero Bullets

- Native APK, no root, no Termux-first shell.
- Docker Engine API-compatible daemon over a Unix socket.
- Compose, Dockerfile, images, containers, jobs, logs, editor, and terminals in
  one Android UI.
- VS Code Server, Continue, Codex, Claude Code, and llama.cpp GPU workspace
  templates.
- Product APK does not bundle upstream Docker CLI/Compose; tests can stage
  them separately for compatibility checks.
- Direct Android executor and syscall mediation are developed in-tree, with
  repeatable performance benchmarks.
- Known Android limits are documented instead of hidden: cgroups, namespaces,
  bridge networking, mount propagation, privileged devices, and full BuildKit
  parity are outside the default unrooted APK scope.

## GitHub Topics

Use these repository topics:

```text
android
docker
containers
compose
docker-engine-api
android-apk
vscode-server
llama-cpp
vulkan
syscall
ptrace
mobile-development
```

## Suggested Repository Description

Skydnir Android APK with selected Engine API support, Compose/Dockerfile UI,
container files, persistent logs, VS Code Server templates, and direct Android
executor experiments.

## Pinned Issue Ideas

1. **Roadmap: Compose up parity on Android**
   https://github.com/ryo100794/skydnir/issues/3

   Track direct executor maturity, TTY attach, signals, networking, volumes,
   archive APIs, and storage cleanup.

2. **Compatibility report: what works vs Docker**
   https://github.com/ryo100794/skydnir/issues/1

   Link to `docs/test/COMPATIBILITY.md`, `docs/plan/STATUS.md`, and the latest
   Android smoke logs.

3. **Call for testers: Android device matrix**
   https://github.com/ryo100794/skydnir/issues/2

   Ask users to report model, Android version, ABI, SDK route, image pull,
   build, compose up, VS Code port, and runtime benchmark output.

## Release Note Categories

Keep release notes scannable by grouping changes into these categories:

- **User workflow**: UI, Compose controls, Dockerfile editing, file browser,
  terminals, service URLs, storage controls.
- **Docker compatibility**: Engine endpoints, Docker CLI behavior, Compose
  behavior when test tools are staged separately, archive/copy behavior,
  inspect metadata, warnings.
- **Runtime/executor**: direct execution, TTY attach, signals, process cleanup,
  path mediation, Android SDK route.
- **Templates**: VS Code Server, Continue, Codex, Claude Code, llama.cpp, and
  project-library changes.
- **GPU bridge**: request parsing, ICD/shim/executor changes, benchmark
  artifacts, known blockers, CPU fallback status.
- **Device testing**: Android version, device model, ABI, APK flavor, smoke
  result, benchmark link.
- **Known limits**: Android platform restrictions and unsupported Docker
  features.

## Release Note Template

```markdown
## Skydnir vX.Y.Z

### Highlights

- ...

### Compatibility

- Engine API:
- Compose:
- Direct executor:
- TTY/logs:
- Storage:
- GPU bridge:
- Templates:

### Device testing

- Device:
- Android:
- APK flavor:
- Smoke:
- Runtime benchmark:

### Known limits

- ...
```

## Social Post Drafts

### Technical post

I am building Skydnir: an experimental Docker-compatible runtime and
workspace app packaged as a normal Android APK. It embeds a Skydnir daemon, speaks a
Docker Engine-like API, manages Compose/Dockerfile projects, streams build
logs into the UI, and experiments with direct syscall-mediated container
execution on Android.

### Demo post

Docker-like workflows on Android are strange in exactly the interesting way:
no namespaces, no cgroups, no overlayfs, no bridge network. Skydnir
turns that constraint into an APK with Compose controls, image/container file
browsing, persistent logs, editor tabs, terminals, and VS Code Server
templates.

### Evidence post

Latest fixed record: Skydnir build 20260505.1 passes APK build outputs
and the historical Android full smoke route for Dockerfile build, Compose
up/down, `docker exec`, and a basic Engine API `exec -it` path. The honest
caveat: current terminal/service-truth/teardown promotion gates still need
fresh named device evidence, fast/design gates still fail on the current
literal test-density threshold, and host backend regression needs its
`pdocker-direct` helper lane split or staged.

### Tester call

Looking for Android testers for Skydnir. The useful reports are:
device model, Android version, APK flavor, image pull result, Compose up log,
VS Code port check, `docker ps` view, and runtime benchmark output.

## Demo Checklist

- Record the upper/lower split UI.
- Show Compose up from the UI, not from adb.
- Show live log updates with elapsed time and progress.
- Show container card ports and service URL.
- Open VS Code Server at `127.0.0.1:18080` when available.
- Show the llama.cpp template only with the current honest mode: CPU fallback
  for normal demos, forced Vulkan/OpenCL only when presenting benchmark data.
- Open image/container file browser.
- Open an interactive container terminal tab.
- Show storage metrics and prune action.
- End with a known-limits slide or caption so the demo does not imply upstream
  Docker parity on Android.

## Tester Call Flow

1. Open or refresh the device-test issue with the current APK version, target
   smoke path, and links to `docs/plan/STATUS.md` and
   `docs/test/COMPATIBILITY.md`.
2. Ask testers for device model, Android version, ABI, install source, APK
   flavor, free storage, and whether battery restrictions were disabled.
3. Ask for one short workflow at a time: start the Skydnir daemon, pull an image, build
   the default workspace, run Compose up, open VS Code Server, check `docker
   ps`, and capture logs.
4. For llama/GPU reports, ask for the benchmark artifact and whether the run
   used CPU fallback, forced Vulkan, forced OpenCL, or auto mode.
5. Triage every report into one of four labels: works, blocked by Android
   platform limit, Skydnir bug, or needs reproduction.
6. Move durable findings into docs before updating README highlights, releases,
   or Wiki mirrors.

## GitHub Intake

- `.github/ISSUE_TEMPLATE/bug_report.yml` collects device, flavor, route,
  reproduction steps, expected/actual behavior, and redaction confirmation.
- `.github/ISSUE_TEMPLATE/device_test.yml` collects Android device matrix
  results.
- `.github/ISSUE_TEMPLATE/compatibility_gap.yml` separates upstream Docker
  behavior from Skydnir behavior.
- `.github/pull_request_template.md` keeps tests, route coverage, and secret
  checks visible before merge.
- `.github/RELEASE_TEMPLATE.md` keeps release notes aligned with compatibility,
  device testing, known limits, signing, and security audit results.

## Boundaries To State Publicly

- This is Docker-compatible work, not upstream Docker.
- Android kernel restrictions mean cgroup, namespace, overlayfs, and bridge
  parity are intentionally scoped.
- Some GPU behavior is Skydnir-specific extension design, not NVIDIA Docker.
- Upstream Docker CLI/Compose are test tools only, not APK payload.
- Signing keys, certificates, and local debug secrets must never be committed.

## Maintenance

- Keep this page as wording guidance, not a status ledger.
- Refresh public claims only after the matching status, compatibility, or
  design document is current.
- Keep release templates here and publishing workflow in [`NEWSFLOW.md`](NEWSFLOW.md).
