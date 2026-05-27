# Blender Xvnc noVNC

Ubuntu 24.04 workspace with Blender, Xvnc, noVNC, Matchbox, and Mesa graphics diagnostics for OpenGL/GLSL workloads.

The compose header comment `# skydnir.service-url: 18083=noVNC Blender` labels
the local browser shortcut. Open noVNC at port `18083`; direct VNC clients can
connect to host port `15901`.

The compose template mounts the selected Android Documents folder at
`/documents` by default. Use it only for explicit import/export or data
exchange. Override `PDOCKER_DOCUMENTS_HOST` or `PDOCKER_DOCUMENTS_MOUNT` to
share a folder or move the mount path, or use `PDOCKER_SHARED_DOCUMENTS_HOST`
for the cross-project `/shared` mount.

Build note: the Dockerfile downloads the `novnc` Debian package from the
configured Ubuntu apt repository and extracts its static files without running
the package maintainer scripts.

The default graphics path is Mesa software rendering:

- `LIBGL_ALWAYS_SOFTWARE=1`
- `GALLIUM_DRIVER=llvmpipe`
- `PDOCKER_GL_BACKEND=llvmpipe`
- `PDOCKER_GRAPHICS_MODE=software`

Future Zink/Skydnir Vulkan validation can be staged by setting
`PDOCKER_GL_BACKEND=zink-experimental`. Optional lower-level switches include
`PDOCKER_ZINK_EXPERIMENTAL`, `LIBGL_ALWAYS_SOFTWARE`, `GALLIUM_DRIVER`,
`MESA_LOADER_DRIVER_OVERRIDE`, `VK_ICD_FILENAMES`, or
`PDOCKER_VULKAN_ICD_FILENAMES`. These switches only expose the intended
experiment surface; this template does not claim Vulkan/Zink acceleration works.
Startup logs print the selected `PDOCKER_GL_BACKEND` so llvmpipe software
rendering and the future Zink experiment path are easy to distinguish.

Runtime logs stream to stdout/stderr and are also mirrored under
`workspace/logs/`:

- `xvnc.log`
- `wm.log`
- `glxinfo.log`
- `blender.log`
- `novnc.log`

Set `BLENDER_STARTUP_FILE=/workspace/scene.blend` to open a scene file, or
pass simple additional command line flags with `BLENDER_EXTRA_ARGS`.
