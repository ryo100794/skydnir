# GPU Host-Native Baseline

- Date: 20260505T022346Z UTC.
- Runs: 2.
- Scope: Android native executor inside the APK app process domain.
- This is not CPU emulation; Vulkan samples use the Android Vulkan backend.

| Probe | Backend | Valid | Steady median ms | Steady mean ms | Dispatch median ms | Transport |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Host CPU matmul256 | cpu_scalar | 2/2 | 36.4817 | 36.4817 | 36.4817 | host-cpu-local-process-buffer |
| Host Vulkan matmul256 resident | android_vulkan | 2/2 | 1.1628 | 1.1628 | 1.1324 | direct-vulkan-resident-buffer |
| Host CPU vector-add | cpu_scalar | 2/2 | 0.0806 | 0.0806 | 0.0806 | host-cpu-local-process-buffer |
| Host Vulkan vector-add resident | android_vulkan | 2/2 | 0.6184 | 0.6184 | 0.5297 | direct-vulkan-resident-buffer |

## Ratios

- Host CPU matmul256 / host Vulkan resident matmul256: 31.3740x.
- Host Vulkan resident matmul256 / host CPU matmul256: 0.0319x.
- Host CPU vector-add / host Vulkan resident vector-add: 0.1304x.
- Host Vulkan resident vector-add / host CPU vector-add: 7.6677x.

Interpretation: matmul is the useful LLM-shaped probe. Vector-add is intentionally retained as a transfer/dispatch overhead canary, and CPU may win there.
