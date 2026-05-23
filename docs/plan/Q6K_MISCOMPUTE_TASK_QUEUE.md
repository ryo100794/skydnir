# Q6_K Miscompute Investigation Task Queue

Snapshot date: 2026-05-23.

This queue exists so the Q6_K investigation never stalls after one diagnostic
run.  When a task closes, add the next smallest task that can further locate
the miscompute.  The goal is not to accumulate tests; the goal is to prevent
bad bridge states from being introduced and to identify the exact failing
layer.

Non-negotiable constraints:

- Do not modify llama.cpp.
- Do not modify Dockerfiles, models, or prompts.
- Do not change `VULKAN_DISPATCH_V4` positional ABI.
- Do not claim performance from a run that does not pass correctness.
- Do not replace static reasoning with random trial-and-error.
- Do not hide native Q6 failure behind the safe-kernel path.

## Current working model

The safe Q6 bridge-owned kernel has proven that the bridge can move the relevant
descriptor data through Android Vulkan and pass the `ngl=1` prompt gate under
explicit compatibility substitution.  That does **not** prove that the native
llama.cpp Q6 SPIR-V is correct on the bridge.

The native Q6 source hash remains:

- `0x1bf751845c5dce75`

The bridge-owned safe Q6 hash remains:

- `0x7ec0292e948c9b41`

The current blocker is therefore:

> Find where native Q6 diverges: descriptor/range transport, push/spec
> interpretation, local size/workgroup mapping, reduction/arithmetic,
> synchronization/device visibility, or final store/writeback.

Latest device evidence from `192.168.179.26:45055` narrows the native Q6
identity one step further:

- source/native SPIR-V hash: `0x1bf751845c5dce75`;
- effective SPIR-V hash during dispatch: `0xe38f6a6a906d765c`;
- the effective hash is reproduced by changing only
  `OpExecutionMode %main LocalSize 1 1 1` to `LocalSize 32 1 1`;
- the source module separately declares `BuiltIn WorkgroupSize` as an
  `OpSpecConstantComposite` whose first component is `SpecId 0`
  (`default=1`, runtime specialization value `32`);
- writable binding writeback is verified, and the wrong value is already
  visible at the GPU final-store/readback boundary.

So the next investigation must treat `[32,1,1]` legalization as a known,
reproducible bridge transform, not as an unknown shader replacement.  The open
question is now whether the remaining native Q6 mismatch is caused by
descriptor/alias semantics, local invocation/workgroup interpretation,
native arithmetic/reduction, or final store indexing.

Static follow-up on the preserved native/effective modules found:

- `docs/test/spirv-q6k-native-adb45055/native-vs-effective-local-size-patched.dataflow.json`
  reports only `local_size` mismatch; entry points, LocalSizeId,
  `BuiltIn WorkgroupSize`, descriptors, push constants, load origins, and
  store origins match.
- The disassembly diff is the single line
  `OpExecutionMode %4 LocalSize 1 1 1` versus
  `OpExecutionMode %4 LocalSize 32 1 1`.
- The native Q6 output binding is `%284` (`set=0,binding=2`).  There are two
  final output stores:
  - tail/partial path: block `%2865`, store word index `3789`,
    `native-q6-source.spv.spvasm` line 844;
  - full path: block `%1862`, store word index `6653`,
    `native-q6-source.spv.spvasm` line 1466.
- Both stores write a value loaded from workgroup memory `%143[y][x][0]`.
  Therefore the next probe should prioritize workgroup partial/reduction values
  before treating host fd writeback as the culprit.
- V4 descriptor/offset review found the current artifact API-faithful:
  binding 2/3/4 intentionally share the same API buffer slice, descriptor
  writes remain distinct, and strict reconciliation matches.  `copy_alias`
  remains a future hazard, so strict passthrough must reject or ignore alias
  copy substitution before V4 send.

## Always-on inventory command

Run this after each related artifact lands:

