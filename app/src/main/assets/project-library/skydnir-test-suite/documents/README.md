# Documents Export

The selected Android Documents folder is mounted at `/documents` by default.
The test suite writes report artifacts under:

```text
/documents/skydnir-exports/skydnir-test-suite/
```

The app stores project definitions under `pdocker/projects` in the selected
Android Documents folder when that workspace-root mode is enabled. Test reports
are explicit exchange artifacts. Do not put hot build caches, layer data, model
files, or temporary compiler outputs here by default; keep them on the fast
app-private workspace.

Use `SKYDNIR_DOCUMENTS_HOST`, `SKYDNIR_DOCUMENTS_MOUNT`,
`SKYDNIR_SHARED_DOCUMENTS_HOST`, or `SKYDNIR_SHARED_DOCUMENTS_MOUNT` when
multiple projects intentionally need to share the same folder.
