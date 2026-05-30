# Q6K Safe SPIR-V Static Decompilation Snapshot

This directory records the current static reverse-analysis baseline for the embedded `kQ6kSafeSpv` module in `app/src/main/cpp/pdocker_gpu_executor.c`.

Generated command:

```bash
python3 scripts/analyze-spirv.py \
  docs/test/spirv-q6k-safe-current/q6k-safe.spv \
  --json-out docs/test/spirv-q6k-safe-current/q6k-safe.analysis.json \
  --probe-plan-out docs/test/spirv-q6k-safe-current/q6k-safe.probe.json \
  --probe-range 0:2 \
  --disassemble-dir docs/test/spirv-q6k-safe-current
```

Key facts:

- SPIR-V hash: `0x7ec0292e948c9b41`
- Size: `9040` bytes / `2260` words
- Instruction count: `570`
- Entry point: `main`
- Local size: `[1, 1, 1]`
- Descriptors:
  - set `0`, binding `0`: read-only quantized A buffer view
  - set `0`, binding `1`: read-only B vector buffer
  - set `0`, binding `2`: writable D output buffer
- Probe candidates: `30`
- Probe submission policy: valid full-module instrumentation only; arbitrary SPIR-V fragments are not dispatchable.

This is not a high-level GLSL reconstruction. It is the reproducible SPIR-V disassembly/CFG/probe-manifest baseline used to compare against dumped llama.cpp native Q6 kernels.

Tracking note:

- The binary `.spv` and `.spvasm` files are local ignored inputs because the repository keeps generated static evidence small and reviewable.
- The tracked artifacts are the JSON analysis and probe manifests.  They must verify with the current `scripts/verify-spirv-probe-manifest.py` schema.
- If the analyzer or manifest verifier schema changes, regenerate the tracked `.probe.json` files from the local SPIR-V inputs before accepting static Q6 conclusions.
