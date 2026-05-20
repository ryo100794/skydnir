# llama Vulkan API-to-executor reconciliation contract

Snapshot date: 2026-05-20.

This document defines the evidence contract for reconciling unmodified
llama.cpp `ggml-vulkan` API calls with the pdocker glibc Vulkan ICD and the
APK-owned Android Vulkan executor.  It is a documentation-only contract; it
must not be read as proof that llama GPU inference is correct or fast.

## Current conclusion

Full API-to-executor reconciliation is **not yet proven**.  The bridge has
stronger V4 evidence for descriptor sets, buffer/memory identity, offsets,
ranges, SPIR-V metadata, and executor writeback, but there is not yet one
passing artifact that proves every relevant llama.cpp Vulkan callsite survived
ICD capture, transport, executor reconstruction, native Vulkan execution,
readback, HTTP prompt correctness, and verifier classification.

Until that artifact exists, no performance claim and no final root-cause claim
is allowed.  Any throughput, speedup, or "the cause is X" statement is
non-promoting diagnostic text unless the verifier reports a reconciliation pass
with `benchmark_claim_allowed=true` for the same artifact.

The current implementation-level correlation hashes are 64-bit FNV-1a
diagnostic hashes.  They are useful for catching command reconstruction,
descriptor, push-constant, specialization, and dispatch drift, but they are not
cryptographic proof.  A final promoting proof mode must either add a
collision-resistant full-buffer hash, such as SHA-256, or include enough
canonical raw field material for offline re-hashing.  Until then, a matching
FNV-1a reconciliation record can advance debugging, but it must not be used by
itself to claim complete pass-through correctness.

## llama.cpp callsite map summary

llama.cpp remains unmodified.  The current exact callsite map is:

| Scope | Hash / marker | llama.cpp callsite summary | Status |
|---|---|---|---|
| Q4_K pre-Q6 pipeline | `0xf3cd7d18f0276b42` | `ggml-vulkan.cpp` creates pipeline `mul_mat_vec_q4_k_f32_f32` from `vulkan-shaders/mul_mat_vec_q4_k.comp`; push struct is `vk_mat_vec_push_constants`; five descriptor buffers are `A/B/D/Fuse0/Fuse1`; specialization constants are `{ BLOCK_SIZE=32, NUM_ROWS=2, NUM_COLS=1/2 }`.  The shader declares three typed views of binding 0 for the same Q4_K block: `block_q4_K`, `block_q4_K_packed16`, and `block_q4_K_packed32`. | Mapped callsite; not a Q5/Q6 dispatch mix-up and not a llama.cpp ABI change. |
| Q4_K diagnostic variants | `0x853c49b4900eed3c`, `0x22ab0152b230e983` | Same Q4_K callsite after pdocker diagnostic Float16-capability insertion and duplicate-descriptor materialization respectively. | Diagnostic variants only. |
| Q6_K/final projection | `0x274f68a67dfef210`, `0x1bf751845c5dce75`, `0xe38f6a6a906d765c`, `0xbefdfb97e9734eb3`, `0x09c4622d92c6acb9`, `0x498c69a047eb3b2f`, `0xe5cd19682257a368`, `0x7ec0292e948c9b41` | `mul_mat_vec_q6_k`-like large quantized matvec/final projection with multiple binding-0 views, storage8/storage16/int8 features, `BLOCK_SIZE=32`, `NUM_ROWS=2`, `NUM_COLS=1`; row-indexed writeback and workgroup shape evidence exist. | Full reconciliation not proven; current blocker remains `native-q6-device-execution-or-final-store`. |
| RoPE/Yarn | `0xac41e8033a67af4a` | llama Vulkan RoPE/Yarn front-blocker shader. | CPU oracle matched in prior evidence; keep as regression guard. |
| RMSNorm | `0xf2f988b94bd3e0dc` | RMSNorm with optional multiply. | CPU oracle matched in prior evidence; keep as regression guard. |
| Small f32/indexing controls | `0x7bf05c459ac87f2b`, `0x11d5243c43b23a7b`, `0x11c0523df6c795b8` | Small zero-layer/control shaders used to validate oracle and transport plumbing. | Controls only; not proof of transformer-layer offload. |

Fresh Q4_K callsite evidence must show executor marker
`gpu-executor-llama-q4k-callsite-20260520`.  Fresh feature-chain ICD evidence
must show `vulkan-icd-feature-chain-marker-20260518` unless the compare command
explicitly changes the expected marker.

## ICD send fields

For real llama Vulkan dispatches, the glibc ICD sends one `VULKAN_DISPATCH_V4`
line plus file descriptors over `SCM_RIGHTS`:

