# Shared Documents

The selected Android Documents folder is mounted into containers at
`/documents` by default when Android grants direct path access. On devices that
only provide SAF access, Skydnir mounts an app-private SAF-mediated mirror at
`/documents` and syncs selected writes back through the Documents actions. The
Android app stores project definitions under `pdocker/projects` in the selected
folder when that path is directly writable.

Set `SKYDNIR_DOCUMENTS_HOST` or `SKYDNIR_DOCUMENTS_MOUNT` before compose up to
use a different host or container path. Containers from different projects can
share one folder by setting the same `SKYDNIR_DOCUMENTS_HOST`, or by using
`SKYDNIR_SHARED_DOCUMENTS_HOST` mounted at `/shared`. Existing `PDOCKER_*`
names remain accepted for old workspaces.

Use `/documents` for exports, benchmark artifacts, models, and handoff files.
Do not put hot build caches, databases, layer scratch data, or high-frequency
logs here; SD-card/Documents storage is expected to be slower than app-private
workspace storage.
