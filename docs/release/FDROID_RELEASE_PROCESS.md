# F-Droid and Reproducible-Build Readiness Plan

Snapshot date: 2026-05-05.

This document is a readiness plan for CI and F-Droid-style release builds. It
does not claim that pdocker-android is ready for F-Droid submission. Treat it as
the checklist for making future release recipes auditable, source-based, and
repeatable.

## Current F-Droid Concepts

F-Droid review is centered on source availability, FLOSS licensing, inclusion
policy compliance, build metadata, and buildability from published source. The
official repository builds apps from source and reviews dependencies, licenses,
tracking libraries, anti-features, and prebuilt binaries before inclusion.

F-Droid build metadata is normally maintained as YAML in fdroiddata or a
compatible metadata repository. Metadata describes the source repository, tag or
commit, version, build commands, allowed scans/ignores, output APK, and any
special reproducibility handling.

F-Droid reproducible-build work checks whether an APK can be rebuilt from source
to match the upstream binary. Native code, build paths, timestamps, ZIP ordering,
tool versions, and signing behavior are common sources of differences. A
reproducible-build claim should only be made after two independent clean builds
match, or after an F-Droid verification-style flow confirms the result.

Official references:

- F-Droid Inclusion Policy: https://f-droid.org/en/docs/Inclusion_Policy/
- F-Droid Build Metadata Reference:
  https://f-droid.org/en/docs/Build_Metadata_Reference/
- F-Droid Reproducible Builds:
  https://f-droid.org/docs/Reproducible_Builds/
- F-Droid Inclusion How-To: https://f-droid.org/en/docs/Inclusion_How-To/

## Release Principle

`scripts/build-all.sh` is a local convenience wrapper. It is useful for
developer builds because it refreshes current native payloads and packages an
APK, but it is not the complete release process and should not be copied into
F-Droid metadata as the only authority.

A release recipe must instead describe every input and step needed to build the
APK from a clean source checkout:

- Pin the exact Android SDK command-line tools, platform, build-tools, and NDK.
- Pin Gradle through the wrapper and verify the wrapper distribution checksum.
- Pin the JDK major version and distribution used by CI.
- Build native libraries from checked-in source or declared source archives.
- Avoid product APK contents that are not buildable from source.
- Keep signing material and passwords outside Git.
- Record all generated or prebuilt artifacts and how they are regenerated.

## Target Build Profile

Define one release build profile before producing candidate binaries:

- App id, flavor, version name, version code, and Gradle task.
- Required host OS/container image and CPU architecture.
- `JAVA_HOME`, `ANDROID_HOME`, `ANDROID_NDK_HOME`, and locale/timezone.
- Exact SDK packages installed by revision, not just API level.
- Exact NDK revision used for C/C++ outputs.
- Exact Gradle wrapper version and Android Gradle Plugin version.
- Network policy: disabled after dependency fetch, or restricted to declared
  artifact repositories.

The release profile should be runnable in CI from a clean checkout and should
not depend on a developer workstation, Termux state, device state, or previously
generated `app/src/main/jniLibs` outputs.

## Native and Runtime Payload Policy

All native payloads shipped in the product APK need an auditable source path:

- Android/Bionic helper libraries under `app/src/main/jniLibs` must be generated
  from `app/src/main/cpp` or another declared source tree during the release
  build.
- Linux/glibc helper payloads and GPU shim/ICD artifacts must be built from
  source during the release build or excluded from an F-Droid-oriented flavor
  until a source build is documented.
- Generated `jniLibs` files should be treated as build outputs. If they remain
  checked in for local workflow reasons, the release process must delete and
  regenerate them before packaging, then compare the regenerated files to the
  checked-in copies.
- Any checked-in binary under runtime payload directories must have a recorded
  source, license, version, checksum, and rebuild command.
- PRoot, fakechroot, container runtimes, Docker-compatible helpers, and similar
  external components must not be downloaded during the app build without an
  explicit source, license, checksum, and maintainer-approved metadata entry.

## Docker CLI and Compose Policy

The product APK must not bundle upstream Docker CLI or Docker Compose binaries
unless the project has a documented, source-built, license-audited path for
those exact artifacts. For an F-Droid-oriented build, prefer one of these
outcomes:

- Exclude upstream Docker CLI/Compose binaries from the APK.
- Replace bundled binaries with project-owned source-built compatibility code.
- Ship only source/configuration needed to let users provide external tools
  themselves, with clear non-bundling behavior.

Any release notes or metadata must be precise about Docker compatibility scope
and must not imply that proprietary or unverifiable Docker components are
included.

## Runtime Container Downloads

There is an important distinction between APK build inputs and user-initiated
container operation. Downloading executable code, package archives, container
layers, or source during `docker pull`, `docker build`, `apt`, registry access,
or template-driven container setup can be a core product feature. That runtime
behavior is not the same as a hidden app self-update, and this plan should not
frame normal user-directed container/image/package workflows as inherently
forbidden.

The safeguards for runtime downloads are:

- The action is initiated by the user, or follows from a template/command the
  user explicitly selected.
- The registry, image, Dockerfile, package repository, URL, template, and/or
  project source is visible before or during the operation.
- The app does not silently extend the APK, replace app code, or download
  background executables outside a user-visible container/project workflow.
- Bundled product functionality still does not include upstream Docker CLI,
  Docker Compose, PRoot, fakechroot, or similar external binaries unless they
  have a source-built, license-audited, policy-approved release path.
- Downloaded container data is stored in clear app/project storage locations
  with controls for deletion, reset, export, or user review.
- Documentation distinguishes app-shipped code from user-provided or
  user-requested container contents.

