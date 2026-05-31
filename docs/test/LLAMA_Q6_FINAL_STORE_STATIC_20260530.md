# Q6 final-store static analysis (2026-05-30)

Input evidence:

- Runtime artifact: `docs/test/llama-gpu-ngl1-q6-workgroup-adb46015-20260530T232458Z.json`
- Reconstructed lineage: `docs/test/llama-gpu-q6-effective-lineage-adb46015-20260530.json`
- Source SPIR-V: `/tmp/q6write10-bundle/native-q6.write.spv`
- Effective SPIR-V hash: `0x2abe8e79566aa67a`

This is a static report.  It does not run ADB, llama.cpp, or a Vulkan driver.

## Effective-module reconstruction

The effective module is reproducible offline from the source module and the
recorded artifact policy:

1. `0xd2d7fbedceb5a8a6`: instrumented native Q6 source module.
2. `0x4c00be09530ea2db`: literal `LocalSize 1,1,1` legalized to `32,1,1`.
3. `0xab97bf7e13302b50`: specialization constants materialized with
   `{0:32, 1:2, 2:1}`.
4. `0x2abe8e79566aa67a`: duplicate descriptor target `%371` rewritten from
   binding `0` to binding `6`.

The descriptor rewrite is not optional for this artifact.  Binding `5` is the
probe/debug SSBO, so the first free alias binding is `6`.

## Descriptor variables relevant to final output

| SPIR-V id | Binding | Role in final output path |
|---:|---:|---|
| `%284` | 2 | final output SSBO written by the Q6 shader |
| `%239` | 3 | optional additive input, gated by `push[7] & 1` |
| `%266` | 4 | optional additive input, gated by `push[7] & 2` |
| `%3444` | 5 | debug/probe SSBO written after observation |
| `%371` | 6 | duplicate-binding alias of original binding 0 after normalization |

The runtime artifact records descriptor write `dst_binding=6` sourced from
binding `0`, offset `16384`, range `510504960`, and `alias_write=true`.  The
alias is therefore represented in the Android descriptor set rather than being
left unbound.

## Final output index formula

The effective final output store is:

```text
%1870 = (%2992 * push[6]) + ((WorkGroupID.y + push[8]) * push[6])
        + (2 * (WorkGroupID.x + NumWorkGroups.x * WorkGroupID.z))
        + %2993
%1875 = AccessChain binding2[%1870]
OpStore %1875 %1874
```

For the captured artifact, `push[6]=151936`, `push[8]=0`, and dispatch is
`[1187, 1, 64]`.  This matches the compare-script store-index model currently
reported as valid and full-coverage.

## Final output value path

The effective final value is:

```text
%1873 = AccessChain Workgroup %143[%2992][%2993][0]
%1874 = OpLoad %float %1873
OpStore binding2[%1870] %1874
```

Before that load, the shader:

1. accumulates FMA results into function variable `%656`,
2. copies `%656` through `%905` into workgroup variable `%143[..., lane]`,
3. runs a workgroup-memory reduction over `%143` with `OpControlBarrier`,
4. lets only `LocalInvocationID.x == 0` enter the final-store block.

The optional additive paths from bindings 3 and 4 are statically present, but
the artifact has `push[7]=0`, so both branches are dynamically skipped for this
run.

## Debug/probe feedback check

The debug/probe binding is `%3444` at binding `5`.

Static disassembly check:

- `%3444` is only used by `OpAccessChain` destinations followed by `OpStore`.
- There are no `OpLoad` instructions from `%3444`.
- The final observed record for candidate `130` stores `%1874` bits and `%1870`
  after the binding-2 `OpStore`.

Therefore, the debug/probe SSBO does not feed back into `%143`, `%1874`, or
binding 2 in the reconstructed effective module.

## Current conclusion

The current evidence no longer supports blaming stale executor packaging,
descriptor offset/range reconstruction, row-indexed writeback, or a missing
alias descriptor.  The remaining native Q6 mismatch is in the device execution
of the final value path before or at:

```text
Workgroup %143 reduction -> %1874 -> binding2[%1870]
```

The next implementation must therefore target a generic compatibility boundary
around workgroup-memory/barrier/final-store behavior, or prove another static
semantic mismatch in that path.  It must not replace llama.cpp, the model, the
prompt, or the Dockerfile.