- FDs: shader SPIR-V fd first, then one fd per bound storage-buffer memory.
- Header tokens: `shader_size`, `binding_count`, `push_size`,
  `dispatch_x/y/z`, hex `push_constants`, hex entry name, specialization entry
  count, specialization data size, and hex specialization data.
- Per-specialization tokens: `constantID`, `offset`, `size`.
- Per-binding tokens, in order: `descriptor_set`, `binding`, executor memory
  `offset`, transfer `size`, original `api_offset`, original `api_range`,
  `api_buffer_size`, `api_descriptor_type`, `api_dynamic`,
  `api_memory_offset`, `api_memory_size`, `api_memory_id`, `api_buffer_id`.
- Diagnostic/options tokens when present: dirty probe/writeback/cache knobs,
  resident/mutable cache knobs, `profile`, `strict_passthrough`,
  `strict_device_local_staging`, duplicate-descriptor rewrite/materialization,
  specialization materialization, pipeline optimization disable,
  unused-descriptor transfer skipping, SPIR-V descriptor-access mode,
  overlap-aliasing disable, CPU oracle, Q6_K/Q4_K diagnostic safe/oracle knobs,
  Float16-capability insertion, storage8/storage16/subgroup disables, and
  `requested_feature_mask`.

The reconciliation rule is that the ICD must preserve both coordinate systems:
`api_memory_offset + api_offset` is the memory image position, while
`api_offset` remains the `VkDescriptorBufferInfo.offset` visible to the native
Vulkan descriptor.

## Executor receive fields

The executor parses the V4 command into:

- shader fd/size, entry name, push bytes, dispatch dimensions,
  specialization entries and specialization data;
- `VulkanDispatchBinding` records containing `descriptor_set`, `binding`,
  executor `offset`, `size`, `api_offset`, `api_range`, `api_buffer_size`,
  `api_descriptor_type`, `api_dynamic`, `api_memory_offset`,
  `api_memory_size`, `api_memory_id`, and `api_buffer_id`;
- option booleans/limits mirroring the ICD tokens plus executor-only env state
  such as `PDOCKER_GPU_Q4K_PIPELINE_RETRY_LADDER`.

Executor evidence must report these values back in JSON through fields such as
`executor_build_marker`, `source_spirv_hash`, `effective_spirv_hash`, `entry`,
`specializations`, `bindings`, `dispatch`, `pipeline_key`,
`strict_object_graph`, `requested_feature_mask`, `spirv_local_size`,
`spirv_local_size_resolved`, `q4k_callsite_detected`, Q4/Q6 diagnostic flags,
`binding_details[]`, descriptor write/alias reports, SPIR-V feature and binding
reflection reports, CPU-oracle report, hashes before upload / after upload /
after dispatch / after writeback, and Android enabled feature bits on failures.

## Lossy risk list

A reconciliation artifact must explicitly rule out these lossy paths:

1. Descriptor set flattening or set/binding aliasing.
2. Conflating `api_offset` with `api_memory_offset + api_offset`.
3. `VK_WHOLE_SIZE` or descriptor ranges clamped to the wrong buffer/allocation
   tail.
4. Copy-alias or overlap-alias resolution changing memory/buffer identity.
5. Missing `api_memory_id`/`api_buffer_id` or collapsed strict object graph.
6. Descriptor rewrite/materialization changing shader ABI without being recorded.
7. SPIR-V mutation not tied to original/effective hashes and policy flags.
8. Specialization constants or `LocalSizeId` interpreted as unrelated constants.
9. Missing requested/enabled Vulkan feature masks for pipeline failures.
10. Resident/mutable/write-only caches hiding stale bytes or writeback misses.
11. Missing barriers/fences between host writes, shader reads/writes, and host
    readback.
12. CPU oracle or safe-kernel diagnostic paths accidentally treated as product
    performance evidence.

## Reconciliation artifact schema

A promoting artifact should be JSON with `schema: "pdocker.llama.vulkan.reconciliation.v1"` and at least:

```json
{
  "schema": "pdocker.llama.vulkan.reconciliation.v1",
  "snapshot_utc": "YYYY-MM-DDTHH:MM:SSZ",
  "source_compare_artifact": "docs/test/llama-gpu-....json",
  "llama_cpp_unmodified": true,
  "expected_markers": { "executor": "...", "icd": "..." },
  "observed_markers": { "executor": ["..."], "icd": ["..."] },
  "callsite_map": [
    {
      "hash": "0x...",
      "llama_pipeline": "...",
      "shader": "...",
      "descriptor_buffers": ["..."],
      "push_struct": "...",
      "specializations": { "...": "..." },
      "reconciled": false,
      "lossy_risks": []
    }
  ],
  "icd_send": { "vulkan_dispatch_v4": true, "fields_present": [] },
  "executor_receive": { "fields_present": [], "binding_details_present": true },
  "field_reconciliation": {
    "descriptor_sets": "pass|fail",
    "descriptor_bindings": "pass|fail",
    "offsets_ranges": "pass|fail",
    "buffer_memory_identity": "pass|fail",
    "push_constants": "pass|fail",
    "specializations": "pass|fail",
    "spirv_hashes": "pass|fail",
    "feature_masks": "pass|fail",
    "writeback_hashes": "pass|fail"
  },
  "verifier": { "classification": "...", "benchmark_claim_allowed": false },
  "claim_gate": { "performance_allowed": false, "root_cause_allowed": false }
}
```

