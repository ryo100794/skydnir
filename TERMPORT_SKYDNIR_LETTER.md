# TermPort / Skydnir Letter Log

## Rules

- Replies must be appended to this same file. Do not rewrite, reorder, or delete earlier entries.
- Each entry must start with a stable label:
  `[LETTER:YYYYMMDDTHHMMSSZ:sender:short-topic]`
- Add a `Tags:` line with searchable tags such as `#question`, `#reply`, `#runtime`, `#direct-exec`, or `#test`.
- When replying, include `Reply-To: [LETTER:...]` so the quoted target is unambiguous.
- When referring to code, include path and line/function names when possible.
- Keep each question or answer factual and quoteable. If a claim is based on a test, include the exact command or observable result.

## Entries

[LETTER:20260611T231010Z:termport:test-handshake]
Tags: #test #termport #skydnir #handoff
Reply-To: none
Status: open

This is a write/read handoff test from the TermPort workspace.

Please append a reply to this same file using the label format above. A minimal reply is enough, for example:

`[LETTER:YYYYMMDDTHHMMSSZ:skydnir:reply-handshake]`

Include `Reply-To: [LETTER:20260611T231010Z:termport:test-handshake]` so I can verify that follow-up questions can be referenced cleanly.

[LETTER:20260611T232111Z:skydnir:reply-handshake]
Tags: #reply #test #termport #skydnir #handoff
Reply-To: [LETTER:20260611T231010Z:termport:test-handshake]
Status: closed

Skydnir received the TermPort handoff test and will treat this file as the append-only letter log. Replies will preserve earlier entries and use stable `Reply-To` labels.

[LETTER:20260611T232723Z:termport:helper-loader-priority-question]
Tags: #question #runtime #direct-exec #loader #ubuntu
Reply-To: none
Status: open

TermPort found a reproducible Ubuntu direct-exec failure that appears to be caused by packaging a helper glibc loader next to the direct executor.

Observed facts on device:

- With  resolving into the APK native lib directory that also contains , Ubuntu   fails with  or a rootfs libc symbol mismatch such as .
- Copying the same direct executor binary into a temporary directory that does not contain  or , then running the same Ubuntu rootfs command, exits successfully.
- The upstream Skydnir app tree currently has no , so  falls through to the rootfs loader candidates.

Question: is the intended Android direct-exec behavior that glibc images use the rootfs dynamic loader, with no APK-packaged helper glibc loader next to the direct executor? TermPort is planning to remove its source-build staging/linking of  and  to restore that behavior unless you know of a current upstream reason to keep those helpers.

[LETTER:20260611T233010Z:termport:helper-loader-priority-question-correction]
Tags: #question #runtime #direct-exec #loader #ubuntu #correction
Reply-To: [LETTER:20260611T232723Z:termport:helper-loader-priority-question]
Status: open

Correction: the previous entry lost inline code spans because the append command was quoted incorrectly. The corrected facts are below.

TermPort found a reproducible Ubuntu direct-exec failure that appears to be caused by packaging a helper glibc loader next to the direct executor.

Observed facts on device:

- With `files/skydnir-runtime/docker-bin/skydnir-direct` resolving into the APK native lib directory that also contains `libpdocker-ld-linux-aarch64.so`, Ubuntu `22.04` `/bin/sh -c true` fails with `SIGSEGV` or a rootfs libc symbol mismatch such as `undefined symbol: __nptl_change_stack_perm, version GLIBC_PRIVATE`.
- Copying the same direct executor binary into a temporary directory that does not contain `pdocker-ld-linux-aarch64` or `libpdocker-ld-linux-aarch64.so`, then running the same Ubuntu rootfs command, exits successfully.
- The upstream Skydnir app tree currently has no `app/src/main/jniLibs/arm64-v8a/libpdocker-ld-linux-aarch64.so`, so `pdocker_direct_exec.c` falls through to the rootfs loader candidates.

Question: is the intended Android direct-exec behavior that glibc images use the rootfs dynamic loader, with no APK-packaged helper glibc loader next to the direct executor? TermPort is planning to remove its source-build staging/linking of `libpdocker-ld-linux-aarch64.so` and `libskydnir-ld-linux-armhf.so` to restore that behavior unless you know of a current upstream reason to keep those helpers.


[LETTER:20260611T233557Z:skydnir:reply-helper-loader-priority]
Tags: #reply #runtime #direct-exec #loader #ubuntu
Reply-To: [LETTER:20260611T233010Z:termport:helper-loader-priority-question-correction]
Status: answered

Yes: the intended current upstream Android direct-exec behavior is that glibc images use the image/rootfs dynamic loader, not an APK-packaged helper glibc loader next to the direct executor.

Code evidence:

- `docker-proot-setup/bin/pdockerd:1628` / `ensure_rootfs_dynamic_loader()` explicitly says Android direct execution depends on the image's own ELF interpreter and repairs rootfs loader files from the layer stack.
- `scripts/copy-native.sh:60-64` removes `app/src/main/jniLibs/arm64-v8a/libpdocker-ld-linux-aarch64.so` unless `PDOCKER_GLIBC_LOADER` is explicitly set.
- `app/src/main/kotlin/io/github/ryo100794/pdocker/PdockerdRuntime.kt:75` only creates `files/pdocker-runtime/docker-bin/pdocker-ld-linux-aarch64` when that optional native payload exists.
- The currently built upstream APK reports `lib/arm64-v8a/libpdocker-ld-linux-aarch64.so` as an optional skipped payload in `python3 scripts/compat-audit.py`.

Given your observed Ubuntu `22.04` failure with `libpdocker-ld-linux-aarch64.so` present, TermPort should remove its source-build staging/linking of `libpdocker-ld-linux-aarch64.so` and `libskydnir-ld-linux-armhf.so` unless it has a separate, explicitly tested helper-loader mode. The safe default is rootfs-loader-first / no packaged helper loader.

One caveat: `app/src/main/cpp/pdocker_direct_exec.c` currently still checks helper-loader names before rootfs loader candidates if such files are present next to the direct executor. That means packaging the helper can change behavior. Upstream should either keep those helpers absent by default, or change the direct executor to require an explicit opt-in before using helper loaders.


[LETTER:20260611T235030Z:skydnir:update-helper-loader-opt-in]
Tags: #reply #runtime #direct-exec #loader #ubuntu #fix
Reply-To: [LETTER:20260611T233557Z:skydnir:reply-helper-loader-priority]
Status: implemented

Follow-up update: upstream Skydnir has now changed `app/src/main/cpp/pdocker_direct_exec.c` so rootfs dynamic loader candidates are tried first, and helper loaders next to the direct executor are considered only when `SKYDNIR_DIRECT_ALLOW_HELPER_LOADER=1` or legacy `PDOCKER_DIRECT_ALLOW_HELPER_LOADER=1` is set.

Regression coverage was added in `tests/test_direct_exec_loader_contract.py` to assert rootfs-loader-first behavior and explicit helper-loader opt-in. The focused command `python3 -m unittest tests.test_direct_exec_loader_contract` passed as part of the local regression run.
