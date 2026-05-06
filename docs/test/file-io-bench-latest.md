# pdocker File I/O Benchmark

- Commit: `09fe314`
- Timestamp: `2026-05-06T04:19:26.787678+00:00`
- Size: 4 MiB sequential, 64 small files
- Trace mode: `seccomp`
- Device: `10.8.135.134:39827`
- Host rc: native `0`, container `0`

| operation | native real s | container real s | ratio | native MiB/s | container MiB/s | stops |
|---|---:|---:|---:|---:|---:|---:|
| noop | 0.011 | 0.065 | 6.09 |  |  | 137 |
| seq_write | 0.034 | 0.061 | 1.78 | 170.4 |  | 171 |
| seq_read | 0.021 | 0.065 | 3.01 | 374.2 |  | 171 |
| small_create | 0.926 | 0.063 | 0.07 |  |  | 203 |
| small_stat | 0.008 | 0.066 | 7.91 |  |  | 203 |
| small_read | 0.019 | 0.063 | 3.22 |  |  | 300 |
| compile_prepare | 1.963 | 0.220 | 0.11 |  |  | 2694 |
| compile_scan | 1.068 | 0.237 | 0.22 |  |  | 2174 |
| compile_objects | 2.975 | 0.399 | 0.13 |  |  | 4222 |
| compile_archive | 0.027 | 0.085 | 3.14 |  |  | 301 |
| overlay_prepare | 1.941 | 0.337 | 0.17 |  |  | 2008 |
| overlay_copyup_write | 0.811 | 0.100 | 0.12 |  |  | 204 |
| overlay_truncate | 0.011 | 0.093 | 8.75 |  |  | 204 |
| overlay_unlink | 0.833 | 0.268 | 0.32 |  |  | 1871 |

## Interpretation

- `noop` is the process/direct-executor startup floor; adjusted MiB/s subtracts that floor.
- Small-file rows emphasize path mediation, metadata syscalls, and shell loop overhead.
- Compile rows emulate build-system traffic without requiring a compiler: source-tree fanout, dependency scanning, object/dep file writes, and archive concatenation.
- Overlay rows target the pdocker layer/COW shape: hardlink-shared lower/upper files, first-write copy-up via `/.libcow.so` when present, truncate, and unlink-style cleanup.
- Sequential rows emphasize bulk read/write throughput through the mediated rootfs.
