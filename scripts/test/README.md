# Test script implementations

This directory contains relocated test and smoke implementations behind stable
top-level wrappers. The wrapper paths remain the compatibility surface for
docs, CI, and manual commands unless a deliberate migration says otherwise.

| Implementation | Stable wrapper |
|---|---|
| `smoke-opencl-bridge.sh` | [`../smoke-opencl-bridge.sh`](../smoke-opencl-bridge.sh) |
| `smoke-vulkan-icd-bridge.sh` | [`../smoke-vulkan-icd-bridge.sh`](../smoke-vulkan-icd-bridge.sh) |
| `smoke-vulkan-llama-init.sh` | [`../smoke-vulkan-llama-init.sh`](../smoke-vulkan-llama-init.sh) |
| `verify-device-llama-template.sh` | [`../verify-device-llama-template.sh`](../verify-device-llama-template.sh) |

Prerequisites are unchanged from the wrapper commands: built GPU/native helper
libraries or an Android device/ADB session may be required depending on the
specific smoke.

Policy: keep docs and CI on the stable wrappers unless the inventory,
runbooks, and tests are migrated in the same focused change.
