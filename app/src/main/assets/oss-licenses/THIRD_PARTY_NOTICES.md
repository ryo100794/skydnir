# Third-party notices

pdocker-android bundles or depends on the following third-party components:

- go-containerregistry / crane: Apache-2.0, https://github.com/google/go-containerregistry
- xterm.js and xterm-addon-fit: MIT, https://github.com/xtermjs/xterm.js
- Chaquopy: open-source builds with license restrictions removed since 12.0.1, https://chaquo.com/chaquopy/license/
- CPython / Python 3.11 runtime packaged by Chaquopy: Python Software Foundation License, https://docs.python.org/3/license.html
- OpenSSL libraries packaged by Chaquopy (`libssl_chaquopy.so`, `libcrypto_chaquopy.so`): OpenSSL License / Apache-2.0 depending packaged upstream version, https://www.openssl.org/source/license.html
- SQLite library packaged by Chaquopy (`libsqlite3_chaquopy.so`): SQLite public-domain dedication / blessing, https://www.sqlite.org/copyright.html
- CA certificate bundle / certifi packaged by Chaquopy (`assets/chaquopy/cacert.pem`): certificate bundle notices, https://github.com/certifi/python-certifi
- Android Gradle Plugin, AndroidX, Material Components, Kotlin: Apache-2.0

The default APK does not bundle PRoot, proot-loader, talloc, upstream Docker
CLI, or upstream Docker Compose plugin binaries. Optional external proot
comparisons and upstream Docker CLI/Compose compatibility tools are
developer-supplied diagnostics only and are not part of the shipped app payload.

See `THIRD_PARTY_NOTICES.md` in the source repository for the maintained
license inventory and distribution notes.
