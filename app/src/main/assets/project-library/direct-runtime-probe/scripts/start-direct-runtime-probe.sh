#!/bin/sh
set -eu

mkdir -p /workspace /reports /documents /shared
export_dir="${PDOCKER_EXPORT_DIR:-/documents/skydnir-exports}/direct-runtime-probe"
mkdir -p "$export_dir"

cat > /reports/README.txt <<'EOF'
Skydnir direct runtime probe container

Default compose up runs `pdocker-container-probe` once and writes diagnostics
to /reports and to the selected Android Documents export folder.

The repository runner can also execute the same payload with direct-executor
memory guard controls:

ROOTFS=/path/to/rootfs scripts/verify-heavy.sh --container-probe
EOF

timestamp="$(date -u +%Y%m%dT%H%M%SZ 2>/dev/null || date +%s)"
log="/reports/direct-runtime-probe-$timestamp.log"
latest="/reports/latest.log"
export_log="$export_dir/direct-runtime-probe-$timestamp.log"
export_latest="$export_dir/latest.log"
summary="/reports/latest.json"
export_summary="$export_dir/latest.json"

printf 'Skydnir direct runtime probe starting\n'
printf 'workspace: /workspace\n'
printf 'reports: /reports\n'
printf 'documents export: %s\n' "$export_dir"
printf 'shared: %s\n' "${PDOCKER_SHARED_DOCUMENTS_MOUNT:-/shared}"

set +e
pdocker-container-probe > "$log" 2>&1
rc=$?
set -e

cp "$log" "$latest"
cp "$log" "$export_log" 2>/dev/null || true
cp "$log" "$export_latest" 2>/dev/null || true

if [ "$rc" -eq 0 ]; then
  status=pass
else
  status=fail
fi
cat > "$summary" <<EOF
{
  "schema": "pdocker.direct-runtime-probe.v1",
  "status": "$status",
  "exit_code": $rc,
  "timestamp": "$timestamp",
  "log": "$log",
  "documents_log": "$export_log"
}
EOF
cp "$summary" "$export_summary" 2>/dev/null || true

cat "$log"
printf 'Skydnir direct runtime probe %s rc=%s\n' "$status" "$rc"

if [ "$rc" -ne 0 ]; then
  exit "$rc"
fi

while :; do
  sleep 3600
done
