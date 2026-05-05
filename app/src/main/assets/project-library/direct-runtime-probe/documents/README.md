# Documents Mount

The selected Android Documents folder is mounted at `/documents` by default.
Use it for exporting probe reports or exchanging files with other apps. Default
compose up writes probe diagnostics to:

`/documents/pdocker-exports/direct-runtime-probe/latest.log`

and a machine-readable summary to:

`/documents/pdocker-exports/direct-runtime-probe/latest.json`

Do not put hot build caches, compiler temporary files, package-manager caches,
or tight test loops here. Keep those under `/workspace` or app-private pdocker
storage, then copy selected results to `/documents/pdocker-exports`.

Project definitions are stored under `pdocker/projects` in the selected
Android Documents folder. Cross-project shared data can use
`PDOCKER_SHARED_DOCUMENTS_HOST` mounted at `/shared`.
