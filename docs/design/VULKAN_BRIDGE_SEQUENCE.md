# Vulkan bridge call sequence and data flow

Snapshot date: 2026-05-24.

This document describes the implementation sequence for pdocker's Vulkan
bridge as it exists in the Android project.  It focuses on the real data path
used by unmodified container applications such as llama.cpp:

```text
glibc Vulkan application
  -> pdocker glibc Vulkan ICD
  -> pdocker GPU command ABI
  -> APK-owned Android/Bionic Vulkan executor
  -> Android Vulkan driver
  -> writeback and JSON evidence
```

The bridge is not raw vendor-library passthrough.  The container must not load
Android/Bionic GPU libraries directly.  Instead, the container-facing ICD
captures standard Vulkan calls, serializes the command state, and the APK-side
executor reconstructs Android Vulkan objects from that state.

## Source map

| Layer | Primary implementation | Responsibility |
|---|---|---|
| Container Vulkan ICD | `docker-proot-setup/gpu/pdocker_vulkan_icd.c` | glibc-facing Vulkan entry points, object/handle tracking, command serialization, fd passing |
| Shared GPU ABI | `docker-proot-setup/gpu/pdocker_gpu_abi.h`, `app/src/main/cpp/pdocker_gpu_abi.h` | command names, field names, feature flags, option propagation |
| Android executor | `app/src/main/cpp/pdocker_gpu_executor.c` | command parse, validation, optional compatibility lowerings, Android Vulkan execution, readback, diagnostics |
| Device package | `app/src/main/jniLibs/*/libpdockergpuexecutor.so` | packaged Android executable payload |
| Evidence tooling | `scripts/android-llama-gpu-compare.sh`, `scripts/android-llama-gpu-q6-workgroup-run.sh`, `tests/test_gpu_abi_contract.py` | repeatable runs, ABI/env/test guardrails |

## Top-level call sequence

```mermaid
sequenceDiagram
    participant App
    participant Loader
    participant ICD
    participant Socket
    participant Executor
    participant Driver
    participant Files
    participant Evidence

    App->>Loader: create instance and query devices
    Loader->>ICD: ICD entry points
    ICD-->>App: advertise limits and features

    App->>ICD: create buffers and memory
    ICD->>ICD: remember logical object graph
    ICD->>Files: keep backing fds and offsets

    App->>ICD: create shader module
    ICD->>Files: stage shader bytes in a shader fd
    ICD->>ICD: hash source shader bytes

    App->>ICD: vkUpdateDescriptorSets
    ICD->>ICD: record set binding range and ids

    App->>ICD: push constants and dispatch
    ICD->>ICD: record push bytes and dispatch dimensions

    App->>ICD: submit queue work
    ICD->>Socket: send dispatch v4 command
    ICD->>Socket: pass shader and buffer fds
    Socket->>Executor: deliver command and fds

    Executor->>Executor: parse and validate command
    Executor->>Executor: reflect shader and resolve specialization
    Executor->>Executor: apply scoped compatibility lowerings
    Executor->>Driver: create android vulkan objects
    Executor->>Driver: upload and synchronize inputs
    Executor->>Driver: submit compute work
    Executor->>Driver: wait and read mapped memory
    Executor->>Files: write changed output ranges
    Executor->>Evidence: emit json evidence
    Executor-->>ICD: dispatch result
    ICD-->>App: Vulkan call returns status
```

## Command payload sequence

`VULKAN_DISPATCH_V4` is the main compute dispatch packet.  The text command and
the fd array are both part of the payload; either one alone is incomplete.

```mermaid
sequenceDiagram
    participant ICD
    participant ABI
    participant Socket
    participant Executor

    ICD->>ABI: choose dispatch v4 command
    ICD->>ICD: canonicalize entry name, push bytes, specialization data
    ICD->>ICD: enumerate descriptor bindings in application order
    ICD->>ICD: append strict object fields per binding
    ICD->>Socket: send text command fields
    ICD->>Socket: pass shader fd and buffer fds
    Socket->>Executor: deliver command line and fd table
    Executor->>Executor: validate counts ranges and fd count
    Executor->>ABI: interpret options and defaults
    Executor->>Executor: build binding records and option flags
```

The command includes these field groups:

1. **Shader identity**: `shader_size`, entry point, source SPIR-V bytes, source
   hash.
2. **Dispatch identity**: `dispatch_x`, `dispatch_y`, `dispatch_z`.
3. **Push constants**: byte count and hex payload.  The executor must not
   reinterpret the push struct as a C struct unless a diagnostic oracle is
   explicitly analyzing a known callsite.
