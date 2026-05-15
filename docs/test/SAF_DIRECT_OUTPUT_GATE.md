# SAF Direct-Output Gate

Snapshot date: 2026-05-15.

This gate verifies that a container writing to `/documents` can prove the data
landed in the selected Android Documents/SAF backend. App-private mirror fallback
is not accepted as a direct-output pass unless the fallback is explicitly
recorded as fallback evidence.

This SAF direct-output gate is evidence-first: it must prove the selected
Documents backend received the bytes directly, or it must record a non-passing
fallback reason.

## Driver

Run:

```sh
PDOCKER_SAF_DIRECT_OUTPUT_CONTAINER=<running-container-id-or-name> \
  bash scripts/android-documents-mediator-smoke.sh
```

The driver writes:

```text
docs/test/saf-direct-output-latest.json
```

The canonical lane is `android-documents` in `tests/test_driver_manifest.json`.

## Required proof

The artifact must include:

- a real container Engine exec against `/documents`;
- payload visibility under the selected Documents/SAF host path;
- sidecar metadata for Unix-like file attributes;
- rename/stat proof;
- unlink proof;
- path traversal rejection policy;
- read-only grant/fallback policy.
- fail-closed direct-write validation evidence for an unsafe target such as
  `../escape-phase2.txt`;
- conflict evidence from the sidecar (`providerEvidence`, `sha256`, and
  `conflictState`) so external Android edits cannot be silently overwritten.

`planned-skip` and mirror-only success are not passes. If no real container is
available, the gate fails with an explicit reason instead of reporting fake
success.

## Fallback policy

App-private fallback is allowed only as recorded failure evidence:

```text
payloadState = mirror-fallback-after-saf-error
```

That state is useful for diagnostics but does not satisfy the direct-output
gate. A direct-output pass requires the payload to be visible at the selected
Documents/SAF host path.

Fallback is never used to bypass path validation. Unsafe relative paths
(`..`, absolute paths, backslashes, empty segments, and dot segments) must fail
closed before either the SAF provider write or app-private mirror fallback is
attempted. The gate records this as the `direct_write_path_validation` case with
`PathValidationPolicy = fail-closed`.

## Conflict evidence

Each SAF sidecar record now carries provider evidence for the published payload:
logical path, provider document evidence, byte size, optional `sha256`, and a
`conflictState`. Before a mediated write overwrites an existing published
payload, the mediator compares the sidecar with the current provider evidence.
If the provider payload is missing or its hash/size changed outside pdocker, the
mediator records `conflictState = external-provider-change` and refuses to
publish until a repair/resolution flow handles the conflict.

## Layer boundary

The SAF mediator exposes a filesystem backend contract. Upper layers, including
COW/overlay and archive/runtime code, must not bypass the backend to inspect SAF
implementation details. Unix metadata is represented by the sidecar layer; file
payload truth remains in the selected Documents/SAF backend when available.

This gate intentionally does not change COW internals. It records the expected
boundary so later overlay tests can stack on top of the same backend contract.

## Remaining gap

The current gate scaffold depends on an existing running container and an
installed debug APK. Read-only grant behavior is detected from the app/daemon
documents status and documented in the artifact, but a fully automated Android
permission downgrade scenario still needs an instrumented UI grant harness. The
mediator now records and enforces conflict evidence before overwrite, but a
device harness that edits the selected provider outside pdocker and then proves
the conflict path end-to-end is still a follow-up gate.
