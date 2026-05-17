# Runtime single-container gate

This gate promotes only a real Android device artifact for:

```sh
docker run --rm ubuntu:22.04 echo hi
```

Verify it on the host with:

```sh
python3 scripts/verify-runtime-single-container-artifact.py docs/test/runtime-single-container-echo-hi-latest.json
```

A passing artifact must use schema `pdocker.android.runtime-single-container-echo-hi.v1`, status `pass`, `success: true`, command exactly `docker run --rm ubuntu:22.04 echo hi`, exit code `0`, stdout exactly `hi`, a lowercase 64-hex Engine container ID sourced from `docker --cidfile`, `host_shell_fallback: false`, and links to the device stdout, stderr, combined log, and cidfile diagnostics.

The verifier rejects planned-gap, blocked, skipped, and failed artifacts. It also rejects fake success such as host-shell output, stale or missing command fields, missing exact stdout proof, nonzero or string exit codes, short/synthetic container IDs, and artifacts with no evidence links.
