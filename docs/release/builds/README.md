# Fixed Build Evidence

Snapshot date: 2026-05-17.

## Purpose

This directory stores immutable release-candidate build records. These records
are distinct from mutable `latest` test artifacts in `docs/test/`.

## Builds

| Build | Scope |
|---|---|
| [`20260505.1/`](20260505.1/) | Fixed Android build 20260505.1 evidence and release gate logs |

## Maintenance

- Store one directory per fixed build number.
- Do not rewrite a published build record; create a new build directory when
  evidence changes materially.
- Keep large generated logs here only when they are part of the fixed build
  evidence for release review.
