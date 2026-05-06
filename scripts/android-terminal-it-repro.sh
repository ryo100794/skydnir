#!/usr/bin/env bash
set -euo pipefail

PKG="${PKG:-io.github.ryo100794.pdocker.compat}"
CID="${1:-}"

if [[ -z "$CID" ]]; then
  CID="$(adb shell "run-as $PKG sh -c 'for p in files/pdocker/containers/*/state.json; do grep -q \"\\\"Running\\\": true\" \"\$p\" 2>/dev/null || continue; p=\${p%/state.json}; echo \${p##*/}; exit 0; done; exit 1'" | tr -d '\r')"
fi

if [[ -z "$CID" ]]; then
  echo "No running container found. Start a test container first." >&2
  exit 2
fi

run_case() {
  local name="$1"
  local cmd_json="$2"
  local script="$3"
  local mode="${4:-bulk}"
  local payload
  payload="$(cat <<EOF | base64 -w0
set -eu
cd files/pdocker
cid='$CID'
mode='$mode'
body='{"AttachStdin":true,"AttachStdout":true,"AttachStderr":true,"Tty":true,"Env":["TERM=xterm-256color","COLORTERM=truecolor","ENV=","BASH_ENV="],"Cmd":$cmd_json}'
len=\${#body}
resp=\$({ printf 'POST /containers/%s/exec HTTP/1.1\r\nHost: docker\r\nContent-Type: application/json\r\nContent-Length: %s\r\nConnection: close\r\n\r\n' "\$cid" "\$len"; printf %s "\$body"; } | nc -U -W 5 pdockerd.sock | tr -d '\r')
exec_id=\$(printf '%s\n' "\$resp" | sed -n 's/.*"Id": "\([0-9a-f]*\)".*/\1/p')
test -n "\$exec_id" || { echo "create failed"; echo "\$resp"; exit 3; }
start='{"Detach":false,"Tty":true}'
slen=\${#start}
out=\$({
  printf 'POST /exec/%s/start HTTP/1.1\r\nHost: docker\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Type: application/json\r\nContent-Length: %s\r\n\r\n' "\$exec_id" "\$slen"
  printf %s "\$start"
  sleep 1
  if [ "\$mode" = "top" ]; then
    printf '%b' 'top\r'
    sleep 3
    printf 'q'
    sleep 1
    printf '%b' '\recho pdocker-after-top\rexit\r'
  else
    printf '%b' '$script'
  fi
} | nc -U -W 20 pdockerd.sock | tr -d '\000')
printf '%s\n' "\$out" > "diagnostics/terminal-repro-$name.log"
printf '%s\n' "\$out"
EOF
)"
  timeout 45 adb shell "run-as $PKG sh -c 'printf %s $payload | base64 -d > files/pdocker/diagnostics/terminal-repro-$name.sh && sh files/pdocker/diagnostics/terminal-repro-$name.sh'"
}

history_script='echo pdocker-history-first\rprintf "pdocker-before-up\n"\r\033[A\rprintf "\r"\rprintf "pdocker-after-history\n"\rexit\r'
top_script=''

echo "[terminal-repro] container=$CID"
echo "[terminal-repro] legacy sh history should show escape/control noise or fail to replay"
legacy_out="$(run_case legacy-sh '["/bin/sh","-i"]' "$history_script" || true)"
printf '%s\n' "$legacy_out"
if printf '%s\n' "$legacy_out" | grep -q 'pdocker-history-first.*pdocker-history-first'; then
  echo "legacy-sh unexpectedly replayed history" >&2
  exit 4
fi

echo "[terminal-repro] bash history should replay first command"
bash_out="$(run_case bash-history '["/bin/sh","-lc","if command -v /bin/bash >/dev/null 2>&1; then exec /bin/bash -i; else exec /bin/sh -i; fi"]' "$history_script")"
printf '%s\n' "$bash_out"
count="$(printf '%s\n' "$bash_out" | grep -c 'pdocker-history-first' || true)"
if [[ "$count" -lt 2 ]]; then
  echo "bash-history did not replay command via arrow-up" >&2
  exit 5
fi

echo "[terminal-repro] top should quit with q and return to shell"
top_out="$(run_case top-q '["/bin/sh","-lc","if command -v /bin/bash >/dev/null 2>&1; then exec /bin/bash -i; else exec /bin/sh -i; fi"]' "$top_script" top)"
printf '%s\n' "$top_out" | tail -n 80
printf '%s\n' "$top_out" | grep -q 'pdocker-after-top'

echo "[terminal-repro] passed"