4. **Specialization constants**: `{constantID, offset, size}` map entries and
   the raw specialization data blob.
5. **Descriptor bindings**: descriptor set, binding, transfer offset/size,
   Vulkan-visible descriptor offset/range, buffer size, memory offset/size,
   memory id, and buffer id.
6. **Policy/options**: strict passthrough, device-local staging, descriptor
   access reflection, local-size legalization, specialization materialization,
   safe-kernel diagnostics, caches, dirty probes, feature disables, and oracle
   settings.

## Data flow and transformation map

The design rule is simple: preserve application-visible Vulkan semantics first;
only apply narrow, recorded compatibility lowerings when Android driver behavior
requires it.  Every transformation must be visible in JSON evidence.

| Data item | ICD capture | Executor use | Allowed processing | Evidence fields |
|---|---|---|---|---|
| SPIR-V module | copied byte-for-byte into shader fd | read from fd, summarized, optionally patched | hash, reflection, scoped LocalSize legalization, scoped specialization materialization, optional diagnostic safe-kernel replacement | `source_spirv_hash`, `effective_spirv_hash`, `oracle_spirv_hash`, `local_size_patched`, `specialization_materialized`, `q6k_safe_kernel` |
| Entry point | Vulkan string from app | pipeline shader stage `pName` | length validation and NUL termination only | `entry` |
| Specialization constants | raw `VkSpecializationInfo` map/data | either passed to driver or materialized into SPIR-V | validate ranges; scoped Q6/Q4 materialization only | `specializations`, `pipeline_key.spec_hash`, `specialization_materialize_report` |
| Push constants | raw bytes | `vkCmdPushConstants` | no product-path interpretation; diagnostic oracles may decode known callsites | `push`, `push_size`, oracle reports |
| Descriptor set/binding | `vkUpdateDescriptorSets` records | descriptor set layout and writes | preserve set/binding; optional duplicate descriptor materialization only when recorded | descriptor write report, alias report |
| Descriptor offset/range | `VkDescriptorBufferInfo.offset/range` | descriptor write offset/range | preserve descriptor coordinate system; do not confuse with memory-file offset | `api_offset`, `api_range`, `binding_gpu_offset`, `binding_descriptor_offset` |
| Buffer/memory identity | ICD logical ids and backing fd | strict object graph or staged buffers | preserve logical identity in strict mode; alias grouping only for transfer efficiency | `strict_object_graph`, `api_memory_id`, `api_buffer_id`, alias hazards |
| Input bytes | backing fd range | upload to mapped Android VkBuffer memory | read/upload when shader reflection says readable or strict transfer requires it | upload hashes, `read_bindings`, skipped upload bytes |
| Output bytes | backing fd range | read back after fence/invalidate | write back only shader-writable bindings; preserve alias evidence | writeback hashes, `write_bindings`, dirty/writeback reports |
| Barriers/fences | submit/wait sequence | Android command buffer barriers + fence wait | required host/device visibility synchronization | `pre_barriers`, `post_barriers`, dispatch timings |

## SPIR-V processing sequence

```mermaid
sequenceDiagram
    participant Executor
    participant Shader
    participant Reflection
    participant Lowering
    participant Driver

    Executor->>Shader: read shader fd exactly
    Executor->>Reflection: summarize shader
    Reflection-->>Executor: hash capabilities and local size
    Executor->>Reflection: collect descriptor access
    Reflection-->>Executor: used readable writable table

    alt scoped compatibility enabled
        Executor->>Lowering: patch literal local size
        Lowering-->>Executor: local size patched result
        Executor->>Lowering: materialize specialization constants
        Lowering-->>Executor: materialization report
    else normal strict passthrough
        Executor->>Driver: pass original shader and specialization data
    end

    Executor->>Reflection: summarize effective shader
    Executor->>Driver: create shader module
    Executor->>Driver: create compute pipeline
```

### SPIR-V processing rules

- Hashing and reflection are observation.  They do not change shader bytes.
- `patch_spirv_literal_local_size_from_spec()` is a narrow compatibility
  lowering for shaders that expose intended workgroup shape through
  specialization-backed `WorkgroupSize` while carrying a stale literal
  `LocalSize 1,1,1`.
- `materialize_spirv_specialization_constants()` converts scoped
  `OpSpecConstant*` values into ordinary `OpConstant*` values only when the
  expression tree is understood and the result does not grow the module.