## Dependency and Download Policy

The release build should be hermetic after declared dependency resolution:

- No undeclared downloads from release-build shell scripts, Gradle tasks, native
  build steps, app startup, or asset-generation steps.
- No fetching PRoot, fakechroot, Docker CLI, Compose, model files, fonts, web
  assets, binary archives, or seed container images during an F-Droid APK build
  unless the download is explicitly declared in build metadata and allowed by
  policy.
- Maven dependencies must come from declared repositories and should be pinned
  by Gradle lockfiles or equivalent dependency verification.
- npm, pip, cargo, go, or other ecosystem downloads should be absent from the
  Android release build unless the project adds a documented source-based,
  reproducible path for them.

## Signing and Secrets

Signing secrets must stay outside Git:

- Do not commit keystores, private keys, certificates, passwords, local signing
  property files, or generated signature material.
- CI should inject signing paths and passwords through secret storage.
- F-Droid-oriented metadata should build unsigned or let F-Droid sign, unless a
  reproducible-build verification flow intentionally compares to an upstream
  developer-signed APK.
- If upstream signature-copy verification is pursued, document the exact
  `apksigner` version and signature extraction process separately from this
  readiness plan.

## Reproducibility Checks

Before claiming reproducibility, run at least:

1. Clean checkout build in a pinned container or CI image.
2. Second clean checkout build in a fresh workspace at the same path, or with
   known path-normalization settings.
3. Compare APKs with `sha256sum`, `apksigner verify`, `zipinfo`, and
   `diffoscope` when available.
4. Compare native `.so` files and executable payloads before packaging.
5. Confirm no timestamps, absolute paths, random IDs, hostnames, usernames, or
   dirty Git metadata are embedded.
6. Confirm dependency caches were populated only from declared sources.

Native code is especially sensitive to NDK revision, host platform, debug
sections, strip behavior, and embedded paths. If differences remain, record
them as blockers instead of labeling the build reproducible.

## License and Source Audit

Maintain a release audit before any F-Droid submission attempt:

- App source license and copyright notices.
- Third-party source dependencies, versions, licenses, and repository URLs.
- Asset licenses for icons, fonts, screenshots, templates, demos, and bundled
  project examples.
- Binary payload inventory with source/build proof or exclusion plan.
- Anti-feature review for tracking, advertising, non-free network services,
  non-free assets, privileged execution, and runtime download transparency.
- Confirmation that all source needed for the APK is publicly available from
  the tagged release or declared source archives.

## Generated and Prebuilt Artifact Handling

Use this policy for generated artifacts:

- Prefer not to commit generated `jniLibs` or runtime binaries that are shipped
  in release APKs.
- If checked-in generated outputs remain necessary for active development, keep
  them outside the F-Droid release source path or regenerate and verify them in
  CI.
- Record each generated file path, source inputs, build command, toolchain
  version, and expected checksum in release notes or metadata support files.
- Fail release builds when checked-in binaries are newer than source, missing a
  source mapping, or differ from cleanly regenerated outputs.

## CI Shape

A suitable CI/F-Droid-style pipeline should have separate jobs:

- Dependency verification and license/source audit.
- Clean native build from source.
- Clean APK build with pinned SDK/NDK/JDK/Gradle.
- APK/native reproducibility comparison.
- Secret scan and signing-material absence check.
- Release artifact upload from CI only after all gates pass.

The CI job may call pieces currently used by `scripts/build-all.sh`, but the
release workflow should expose each step explicitly so reviewers can see what
was built, what was copied, and what was excluded.

## Public Release Candidate Gate

Issue #9 tracks the first public GitHub release candidate gate. Passing that
gate is separate from F-Droid readiness: it can make a GitHub RC transparent and
testable without claiming source-reproducible F-Droid inclusion. The canonical
GitHub release posture, blocker list, and release-candidate cut criteria live
in [`RELEASE_READINESS.md`](RELEASE_READINESS.md); do not duplicate that status
text here.

Before publishing a GitHub RC, record these criteria in the issue or release
notes:

- The target app id, flavor, version name, version code, commit, and APK output
  path.
- The local or CI build command used, including whether the APK is debug-signed
  or signed with external release material.
- The host-only readiness result from
  `python3 scripts/verify-release-readiness.py`.
- The normal fast validation result from `bash scripts/verify-fast.sh`, or an
  explicit note for any deferred device-only lane.
- A binary/generated-payload inventory review using
  `metadata/fdroid/generated-binary-inventory.md`, with every F-Droid-oriented
  blocker left visible instead of reworded as ready.
- Secret/signing confirmation that no keystores, private keys, passwords, local
  signing property files, or generated signature material are committed.
- Runtime-download wording that distinguishes app-shipped code from
  user-directed container/image/package downloads.
- Storage metrics release evidence from
  `python3 scripts/verify-storage-metrics.py`, with accounting language owned
  by [`docs/test/STORAGE_METRICS.md`](../test/STORAGE_METRICS.md).
- A link to the current blocker scope in
  [`RELEASE_READINESS.md`](RELEASE_READINESS.md), plus any required user data
  reset or upgrade notes.

The lightweight CI workflow in `.github/workflows/release-readiness.yml` runs
only host checks. It does not build an APK, does not require an Android device,
does not download runtime payloads, and does not certify F-Droid inclusion
readiness.

## Metadata Placeholder

This repository may keep draft support files under `metadata/fdroid/`, but real
F-Droid metadata should not be treated as authoritative until it names exact
versions, source refs, build commands, scan rules, anti-features, and artifact
handling. Placeholder files must not be interpreted as a submission request or
as evidence of inclusion readiness.
