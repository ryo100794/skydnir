#!/usr/bin/env bash
set -euo pipefail

source "/opt/ros/${ROS_DISTRO:-humble}/setup.bash"

display="${VNC_DISPLAY:-:1}"
vnc_port="${VNC_PORT:-5900}"
novnc_port="${NOVNC_PORT:-6080}"
geometry="${VNC_GEOMETRY:-1280x800}"
depth="${VNC_DEPTH:-24}"
workspace="${WORKSPACE:-/workspace}"
log_dir="${workspace}/logs"
vnc_log="${VNC_LOG_FILE:-${log_dir}/xvnc.log}"
xfce_log="${XFCE_LOG_FILE:-${log_dir}/xfce4.log}"
rviz_log="${RVIZ_LOG_FILE:-${log_dir}/rviz2.log}"
glxinfo_log="${GLXINFO_LOG_FILE:-${log_dir}/glxinfo.log}"
novnc_log="${NOVNC_LOG_FILE:-${log_dir}/novnc.log}"

mkdir -p "$log_dir" "${XDG_RUNTIME_DIR:-/tmp/runtime-root}" /root/.vnc
chmod 700 "${XDG_RUNTIME_DIR:-/tmp/runtime-root}" /root/.vnc
rm -f "/tmp/.X${display#:}-lock" "/tmp/.X11-unix/X${display#:}"

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
  for pid in "${novnc_pid:-}" "${rviz_pid:-}" "${xfce_pid:-}" "$xvnc_pid"; do
    if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
  wait >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

export DISPLAY="$display"
export QT_X11_NO_MITSHM="${QT_X11_NO_MITSHM:-1}"
export PDOCKER_GL_BACKEND="${PDOCKER_GL_BACKEND:-llvmpipe}"

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
    ;;
  *)
    echo "Unsupported PDOCKER_GL_BACKEND='${PDOCKER_GL_BACKEND}'; falling back to llvmpipe software rendering."
    export PDOCKER_GL_BACKEND=llvmpipe
    export LIBGL_ALWAYS_SOFTWARE=1
    export GALLIUM_DRIVER=llvmpipe
    ;;
esac

sleep 2
echo "Starting XFCE desktop"
startxfce4 2>&1 | tee -a "$xfce_log" &
xfce_pid=$!

sleep 3
{
  echo "OpenGL diagnostics"
  echo "PDOCKER_GL_BACKEND=${PDOCKER_GL_BACKEND}"
  echo "LIBGL_ALWAYS_SOFTWARE=${LIBGL_ALWAYS_SOFTWARE:-}"
  echo "GALLIUM_DRIVER=${GALLIUM_DRIVER:-}"
  echo "MESA_LOADER_DRIVER_OVERRIDE=${MESA_LOADER_DRIVER_OVERRIDE:-}"
  glxinfo -B
} 2>&1 | tee -a "$glxinfo_log" || true

rviz_args=()
if [ -n "${RVIZ_CONFIG:-}" ]; then
  rviz_args+=("-d" "$RVIZ_CONFIG")
fi
echo "Starting RViz2"
rviz2 "${rviz_args[@]}" 2>&1 | tee -a "$rviz_log" &
rviz_pid=$!

novnc_web="/usr/share/novnc"
if [ ! -f "${novnc_web}/vnc.html" ]; then
  novnc_web="/usr/share/novnc/www"
fi
echo "Starting noVNC on ${novnc_port}, forwarding to 127.0.0.1:${vnc_port}"
websockify --web="$novnc_web" "0.0.0.0:${novnc_port}" "127.0.0.1:${vnc_port}" \
  2>&1 | tee -a "$novnc_log" &
novnc_pid=$!

wait -n "$xvnc_pid" "$xfce_pid" "$rviz_pid" "$novnc_pid"