```bash
python3 scripts/maintenance/summarize-q6k-evidence.py \
  --out docs/test/q6k-evidence-inventory-latest.json
```

The generated inventory records the next task queue from observable artifact
gaps.  If it says `q6k-native-spv-dump` is open, do not pretend native static
comparison is complete.

## Open queue

### Current artifact-derived facts

The inventory currently finds native Q6 mismatch evidence in older artifacts
and safe-kernel match evidence in newer artifacts.  The strongest native
mismatch artifacts show:

- command transport, descriptor hashes, push hashes, specialization hashes, and
  writeback evidence are often sufficient to move suspicion away from the
  host/container fd writeback layer;
- the native output value is already wrong at the GPU-after-dispatch /
  final-store-visible boundary in the detailed mismatch records;
- historical artifacts disagree on the intended local-size interpretation
  (`[32,2,1]` appears in old diagnostics, while the current safe-kernel plan
  treats `[32,1,1]` as the validated compatibility target), so the next native
  run must record native SPIR-V `OpExecutionMode`/`OpExecutionModeId`,
  `BuiltIn WorkgroupSize`, specialization entries, and the resolved local size
  in the same Q6 event before assigning blame to reduction math;
- safe-kernel success proves the bridge data path can work, but it also means
  native Q6 must remain separately visible as unresolved.

Therefore, the next tasks are ordered to avoid jumping directly to performance
or shader replacement.

### Q6K-001: Native SPIR-V dump

Status: closed for source module, open for runtime dump plumbing.

Purpose: collect the exact native Q6 `.spv` for source hash
`0x1bf751845c5dce75`.

Acceptance:

- [x] A `.spv` whose FNV hash is `0x1bf751845c5dce75` is preserved as
  `docs/test/spirv-q6k-native-adb45055/native-q6-source.spv`.
- [x] The dump is analyzed with `scripts/analyze-spirv.py`.
- [x] The analysis records entry point, descriptors, push constants,
  specialization constants, `BuiltIn WorkgroupSize`, access-chain origins,
  load origins, and store origins.
- [ ] The executor-side `PDOCKER_GPU_SPIRV_DUMP_DIR` path must still be fixed
  or bridged so future effective runtime modules are dumped automatically.

Next task if blocked:

- Add artifact/UI/log evidence that explains why the dump was not created
  before running another performance or correctness claim.

### Q6K-002: Safe-vs-native static dataflow compare

Status: started.

Purpose: determine whether the bridge understands native Q6 ABI/dataflow before
executing more GPU variants.

Acceptance:

```bash
python3 scripts/compare-spirv-dataflow.py \
  docs/test/spirv-q6k-safe-current/q6k-safe.analysis.json \
  <native-q6.analysis.json> \
  --json-out <safe-vs-native-q6.dataflow.json>
```

Current report:

- `docs/test/spirv-q6k-native-adb45055/safe-vs-native-q6-source.dataflow.json`
  exists.
- The safe kernel and native Q6 module are structurally not equivalent:
  descriptor layout/storage-class shape and store/load origin counts differ.
- This is expected because the safe kernel is a bridge-owned compatibility
  substitute, not a proof that the native module is decoded identically.

The report must continue to either:

- show matching descriptor/push/store origins for the comparable Q6 operation,
  or
- name the first structural mismatch that explains why the safe-kernel and
  native module are not equivalent.

Next task if it fails:

- Create a bridge invariant test at the point where the structural mismatch was
  introduced.  Do not add an end-of-pipeline test only.

### Q6K-003: Native mismatch classifier

Status: in progress.

Purpose: convert "native Q6 mismatch" into exactly one active blocker class.

Acceptance:

The latest artifact must prove which of these classes remains:

1. descriptor/range transport,
2. push/spec interpretation,
3. local-size/workgroup mapping,
4. Q6 arithmetic/reduction,
5. synchronization/device visibility,
6. final output store/writeback.

Evidence required:

