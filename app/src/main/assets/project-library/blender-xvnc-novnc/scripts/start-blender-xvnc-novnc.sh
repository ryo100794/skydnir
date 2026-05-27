#!/usr/bin/env bash
set -euo pipefail

display="${VNC_DISPLAY:-:1}"
vnc_port="${VNC_PORT:-5901}"
novnc_port="${NOVNC_PORT:-6080}"
geometry="${VNC_GEOMETRY:-1280x800}"
depth="${VNC_DEPTH:-24}"
workspace="${WORKSPACE:-/workspace}"
log_dir="${workspace}/logs"
vnc_log="${VNC_LOG_FILE:-${log_dir}/xvnc.log}"
wm_log="${WM_LOG_FILE:-${OPENBOX_LOG_FILE:-${log_dir}/wm.log}}"
blender_log="${BLENDER_LOG_FILE:-${log_dir}/blender.log}"
glxinfo_log="${GLXINFO_LOG_FILE:-${log_dir}/glxinfo.log}"
novnc_log="${NOVNC_LOG_FILE:-${log_dir}/novnc.log}"

mkdir -p "$log_dir" "${XDG_RUNTIME_DIR:-/tmp/runtime-root}" /root/.vnc
chmod 700 "${XDG_RUNTIME_DIR:-/tmp/runtime-root}" /root/.vnc
rm -f "/tmp/.X${display#:}-lock" "/tmp/.X11-unix/X${display#:}"

export DISPLAY="$display"
export QT_X11_NO_MITSHM="${QT_X11_NO_MITSHM:-1}"
export PDOCKER_GL_BACKEND="${PDOCKER_GL_BACKEND:-llvmpipe}"
export PDOCKER_GRAPHICS_MODE="${PDOCKER_GRAPHICS_MODE:-software}"
export PDOCKER_ZINK_EXPERIMENTAL="${PDOCKER_ZINK_EXPERIMENTAL:-0}"

if [ -n "${PDOCKER_VULKAN_ICD_FILENAMES:-}" ]; then
  export VK_ICD_FILENAMES="$PDOCKER_VULKAN_ICD_FILENAMES"
fi

case "$PDOCKER_GL_BACKEND" in
  llvmpipe|software)
    export PDOCKER_GL_BACKEND=llvmpipe
    export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"
    export GALLIUM_DRIVER="${GALLIUM_DRIVER:-llvmpipe}"
    echo "Skydnir GL backend: PDOCKER_GL_BACKEND=llvmpipe (Mesa llvmpipe software rendering)"
    ;;
  zink-experimental)
    echo "Skydnir GL backend: PDOCKER_GL_BACKEND=zink-experimental (future Mesa Zink path; acceleration is not validated by this template)"
    if [ "${LIBGL_ALWAYS_SOFTWARE:-1}" = "1" ]; then
      export LIBGL_ALWAYS_SOFTWARE=0
    fi
    if [ "${GALLIUM_DRIVER:-llvmpipe}" = "llvmpipe" ]; then
      export GALLIUM_DRIVER=zink
    fi
    if [ "$PDOCKER_ZINK_EXPERIMENTAL" != "1" ]; then
      echo "Set PDOCKER_ZINK_EXPERIMENTAL=1 with PDOCKER_GL_BACKEND=zink-experimental when staging future Zink validation."
    fi
    ;;
  *)
    echo "Unsupported PDOCKER_GL_BACKEND='${PDOCKER_GL_BACKEND}'; falling back to llvmpipe software rendering."
    export PDOCKER_GL_BACKEND=llvmpipe
    export LIBGL_ALWAYS_SOFTWARE=1
    export GALLIUM_DRIVER=llvmpipe
    ;;
esac

if [ "$PDOCKER_ZINK_EXPERIMENTAL" = "1" ]; then
  echo "PDOCKER_ZINK_EXPERIMENTAL=1 exposes Mesa Zink/Vulkan env switches for future validation only."
fi

echo "Starting Xvnc on ${display} with VNC port ${vnc_port}"
Xvnc "$display" \
  -geometry "$geometry" \
  -depth "$depth" \
  -rfbport "$vnc_port" \
  -SecurityTypes None \
  -localhost no \
  -alwaysshared \
  2>&1 | tee -a "$vnc_log" &
xvnc_pid=$!

cleanup() {
  trap - EXIT INT TERM
  for pid in "${novnc_pid:-}" "${blender_pid:-}" "${wm_pid:-}" "$xvnc_pid"; do
    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
  wait >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

sleep 2
wm_pid=""
if command -v matchbox-window-manager >/dev/null 2>&1; then
  echo "Starting Matchbox window manager"
  matchbox-window-manager 2>&1 | tee -a "$wm_log" &
  wm_pid=$!
elif command -v openbox-session >/dev/null 2>&1; then
  echo "Starting Openbox window manager"
  openbox-session 2>&1 | tee -a "$wm_log" &
  wm_pid=$!
else
  echo "No window manager installed; continuing with unmanaged X11 session" | tee -a "$wm_log"
fi

sleep 2
{
  echo "OpenGL/GLSL diagnostics"
  echo "PDOCKER_GRAPHICS_MODE=${PDOCKER_GRAPHICS_MODE}"
  echo "LIBGL_ALWAYS_SOFTWARE=${LIBGL_ALWAYS_SOFTWARE:-}"
  echo "GALLIUM_DRIVER=${GALLIUM_DRIVER:-}"
  echo "MESA_LOADER_DRIVER_OVERRIDE=${MESA_LOADER_DRIVER_OVERRIDE:-}"
  echo "VK_ICD_FILENAMES=${VK_ICD_FILENAMES:-}"
  glxinfo -B
} 2>&1 | tee -a "$glxinfo_log" || true

blender_args=()
if [ -n "${BLENDER_STARTUP_FILE:-}" ]; then
  blender_args+=("$BLENDER_STARTUP_FILE")
else
  blender_args+=("--factory-startup")
fi
if [ -n "${BLENDER_EXTRA_ARGS:-}" ]; then
  read -r -a extra_args <<< "$BLENDER_EXTRA_ARGS"
  blender_args+=("${extra_args[@]}")
fi

echo "Starting Blender"
stdbuf -oL -eL blender "${blender_args[@]}" 2>&1 | tee -a "$blender_log" &
blender_pid=$!

novnc_web="/usr/share/novnc"
if [ ! -f "${novnc_web}/vnc.html" ]; then
  novnc_web="/usr/share/novnc/www"
fi
echo "Starting noVNC on ${novnc_port}, forwarding to 127.0.0.1:${vnc_port}"
websockify --web="$novnc_web" "0.0.0.0:${novnc_port}" "127.0.0.1:${vnc_port}" \
  2>&1 | tee -a "$novnc_log" &
novnc_pid=$!

wait_pids=("$xvnc_pid" "$blender_pid" "$novnc_pid")
if [ -n "$wm_pid" ]; then
  wait_pids+=("$wm_pid")
fi
wait -n "${wait_pids[@]}"
