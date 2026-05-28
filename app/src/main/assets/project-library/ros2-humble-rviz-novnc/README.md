# ROS 2 Humble RViz noVNC

Ubuntu 22.04 workspace with ROS 2 Humble desktop, RViz, Xvnc, and noVNC.

The compose header comment `# skydnir.service-url: 18082=noVNC RViz` labels
the local browser shortcut. Open noVNC at port `18082`; direct VNC clients can
connect to host port `15900`.

The compose template mounts the selected Android Documents folder at
`/documents` by default. Use it only for explicit import/export or data
exchange. Override `SKYDNIR_DOCUMENTS_HOST` or `SKYDNIR_DOCUMENTS_MOUNT` to
share a folder or move the mount path, or use `SKYDNIR_SHARED_DOCUMENTS_HOST`
for the cross-project `/shared` mount.

Build note: the Dockerfile downloads the `novnc` Debian package from the
configured Ubuntu apt repository and extracts its static files without running
the package maintainer scripts.

The default graphics path is Mesa software rendering:

- `PDOCKER_GL_BACKEND=llvmpipe`
- `LIBGL_ALWAYS_SOFTWARE=1`
- `GALLIUM_DRIVER=llvmpipe`

`PDOCKER_GL_BACKEND=zink-experimental` exposes a future Mesa Zink experiment
path in startup logs only. This template does not claim Vulkan/Zink acceleration works.

Runtime logs stream to stdout/stderr and are also mirrored under
`workspace/logs/`. Set `RVIZ_CONFIG=/workspace/path/to/config.rviz` to start
RViz with a specific config file.
