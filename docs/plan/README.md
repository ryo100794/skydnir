# Plan Documents

Snapshot date: 2026-05-20.

## Purpose

This category tracks current status, active work, and historical steering
snapshots. It should answer what is done, what is next, what is blocked, and
which temporary accommodations must be replaced.

## Contents

| Document | Scope |
|---|---|
| [`STATUS.md`](STATUS.md) | Current implementation status summary |
| [`TODO.md`](TODO.md) | Live unfinished-work ledger and temporary workaround tracker |
| [`ADB_OFF_TASK_QUEUE_20260520.md`](ADB_OFF_TASK_QUEUE_20260520.md) | Completed/maintenance ledger for host-only ADB-off work from 2026-05-20 |
| [`LLAMA_GPU_BRIDGE_NEXT_STEPS.md`](LLAMA_GPU_BRIDGE_NEXT_STEPS.md) | Active llama.cpp GPU bridge procedure, stage gates, and compact-model handoff |
| [`VULKAN_ICD_LLAMA_LIMITS_GAP.md`](VULKAN_ICD_LLAMA_LIMITS_GAP.md) | Vulkan ICD advertised limits/features gap table for llama.cpp/ggml-vulkan bridge work |
| [`ARM32_DIRECT_EXEC_PORTING.md`](ARM32_DIRECT_EXEC_PORTING.md) | Porting plan and promotion gates for replacing the `armeabi-v7a` unsupported direct-exec stub |
| [`INCOMPLETE_IMPLEMENTATION_AUDIT_20260513.md`](INCOMPLETE_IMPLEMENTATION_AUDIT_20260513.md) | Consolidated P0/P1/P2 incomplete-work audit |
| [`ISSUE_WORKFLOW.md`](ISSUE_WORKFLOW.md) | GitHub Issue workflow and TODO/timeline synchronization |
| [`AGENT_COORDINATION.md`](AGENT_COORDINATION.md) | Multi-agent coordination ledger and ownership notes |
| [`DOCUMENTATION_REORGANIZATION_PLAN.md`](DOCUMENTATION_REORGANIZATION_PLAN.md) | Historical documentation cleanup findings and proposed reorganization sequence |
| [`EXECUTION_TIMELINE_20260513.md`](EXECUTION_TIMELINE_20260513.md) | Historical execution timeline snapshot from 2026-05-13 |
| [`GOAL_EXECUTION_QUEUE_20260513.md`](GOAL_EXECUTION_QUEUE_20260513.md) | Historical goal execution queue snapshot from 2026-05-13 |
| [`REPLAN_2026-05-01.md`](REPLAN_2026-05-01.md) | Historical replan snapshot after UI/build/GPU steering |

## Canonical Sources

- Use [`STATUS.md`](STATUS.md) for the current implementation summary.
- Use [`../release/RELEASE_READINESS.md`](../release/RELEASE_READINESS.md) for
  public release posture, blocker summary, and the release-candidate checklist.
- Use [`INCOMPLETE_IMPLEMENTATION_AUDIT_20260513.md`](INCOMPLETE_IMPLEMENTATION_AUDIT_20260513.md)
  as the source list for P0/P1/P2 unfinished-work classification.
- Use [`TODO.md`](TODO.md) for active work, temporary accommodations, blockers,
  and acceptance checks.
- Use [`ADB_OFF_TASK_QUEUE_20260520.md`](ADB_OFF_TASK_QUEUE_20260520.md) as
  the completed ADB-off maintenance ledger and as the pattern for future
  host-only work that must not promote device-gated features.
- Use [`LLAMA_GPU_BRIDGE_NEXT_STEPS.md`](LLAMA_GPU_BRIDGE_NEXT_STEPS.md) for
  the current llama.cpp GPU bridge continuation plan and handoff instructions.
- Use [`VULKAN_ICD_LLAMA_LIMITS_GAP.md`](VULKAN_ICD_LLAMA_LIMITS_GAP.md) when
  deciding whether a Q6/llama GPU artifact is blocked by an advertised
  property/feature mismatch rather than shader or writeback behavior.
- Use [`ARM32_DIRECT_EXEC_PORTING.md`](ARM32_DIRECT_EXEC_PORTING.md) before
  changing the `armeabi-v7a` direct executor from explicit unsupported status
  to real process execution.
- Use GitHub Issues as the primary tracker for actionable work that can be
  assigned, discussed, validated, and closed. Mirror only short issue-linked
  summaries into [`TODO.md`](TODO.md).
- Use [`ISSUE_WORKFLOW.md`](ISSUE_WORKFLOW.md) for the issue promotion and
  timeline synchronization rules.
- Use [`AGENT_COORDINATION.md`](AGENT_COORDINATION.md) for multi-agent ownership
  notes, and use [`../maintenance/DOCUMENTATION_DEDUP_BACKLOG.md`](../maintenance/DOCUMENTATION_DEDUP_BACKLOG.md)
  for current documentation deduplication routing.
- Use [`REPLAN_2026-05-01.md`](REPLAN_2026-05-01.md) only as historical
  context; do not refresh it with live status.
- Link to [`../test/COMPATIBILITY.md`](../test/COMPATIBILITY.md) for test
  evidence and [`../design/README.md`](../design/README.md) for architecture
  decisions.

## Maintenance

- Update [`TODO.md`](TODO.md) whenever a workaround is added or retired.
- Keep historical snapshots stable; update the live plan instead.
- Link to [`../test/COMPATIBILITY.md`](../test/COMPATIBILITY.md) for test
  evidence.
- Link to [`../design/README.md`](../design/README.md) for architectural
  decisions.
