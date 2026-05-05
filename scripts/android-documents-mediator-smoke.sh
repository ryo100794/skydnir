#!/usr/bin/env bash
set -euo pipefail

ADB_SERIAL="${ADB_SERIAL:-10.8.135.134:37669}"
PACKAGE="${PACKAGE:-io.github.ryo100794.pdocker.compat}"
ACTIVITY="${ACTIVITY:-io.github.ryo100794.pdocker.MainActivity}"

adb_cmd() {
  adb -s "$ADB_SERIAL" "$@"
}

setting_xml="$(adb_cmd shell "run-as $PACKAGE sh -c 'cat shared_prefs/pdocker-settings.xml'" 2>/dev/null || true)"
selected_host="$(printf '%s\n' "$setting_xml" | sed -n 's/.*name="documents.hostPath">\([^<]*\)<.*/\1/p' | head -1)"
if [ -z "$selected_host" ]; then
  echo "FAIL: documents.hostPath is not configured" >&2
  exit 1
fi

case "$selected_host" in
  /storage/*|/sdcard/*) ;;
  *)
    echo "FAIL: unsupported documents.hostPath '$selected_host'" >&2
    exit 1
    ;;
esac

case_name="mediator-smoke-$(date -u +%Y%m%dT%H%M%SZ)"
payload="mediator-payload-$case_name"
relative="pdocker-exports/$case_name/nested/latest.log"
sidecar="pdocker-exports_${case_name}_nested_latest.log.json"

adb_cmd shell am start -n "$PACKAGE/$ACTIVITY" >/dev/null
sleep 2

adb_cmd shell "run-as $PACKAGE sh -c 'mkdir -p files/pdocker/documents-saf-mediated/mirror/pdocker-exports/$case_name/nested && printf \"$payload\n\" > files/pdocker/documents-saf-mediated/mirror/$relative'"

deadline=$((SECONDS + 30))
while [ "$SECONDS" -lt "$deadline" ]; do
  saf_payload="$(adb_cmd shell "cat '$selected_host/$relative' 2>/dev/null" | tr -d '\r' || true)"
  mirror_exists="$(adb_cmd shell "run-as $PACKAGE sh -c 'test -e files/pdocker/documents-saf-mediated/mirror/$relative; echo \$?'" | tr -d '\r')"
  sidecar_text="$(adb_cmd shell "run-as $PACKAGE sh -c 'cat files/pdocker/documents-saf-mediated/sidecar/$sidecar 2>/dev/null'" | tr -d '\r' || true)"
  if [ "$saf_payload" = "$payload" ] &&
    [ "$mirror_exists" = "1" ] &&
    printf '%s' "$sidecar_text" | grep -q '"payloadState": "saf-synced-mirror-evicted"'; then
    echo "ok: SAF mediator wrote payload, evicted app mirror, and kept sidecar metadata"
    echo "saf=$selected_host/$relative"
    exit 0
  fi
  sleep 2
done

echo "FAIL: SAF mediator smoke did not converge" >&2
echo "expected_payload=$payload" >&2
echo "saf=$selected_host/$relative" >&2
echo "mirror_exit=${mirror_exists:-unknown}" >&2
echo "sidecar=${sidecar_text:-}" >&2
exit 1
