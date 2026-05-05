# Shared Documents

The selected Android Documents folder is mounted into containers at
`/documents` by default. The Android app stores project definitions under
`pdocker/projects` in that selected folder.

Set `PDOCKER_DOCUMENTS_HOST` or `PDOCKER_DOCUMENTS_MOUNT` before compose up to
use a different host or container path. Containers from different projects can
share one folder by setting the same `PDOCKER_DOCUMENTS_HOST`, or by using
`PDOCKER_SHARED_DOCUMENTS_HOST` mounted at `/shared`.

Use `/documents` for exports, benchmark artifacts, models, and handoff files.
Do not put hot build caches, databases, layer scratch data, or high-frequency
logs here; SD-card/Documents storage is expected to be slower than app-private
workspace storage.