- `source_spirv_hash` and `effective_spirv_hash`;
- descriptor writes and binding reflection;
- V4 offset/range/API offset/API range/API memory offset fields;
- push u32 prefix and specialization entries;
- local size and resolved local size;
- upload/dispatch/writeback hashes for writable binding 2;
- CPU-oracle first mismatch or match status;
- prompt sanity result.

Next task if ambiguous:

- Add the missing evidence field at the layer where ambiguity entered.  Do not
  rerun the same compare expecting a different conclusion.

Current classification from
`docs/test/llama-gpu-ngl1-q6-native-dump-adb45055-20260523T205152Z.json`:

- `blocker_class`: `native-q6-final-store-or-readback`;
- `q6_writeback_verified_all`: true;
- `q6_row_indexed_writeback_verified`: true;
- first mismatch: dst index 0, expected `11.7231684`, GPU `5.85118008`;
- native reduction-tree CPU delta is tiny, while GPU-at-dst absolute error is
  large.

This is strong evidence against host fd writeback as the first failing layer,
but not yet enough to choose between native shader arithmetic/reduction and
store-index/value semantics.  Valid-module instrumentation remains the next
decisive step.

### Q6K-004: Valid-module probe bisection

Status: ready to implement after no-op perturbation guard.

Purpose: bisect native Q6 dynamic execution without submitting arbitrary SPIR-V
fragments and without changing the V4 ABI.

Acceptance:

- Probe manifest verifies fail-closed.
- Instrumented module passes post-instrumentation `spirv-val`.
- Debug SSBO is appended as an ordinary V4 storage-buffer binding.
- Runtime descriptor collision is checked before dispatch.
- The probe output identifies whether divergence appears before reduction,
  during reduction, before final store, or after final store/writeback.

Initial probe candidates, in priority order:

1. full final store: block `%1862`, store word index `6653`, line 1466;
   record `%1870` output index, `%1874` value, `%2992`, `%2993`, and local
   invocation id `%915`;
2. tail final store: block `%2865`, store word index `3789`, line 844;
   record `%2873` output index, `%2877` value, `%2926`, `%2927`, and local
   invocation id `%1918`;
3. full reduction store: block `%1781`, store word index `6351`, line 1388;
4. tail reduction store: block `%2784`, store word index `3487`, line 766;
5. full partial-to-workgroup store: block `%1748`, store word index `6198`,
   line 1347;
6. tail partial-to-workgroup store: block `%2751`, store word index `3334`,
   line 725.

Next task if a range still diverges:

- Split the failing valid-module probe range in half and repeat until the
  smallest responsible block/store site is identified.

### Q6K-006: No-op instrumentation perturbation check

Status: blocked by Q6K-001.

Purpose: prove that adding a debug SSBO binding and an otherwise no-op
instrumented valid module does not change native Q6 behavior.

Acceptance:

- pre-instrumentation and post-instrumentation modules both pass `spirv-val`;
- the no-op instrumented module preserves source/effective/probe hash links;
- descriptor set/binding collision checks pass;
- native Q6 mismatch is reproduced with the same first mismatch class;
- if the no-op path changes the output, Q6 block bisection is not allowed to
  start and the new blocker becomes "probe transport perturbs execution".

Next task if failed:

- Fix debug binding transport, descriptor layout, or barrier semantics before
  adding any semantic probe.

### Q6K-007: Workgroup/local-size contract lock

Status: closed for analyzer support, open for verifier/runtime hard gate.

Purpose: stop the investigation from oscillating between stale `[32,2,1]`,
current `[32,1,1]`, literal `[1,1,1]`, and specialization-derived workgroup
interpretations.

Acceptance:

- [x] analyzer records literal local size and `BuiltIn WorkgroupSize`
  specialization constants;
- [x] native-vs-effective comparison records that effective
  `0xe38f6a6a906d765c` is the local-size legalized form of source
  `0x1bf751845c5dce75`;
