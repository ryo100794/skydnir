# GPU Host-Native Baseline

- Date: 20260714T004326Z UTC.
- Runs: 2.
- Scope: Android native executor inside the APK app process domain.
- This is not CPU emulation; Vulkan samples use the Android Vulkan backend.

| Probe | Backend | Valid | Steady median ms | Steady mean ms | Dispatch median ms | Transport |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Host CPU matmul256 | cpu_scalar | 2/2 | 25.1166 | 25.1166 | 25.1166 | host-cpu-local-process-buffer |
| Host Vulkan matmul256 resident | android_vulkan | 2/2 | 0.5575 | 0.5575 | 0.5492 | direct-vulkan-resident-buffer |
| Host CPU vector-add | cpu_scalar | 2/2 | 0.1368 | 0.1368 | 0.1368 | host-cpu-local-process-buffer |
| Host Vulkan vector-add resident | android_vulkan | 2/2 | 0.4890 | 0.4890 | 0.4573 | direct-vulkan-resident-buffer |

## Ratios

- Host CPU matmul256 / host Vulkan resident matmul256: 45.0523x.
- Host Vulkan resident matmul256 / host CPU matmul256: 0.0222x.
- Host CPU vector-add / host Vulkan resident vector-add: 0.2798x.
- Host Vulkan resident vector-add / host CPU vector-add: 3.5746x.

Interpretation: matmul is the useful LLM-shaped probe. Vector-add is intentionally retained as a transfer/dispatch overhead canary, and CPU may win there.