- The materializer now reports why it did or did not rewrite:
  `failure_reason`, folded counts, first unsupported opcode/spec-op, output
  word count, and WorkgroupSize subtree preservation.
- Diagnostic safe kernels are not product passthrough.  They are controlled
  probes to split bridge/object-graph bugs from native shader behavior.

## Descriptor and memory sequence

```mermaid
sequenceDiagram
    participant ICD
    participant Executor
    participant ObjectGraph
    participant Buffer
    participant Driver
    participant BackingFd

    ICD->>ICD: record buffer memory and bind range
    ICD->>ICD: record descriptor offset and range
    ICD->>Executor: send binding fields and fd

    Executor->>Executor: validate ranges and ids
    Executor->>Executor: reflect shader access
    Executor->>Executor: compute transfer intent
    Executor->>Executor: group overlap ranges for transfer

    alt strict passthrough
        Executor->>ObjectGraph: create captured objects
        ObjectGraph->>Buffer: preserve buffer and descriptor coordinates
    else compatibility staging
        Executor->>Buffer: create compact staging buffer
    end

    Executor->>BackingFd: read readable ranges
    Executor->>Buffer: upload bytes to mapped memory
    Executor->>Driver: bind descriptors
    Driver-->>Buffer: shader reads and writes
    Executor->>Buffer: read mapped memory after fence
    Executor->>BackingFd: write writable ranges
```

The important distinction is between **descriptor coordinates** and **backing
memory coordinates**:

```text
descriptor-visible address = VkDescriptorBufferInfo.offset
backing-file address       = api_memory_offset + VkDescriptorBufferInfo.offset
```

Strict mode must preserve both.  Any optimization that stages a smaller byte
range must still make descriptor offsets and buffer bounds appear exactly as the
application requested.

## Current Q6_K diagnostic flow

The current llama GPU work is focused on Q6_K correctness.  The relevant
sequence is:

```mermaid
sequenceDiagram
    participant Llama
    participant ICD
    participant Executor
    participant Oracle
    participant Driver
    participant Report

    Llama->>ICD: create q6 shader pipeline
    Llama->>ICD: update q6 descriptors
    ICD->>Executor: send dispatch v4 command
    ICD->>Executor: pass shader and buffer fds
    Executor->>Executor: classify q6 source hash
    Executor->>Executor: legalize local size if requested
    Executor->>Executor: materialize specialization constants if scoped
    Executor->>Executor: preserve descriptors and transfer intent
    Executor->>Oracle: run cpu oracle when enabled
    Executor->>Driver: submit android vulkan dispatch
    Executor->>Report: write hashes and descriptor evidence
    Executor->>Report: write oracle deltas and materialization report
```

Known current state:

- The application-facing llama.cpp code, Dockerfile, model, and prompt remain
  unchanged.
- Q6 strict passthrough reaches Android Vulkan execution.
- Q6 writeback from GPU memory to the container range is verified, but output
  correctness is not yet proven.
- The newest evidence point being added is the specialization materialization
  decision report, because Q6 currently requests materialization but has not
  shown an effective rewrite.

## JSON evidence sequence

```mermaid
sequenceDiagram
    participant Executor
    participant Json
    participant Verifier
    participant Plan

    Executor->>Json: compact event for every dispatch
    Executor->>Json: optional profile details
    Executor->>Json: reconciliation report
    Executor->>Json: descriptor reports
    Executor->>Json: cpu oracle report
    Executor->>Json: materialization report
    Verifier->>Json: classify readiness and correctness
    Verifier->>Plan: reference evidence path
```

Evidence must be specific enough to answer:

1. Did the ICD send the same Vulkan fields llama.cpp requested?
2. Did the executor receive and validate those fields without loss?
3. Was any SPIR-V or descriptor transformation applied?
4. If yes, which exact scoped rule applied and what hashes changed?
5. Did Android Vulkan execute, synchronize, and write back the expected ranges?
6. Did deterministic prompt and/or Q6 oracle correctness pass?

## Non-goals and claim gates

- This bridge does not inject Android vendor Vulkan libraries into the glibc
  image.
- It does not modify llama.cpp, its Dockerfile, its model, or its prompt to
  obtain GPU correctness.
- Diagnostic safe kernels, CPU oracles, and shader materialization are not
  performance claims.
- A benchmark claim is allowed only when the verifier has a passing
  reconciliation artifact and prompt/oracle correctness for the same run.
