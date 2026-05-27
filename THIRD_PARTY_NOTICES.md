# Third-party licenses and distribution notes

Snapshot date: 2026-05-18.

This inventory covers externally sourced code and binary payloads bundled by
Skydnir. The current default APK set is usable for distribution when
the notice asset is included and upstream license texts/notices are preserved.

| Component | Where used | License | Distribution condition | Status |
|---|---|---|---|---|
| Docker CLI | `vendor/lib/docker`, test/compatibility tool only; not packaged in the APK | Apache-2.0 | If redistributed separately, include license/notice and preserve Docker upstream notices. | OK: excluded from app payload. |
| Docker Compose plugin | `vendor/lib/docker-compose`, test/compatibility tool only; not packaged in the APK | Apache-2.0 | If redistributed separately, include license/notice and preserve Docker Compose upstream notices. | OK: excluded from app payload. |
| go-containerregistry / crane | `docker-proot-setup/docker-bin/crane`, packaged as `libcrane.so` | Apache-2.0 | Include license notice. | OK with notice asset. |
| xterm.js | `app/src/main/assets/xterm/xterm.js`, `xterm.css` | MIT | Include copyright and license notice. | OK with notice asset. |
| xterm-addon-fit | `app/src/main/assets/xterm/xterm-addon-fit.js` | MIT | Include copyright and license notice. | OK with notice asset. |
| Chaquopy | Gradle plugin/runtime for Python on Android, including APK-resolved `libchaquopy_java.so`, bootstrap native modules, and `.imy` Python payload archives | Open-source builds; upstream states restrictions were removed from 12.0.1 onward | Use current OSS version and Maven Central-compatible distribution; keep the APK notice asset. | OK: project uses Chaquopy 15.0.1 and audits resolved APK payloads. |
| CPython / Python 3.11 runtime | APK-resolved `libpython3.11.so`, Python standard-library payloads, and native extension modules packaged by Chaquopy | Python Software Foundation License | Include Python license/notice through the packaged notice set and release inventory. | OK with notice asset and APK-aware audit. |
| OpenSSL | APK-resolved `libssl_chaquopy.so` and `libcrypto_chaquopy.so` packaged by Chaquopy | OpenSSL License / Apache-2.0 depending packaged upstream version | Preserve upstream OpenSSL license and attribution notices for the packaged runtime libraries. | OK with notice asset and APK-aware audit. |
| SQLite | APK-resolved `libsqlite3_chaquopy.so` packaged by Chaquopy | SQLite public-domain dedication / blessing | Preserve bundled runtime attribution in the notice inventory. | OK with notice asset and APK-aware audit. |
| CA certificate bundle / certifi | APK-resolved `assets/chaquopy/cacert.pem` used by the Python runtime | MPL-2.0-derived certificate bundle notices, as packaged by the Python/Chaquopy runtime | Preserve certificate bundle notice/attribution when redistributing the APK. | OK with notice asset and APK-aware audit. |
| Android Gradle Plugin | Build plugin | Apache-2.0 | Build-time dependency; keep normal Gradle/Maven notices for redistributed build artifacts if needed. | OK. |
| AndroidX core/appcompat/webkit | App dependencies | Apache-2.0 | Include license notice when redistributed. | OK with notice asset. |
| Material Components for Android | App dependency | Apache-2.0 | Include license notice when redistributed. | OK with notice asset. |
| Kotlin Gradle plugin / stdlib | Kotlin build/runtime dependency | Apache-2.0 | Include license notice when redistributed. | OK with notice asset. |
| llama.cpp | Optional source fetched by the bundled `project-library/llama-cpp-gpu` Dockerfile during user-initiated container builds; no llama.cpp source or binary is bundled in the APK | MIT | If a user or distributor publishes a prebuilt llama.cpp image, include upstream MIT license notice for that image. | OK: template reference only, not APK-bundled code. |

## Source and license references

- Docker CLI upstream: https://github.com/docker/cli
- Docker Compose upstream: https://github.com/docker/compose
- go-containerregistry/crane upstream: https://github.com/google/go-containerregistry
- xterm.js upstream: https://github.com/xtermjs/xterm.js
- Chaquopy license page: https://chaquo.com/chaquopy/license/
- CPython license: https://docs.python.org/3/license.html
- OpenSSL license: https://www.openssl.org/source/license.html
- SQLite copyright status: https://www.sqlite.org/copyright.html
- certifi / Mozilla CA bundle: https://github.com/certifi/python-certifi
- Kotlin upstream: https://github.com/JetBrains/kotlin
- llama.cpp upstream: https://github.com/ggml-org/llama.cpp

## Compliance notes

- The APK must include `assets/oss-licenses/THIRD_PARTY_NOTICES.md`.
- The default APK staging path omits `libproot.so`, `libproot-loader.so`,
  `libtalloc.so`, `libdocker.so`, and `libdocker-compose.so`. Optional proot
  comparisons and upstream Docker CLI/Compose compatibility runs are
  command-supplied developer diagnostics only, not bundled app payloads.
- crane is permissively licensed and remains in the app payload for registry
  exchange. Docker CLI and Docker Compose notices are kept in this source
  repository notice for test-tool redistribution only.
- No external source in this inventory blocks distribution under the current
  packaging model. The top-level `LICENSE` file records the current license
  status for Skydnir's own original code.
