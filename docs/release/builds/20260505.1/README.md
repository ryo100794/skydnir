# Build 20260505.1 Verification Record

Date: 2026-05-05 UTC

## Fixed Build Metadata

- `versionCode`: 24
- `versionName`: 0.5.3
- `buildNumber`: 20260505.1
- `buildTimeUtc`: 2026-05-05T23:20:33Z
- `buildCommit`: c194f2b3cd82

`buildCommit` records the committed baseline used when this fixed build number
was prepared. The final configuration-management commit is recorded by Git.

## APK Artifacts

Hashes are recorded in `apk-sha256.txt`.

| APK | Result |
| --- | --- |
| `app/build/outputs/apk/compat/debug/app-compat-debug.apk` | PASS |
| `app/build/outputs/apk/modern/debug/app-modern-debug.apk` | PASS |
| `app/build/outputs/apk/compat/release/app-compat-release-unsigned.apk` | PASS |
| `app/build/outputs/apk/modern/release/app-modern-release-unsigned.apk` | PASS |

Release outputs are unsigned because release signing material is intentionally
kept outside Git.

## Test Results

| Evidence | Result | Notes |
| --- | --- | --- |
| `gradle-unit-tests.log` | PASS | `testCompatDebugUnitTest` and `testModernDebugUnitTest` had no JVM test sources but Gradle completed successfully. |
| `verify-heavy-android-quick.log` | PASS | Installed compat debug APK on `10.8.135.134:37669`; docker version, direct probe, and memory-pager probes passed. |
| `verify-heavy-android-full.log` | PASS | Historical 2026-05-05 device Dockerfile build, Compose up/down, `docker exec`, and a basic Engine API `exec -it` path passed. The bracket argv regression did not reproduce. This does not promote current terminal/service-truth/teardown gates. |
| `verify-fast.log` | FAIL | Fails only at the enforced literal test-density gate: 43154 / 257036 = 0.168x, below the required 2.0x. |
| `verify-scenarios.log` | FAIL | Stops at the same literal test-density gate before later scenario steps. |
| `verify-test-design-criteria.log` | FAIL | Same intentional quality gate failure; negative self-tests passed before the final ratio failure. |
| `verify-heavy-backend-quick.log` | FAIL | Host backend regression still expects direct process execution from the repository backend path, but no host `pdocker-direct` helper is staged there. |
| `verify-heavy-backend-full.log` | FAIL | Same host backend direct-executor expectation mismatch as backend quick. |

## Build Warnings

- Android packaging emitted the existing `extractNativeLibs` manifest warning.
- Gradle emitted deprecation warnings for future Gradle 9 compatibility.
- Native C builds emitted existing `libcow.c` path-size and nonnull warnings.
- The backend host regression failure is not an APK failure; the device full
  route confirms the APK-bundled direct executor can build and run the smoke
  container.

## Follow-Up Gates

1. Increase recorded literal C0/C1/C2 plus semantic test items or adjust the
   unrealistic 2.0x token multiplier only by explicit project decision.
2. Split the backend host regression into metadata-only and process-exec lanes,
   or stage a real host-compatible `pdocker-direct` helper before expecting
   `verify_all.sh` to run containers.
3. Keep `verify-heavy-android-full.log` as historical device smoke evidence
   for build 20260505.1. Current release-blocking terminal, service-truth,
   teardown, image-pull, and release-honesty gates still require newer named
   promotion artifacts.