The artifact may embed or reference the existing
`pdocker.llama.gpu.compare.v1` fields, but it must not replace them with prose.
`summary: "pass"` is never sufficient by itself.  A pass must include
substantive one-to-one evidence such as explicit API/executor canonical hash
pairs, match statuses, dispatch IDs, and the raw/canonical fields needed to
audit those pairs.  Bare pass summaries are classified as ambiguous.  FNV-1a
diagnostic-only records are also classified as ambiguous for promoting
correctness; they must be upgraded with `hash_algorithm: "sha256"`,
`proof_strength: "full"`, or auditable canonical raw fields before a wrong
deterministic completion may be attributed to reconciled GPU correctness.

## Verifier classification order

The verifier must classify in this order, stopping at the first applicable
blocker:

1. Device memory errors: `insufficient_memory`, `runtime_memory_pressure`.
2. Readiness false: `readiness-blocked`.
3. Service liveness passed but `/completion` failed: timeout, disconnect, or
   generic completion failure.
4. `/completion` returned wrong deterministic output but reconciliation is
   absent: `api-executor-reconciliation-missing`.
5. `/completion` returned wrong deterministic output but reconciliation is
   duplicate, unmatched, or otherwise non-one-to-one:
   `api-executor-reconciliation-ambiguous`.
6. `/completion` returned wrong deterministic output but reconciliation hashes
   or match statuses disagree: `api-executor-reconciliation-mismatch`.
7. `/completion` returned wrong deterministic output and reconciliation passes,
   but the executor marker is not fresh: `executor-marker-not-observed` with
   `observed_service_failure=llama-completion-wrong-output`.
8. `/completion` returned wrong deterministic output and reconciliation passes,
   but the ICD marker is not fresh: `icd-marker-not-observed` with
   `observed_service_failure=llama-completion-wrong-output`.
9. `/completion` returned wrong deterministic output with fresh markers and
   reconciliation pass: `llama-completion-wrong-output`.  This is still a
   correctness failure, not a benchmarkable performance result.
10. Missing fresh executor marker outside the wrong-output branch:
    `executor-marker-not-observed`.
11. Missing fresh ICD marker outside the wrong-output branch:
    `icd-marker-not-observed`.
12. Missing feature evidence for pre-HTTP pipeline-feature failures:
   `vulkan-pipeline-feature-evidence-missing`.
13. Pre-HTTP GPU blockers, including `vulkan-pipeline-feature`,
   `vulkan-queue-submit-feature`, `vulkan-generic-spirv-dispatch`, buffer
   allocation/range accounting, device discovery, or runtime memory pressure.
14. Runtime/config propagation mismatch.
15. Oracle fail-closed.
16. Unsupported GPU work accepted.
17. Missing required API prompt sanity, unless Q6 oracle evidence was reached.
18. Missing speedup fields, unless Q6 oracle evidence was reached.
19. Q6 path: not reached, workgroup-shape blocker, writeback mismatch,
    writeback unverified, oracle match, oracle mismatch, and then more specific
    native/output-layout/final-store/reduction classifications.

Only `q6-workgroup-cleared-and-oracle-match` can be terminal for correctness,
and only when required prompt correctness also passes.

## Pass/fail criteria

Pass requires all of the following in one fresh artifact set:

- expected executor and ICD markers observed;
- llama.cpp, Dockerfile, model, and prompt unchanged;
- every mapped callsite has ICD send fields and executor receive fields present;
- every field reconciliation category is `pass`;
- no lossy risk remains unclassified;
- deterministic HTTP prompt correctness passes;
- Q6 writeback evidence is verified and the verifier classification is
  `q6-workgroup-cleared-and-oracle-match`;
- `correctness_claim_allowed=true`; and
- performance claims additionally require CPU comparison fields and
  `benchmark_claim_allowed=true`.

Fail is any missing marker, missing field, mismatch, unclassified lossy risk,
pre-HTTP blocker, config propagation mismatch, oracle fail-closed state,
unsupported work acceptance, wrong/timeout completion, Q6 non-match, or
`benchmark_claim_allowed=false` for performance text.