- [ ] the artifact verifier must classify missing or contradictory workgroup
  evidence before arithmetic/reduction classifications;
- the artifact records literal local size, LocalSizeId ids, BuiltIn
  WorkgroupSize ids, specialization constant ids, specialization values, and
  resolved local size for the same native Q6 dispatch;
- the verifier classifies missing or contradictory workgroup evidence before
  arithmetic/reduction classifications;
- docs and task queue name exactly which tuple is used for the current lane and
  why.

Next task if failed:

- Add analyzer/compare support for specialization-resolved local size before
  another device run is interpreted.

### Q6K-008: Runtime descriptor offset invariant gate

Status: open.

Purpose: prevent descriptor/range/offset coordinate mistakes from entering the
executor unnoticed.

Acceptance:

For every native Q6 V4 binding:

```text
offset == api_memory_offset + api_offset
api_offset + size <= api_buffer_size
api_memory_offset + api_buffer_size <= api_memory_size
VkDescriptorBufferInfo.offset == api_offset
VkDescriptorBufferInfo.range == size
host fd read/write offset == offset
```

The artifact must state whether each invariant passed.  If any invariant fails,
shader arithmetic is not investigated until this layer is fixed.

Next task if failed:

- Add the invariant at ICD send time and executor parse time, then add a
  contract test that rejects inconsistent V4 binding metadata.

Additional strict-passthrough invariant:

- `PDOCKER_VULKAN_ALIAS_COPIES` must not silently alter strict V4 transport.
  In strict mode, an alias-copy hit must be rejected before send or recorded as
  a hard invariant failure, because it can mix copied transport memory with
  original `api_memory_id/api_buffer_id/api_offset` metadata.

### Q6K-009: Descriptor layout static comparison

Status: closed for static analyzer/compare baseline, open for exact mismatch
path reporting.

Purpose: make native-vs-safe comparison sensitive to descriptor element/layout
shape, not only set/binding/read-write flags.

Current implementation:

- `scripts/analyze-spirv.py` now emits descriptor `pointee_layout` with struct
  member offsets/layout decorations and recursive type summaries for pointer,
  struct, array/runtime-array, vector, matrix, scalar, and related decorations.
- The analyzer now emits `workgroup_size_builtin` so native Q6's
  `BuiltIn WorkgroupSize` specialization contract is visible even when
  `OpExecutionModeId` is absent.
- `tests.test_gpu_abi_contract` verifies the embedded safe Q6 descriptor
  layout and the preserved native Q6 WorkgroupSize contract so analyzer updates
  cannot silently drop this evidence.

Acceptance:

- Native Q6 analysis includes descriptor layout for every declared storage
  buffer variable.
- `scripts/compare-spirv-dataflow.py` compares descriptor layout signatures
  between safe and native analyses.
- A layout mismatch reports the exact set/binding/member/type field that
  differs.

Next task if failed:

- Add layout-signature comparison and name-stripped normalization before
  interpreting native Q6 arithmetic.

### Q6K-005: Regression prevention at the mixing point

Status: open.

Purpose: stop recurring update/reflection/env propagation mistakes at their
source instead of adding more late tests.

Acceptance:

- Any new runtime option has one manifest entry and one generated/checked path
  to pdockerd, ICD, executor reflection, compare artifacts, and verifier
  constants.
- Missing executor reflection for correctness-critical env vars is a hard
  failure before Q6 classification.
- Static analyzer/comparison gaps are represented as `blocked` task states,
  not silently ignored.

Next task if a gap appears:

- Add a source-of-truth contract test for that propagation path and remove any
  duplicated literal that allowed drift.

## Closed or parked items

- Safe-kernel correctness path: closed for proving bridge-owned compatibility
  substitution, not closed for native Q6 correctness.
- HTTP liveness and `/v1/models`: parked as readiness only; never correctness.
- Speedup tuning: parked until native or compatibility correctness evidence is
  current and benchmark claims are allowed.
