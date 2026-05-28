#!/bin/sh
set -eu

mkdir -p /workspace /reports /documents /shared
cat > /reports/ready <<'EOF'
Skydnir test suite container is ready.

Run the suite through docker exec:

  docker exec skydnir-test-suite run-skydnir-test-suite

Reports are written to /reports and mirrored to
/documents/skydnir-exports/skydnir-test-suite when the Documents mount is
writable.
EOF

printf 'Skydnir test suite ready; run with docker exec skydnir-test-suite run-skydnir-test-suite\n'

while :; do
  sleep 3600
done
