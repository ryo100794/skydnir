#!/usr/bin/env bash
#
# verify_all.sh — End-to-end regression test for docker-proot-setup.
#
# Each step exits the script on failure with a clear message. Pass:
#   ./scripts/verify_all.sh                 # uses /tmp/pdocker-verify as PDOCKER_HOME
#   ./scripts/verify_all.sh --quick          # fast smoke suite only
#   PDOCKER_HOME=/foo ./scripts/verify_all.sh --full
#
# Steps map 1:1 to the current Engine/API regression scenarios.
#
set -euo pipefail

SETUP_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$SETUP_ROOT/bin:$SETUP_ROOT/docker-bin:$PATH"
export PDOCKER_HOME="${PDOCKER_HOME:-/tmp/pdocker-verify}"
SOCK="${PDOCKERD_SOCK:-/tmp/pdockerd-verify.sock}"
DOCKER="$SETUP_ROOT/docker-bin/docker"

VERIFY_PROFILE="full"
case "${1:-}" in
    --quick|-q)
        VERIFY_PROFILE="quick"
        ;;
    --full|-f|"")
        VERIFY_PROFILE="full"
        ;;
    --help|-h)
        echo "Usage: $(basename "$0") [--quick|--full]"
        echo "  --quick    run fast smoke suite only (no long overlay/compose regression)"
        echo "  --full     run full verification suite (default)"
        exit 0
        ;;
    *)
        echo "Unknown option: $1" >&2
        echo "Usage: $(basename "$0") [--quick|--full]" >&2
        exit 1
        ;;
esac

is_full() { [[ "$VERIFY_PROFILE" == "full" ]]; }

PASS=0
FAIL=0

step() { printf '\n\033[1;36m=== %s ===\033[0m\n' "$*"; }
ok()   { printf '  \033[1;32mok\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
fail() { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
die()  { printf '\n\033[1;31mABORT:\033[0m %s\n' "$*"; cleanup; exit 1; }
docker_build() {
    DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 "$DOCKER" build "$@"
}

PDOCKERD_PID=
cleanup() {
    if [[ -n "$PDOCKERD_PID" ]] && kill -0 "$PDOCKERD_PID" 2>/dev/null; then
        kill "$PDOCKERD_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

# ---------------- 1. environment prechecks ----------------
step "1. environment prerequisites"
for tool in crane gcc python3 tar; do
    command -v "$tool" >/dev/null 2>&1 \
        && ok "$tool present" \
        || fail "$tool missing — required"
done
[[ -x "$SETUP_ROOT/docker-bin/docker" ]] && ok "docker CLI present" || die "docker CLI missing"
[[ -x "$SETUP_ROOT/docker-bin/crane" ]]  && ok "crane present"      || die "crane missing"

# crane must be statically linked Go (no libc dep) — Android-ready as-is
if file "$SETUP_ROOT/docker-bin/crane" 2>/dev/null | grep -q "statically linked"; then
    ok "crane is statically linked (Android-ready, no NDK rebuild needed)"
else
    fail "crane is dynamically linked — APK target requires static binary"
fi

[[ ! -e "$SETUP_ROOT/docker-bin/proot" ]] && ok "bundled proot absent" || fail "bundled proot must not be committed"
[[ ! -e "$SETUP_ROOT/docker-bin/proot-runtime" ]] && ok "legacy proot-runtime absent" || fail "legacy proot-runtime must not be committed"

if selector_out=$(PDOCKER_RUNTIME_BACKEND=no-proot python3 - "$SETUP_ROOT/bin/pdockerd" <<'PY' 2>&1
import importlib.machinery, importlib.util, sys
import os, tempfile
loader = importlib.machinery.SourceFileLoader("pdockerd", sys.argv[1])
spec = importlib.util.spec_from_loader("pdockerd", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
assert mod.runtime_backend_kind() == "direct"
assert mod.runtime_driver_name() == "pdocker-direct"
assert mod.runtime_backend_unavailable_message() == ""
rootfs = tempfile.mkdtemp(prefix="pdocker-direct-rootfs-")
os.makedirs(os.path.join(rootfs, "bin"), exist_ok=True)
open(os.path.join(rootfs, "bin", "sh"), "w").write("#!/bin/sh\n")
argv = mod.build_run_argv(rootfs, ["/bin/sh", "-c", "true"], {}, "/")
assert "-c" in argv
assert "pdocker-direct" in " ".join(argv)
print(mod.runtime_driver_name())
PY
); then
    ok "runtime backend selector: $selector_out"
else
    fail "runtime backend selector failed: $selector_out"
fi

# ---------------- 2. libcow CoW unit test ----------------
step "2. libcow.so build + unit test"
( cd "$SETUP_ROOT/src/overlay" && make -s clean && make -s ) \
    && ok "libcow.so build" \
    || fail "libcow.so build"
cp -f "$SETUP_ROOT/src/overlay/libcow.so" "$SETUP_ROOT/lib/libcow.so"
nm -D "$SETUP_ROOT/lib/libcow.so" 2>/dev/null \
    | grep -E '__memcpy_chk|__fprintf_chk' >/dev/null \
    && fail "libcow.so still references _chk symbols (musl will fail to load)" \
    || ok "no fortify _chk symbols (musl-friendly)"
( cd "$SETUP_ROOT/src/overlay" && bash test_cow.sh ) \
    | tail -1 | grep -q "ALL TESTS PASSED" \
    && ok "test_cow.sh ALL TESTS PASSED" \
    || fail "test_cow.sh did not pass"

# musl variant — pre-built artifact only (rebuild requires running pdockerd
# + alpine image, exercised separately with `make -C src/overlay musl`)
if [[ -f "$SETUP_ROOT/lib/libcow-musl.so" ]]; then
    ok "libcow-musl.so present"
    nm -D "$SETUP_ROOT/lib/libcow-musl.so" 2>/dev/null \
        | awk '$2=="T"{print $3}' | grep -qx open \
        && ok "libcow-musl.so exports 'open' hook" \
        || fail "libcow-musl.so missing 'open' export"
    nm -D "$SETUP_ROOT/lib/libcow-musl.so" 2>/dev/null \
        | awk '$2=="T"{print $3}' | grep -qx open64 \
        && fail "libcow-musl.so unexpectedly exports open64 (musl has none)" \
        || ok "libcow-musl.so correctly omits open64 (musl-pure)"
else
    printf "  \033[1;33mskip\033[0m libcow-musl.so absent (run 'make -C src/overlay musl' to build)\n"
fi

# ---------------- 3. pdocker pull ----------------
step "3. pdocker pull ubuntu:22.04"
rm -rf "$PDOCKER_HOME"
mkdir -p "$PDOCKER_HOME"
pdocker pull ubuntu:22.04 >/dev/null 2>&1 \
    && ok "pdocker pull ubuntu:22.04" \
    || die "pdocker pull failed"
[[ -x "$PDOCKER_HOME/images/docker.io_library_ubuntu_22.04/rootfs/usr/bin/bash" ]] \
    && ok "image bash present" || die "image bash missing"
[[ -L "$PDOCKER_HOME/images/docker.io_library_ubuntu_22.04/rootfs/usr/lib/aarch64-linux-gnu/libtinfo.so.6" ]] \
    && ok "image symlinks preserved (libtinfo.so.6)" \
    || fail "libtinfo.so.6 symlink lost — broken pull"

# ---------------- 4. pdocker run hello ----------------
step "4. pdocker run --rm ubuntu:22.04 echo hi"
out=$(pdocker run --rm ubuntu:22.04 /bin/bash -c 'echo hi-from-pdocker' 2>&1 | tail -1 | tr -d '\r')
[[ "$out" == "hi-from-pdocker" ]] \
    && ok "container output matches" \
    || fail "got: $out"

# ---------------- 5. write isolation (CoW) ----------------
step "5. write isolation: container write must not leak to image or host"
img_md5_before=$(md5sum "$PDOCKER_HOME/images/docker.io_library_ubuntu_22.04/rootfs/etc/hosts" | awk '{print $1}')
host_marker="verify-marker-$$"
pdocker run --rm ubuntu:22.04 /bin/bash -c "echo $host_marker >> /etc/hosts" >/dev/null 2>&1 || true
img_md5_after=$(md5sum "$PDOCKER_HOME/images/docker.io_library_ubuntu_22.04/rootfs/etc/hosts" | awk '{print $1}')
[[ "$img_md5_before" == "$img_md5_after" ]] \
    && ok "image /etc/hosts unchanged" \
    || fail "image /etc/hosts changed (CoW broken)"
grep -q "$host_marker" /etc/hosts \
    && fail "marker leaked to host /etc/hosts" \
    || ok "host /etc/hosts unaffected"

# ---------------- 6. (Dockerfile build is exercised in step 7 via pdockerd) ----------------
# The legacy `pdocker-build` script (udocker-based) is no longer the
# canonical builder — pdockerd's `/build` endpoint, driven by the
# original `docker build` CLI in step 7, is the supported path.
step "6. (Dockerfile build merged into step 7)"
ok "skipped — covered by 'docker build' via pdockerd"

# ---------------- 7. pdockerd via docker CLI ----------------
step "7. pdockerd Docker Engine API end-to-end"
rm -f "$SOCK"
PDOCKER_HOME="$PDOCKER_HOME" python3 "$SETUP_ROOT/bin/pdockerd" --socket "$SOCK" \
    > /tmp/pdockerd-verify.log 2>&1 &
PDOCKERD_PID=$!
for _ in $(seq 1 100); do
    [[ -S "$SOCK" ]] && break
    if ! kill -0 "$PDOCKERD_PID" 2>/dev/null; then
        break
    fi
    sleep 0.1
done
if [[ -S "$SOCK" ]]; then
    ok "pdockerd socket up"
else
    tail -n 40 /tmp/pdockerd-verify.log || true
    die "pdockerd never started"
fi

export DOCKER_HOST="unix://$SOCK"
$DOCKER version >/dev/null 2>&1 && ok "docker version OK" || fail "docker version failed"
$DOCKER info >/dev/null 2>&1   && ok "docker info OK"    || fail "docker info failed"

out=$($DOCKER run --rm ubuntu:22.04 /bin/bash -c 'echo via-pdockerd' 2>&1 | tail -1 | tr -d '\r')
[[ "$out" == "via-pdockerd" ]] \
    && ok "docker run via pdockerd" \
    || fail "docker run got: $out"

if events_out=$(python3 - "$SOCK" <<'PY' 2>&1
import json, socket, sys, time
sock_path = sys.argv[1]
req = (
    f"GET /events?since=0&until={int(time.time())} HTTP/1.0\r\n"
    "Host: pdocker\r\n\r\n"
).encode()
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(10)
s.connect(sock_path)
s.sendall(req)
chunks = []
while True:
    try:
        b = s.recv(65536)
    except socket.timeout:
        break
    if not b:
        break
    chunks.append(b)
s.close()
body = b"".join(chunks).split(b"\r\n\r\n", 1)[-1]
events = [json.loads(line) for line in body.splitlines() if line.strip()]
actions = {(e.get("Type"), e.get("Action")) for e in events}
need = {
    ("container", "create"),
    ("container", "start"),
    ("container", "die"),
    ("container", "destroy"),
}
missing = need - actions
assert not missing, f"missing={sorted(missing)} actions={sorted(actions)}"
assert all("timeNano" in e and "Actor" in e for e in events), events
print(",".join(sorted(a for t, a in actions if t == "container")))
PY
); then
    ok "docker events stream records lifecycle: $events_out"
else
    fail "docker events stream failed: $events_out"
fi

# docker exec on a running container
CID=$($DOCKER create ubuntu:22.04 /bin/bash -c 'sleep 30')
$DOCKER start "$CID" >/dev/null
sleep 1
out=$($DOCKER exec "$CID" /bin/bash -c 'echo exec-test' 2>&1 | tail -1 | tr -d '\r')
[[ "$out" == "exec-test" ]] && ok "docker exec output OK" || fail "exec got: $out"
out=$(printf 'echo stdin-exec\nexit\n' | $DOCKER exec -i "$CID" /bin/bash 2>&1 | tail -1 | tr -d '\r')
[[ "$out" == "stdin-exec" ]] && ok "docker exec stdin OK" || fail "exec stdin got: $out"
$DOCKER kill "$CID" >/dev/null 2>&1 || true
$DOCKER rm -f "$CID" >/dev/null 2>&1 || true

# docker build via API
DF=$(mktemp -d)
cat > "$DF/Dockerfile" <<EOF
FROM ubuntu:22.04
RUN echo api-build > /tmp/api
CMD ["/bin/bash", "-c", "cat /tmp/api"]
EOF
( cd "$DF" && docker_build -t verify-api-img . ) >/dev/null 2>&1 \
    && ok "docker build via pdockerd" \
    || fail "docker build failed"
out=$($DOCKER run --rm verify-api-img 2>&1 | tail -1 | tr -d '\r')
[[ "$out" == "api-build" ]] && ok "built image runs OK" || fail "built image got: $out"
rm -rf "$DF"

# Alpine (musl) — uses libcow-musl.so when present, runs cleanly either way
$DOCKER pull alpine:3.18 >/dev/null 2>&1 \
    && ok "docker pull alpine:3.18" || fail "alpine pull failed"
out=$($DOCKER run --rm alpine:3.18 /bin/sh -c 'echo musl-ok' 2>&1 | tail -1 | tr -d '\r')
[[ "$out" == "musl-ok" ]] \
    && ok "alpine (musl) runs OK" \
    || fail "alpine got: $out"
# Alpine CoW isolation (only if musl shim is installed)
if [[ -f "$SETUP_ROOT/lib/libcow-musl.so" ]]; then
    ALP_HOSTS="$PDOCKER_HOME/images/docker.io_library_alpine_3.18/rootfs/etc/hosts"
    if [[ -f "$ALP_HOSTS" ]]; then
        amd5_b=$(md5sum "$ALP_HOSTS" | awk '{print $1}')
        $DOCKER run --rm alpine:3.18 /bin/sh -c 'echo musl-cow-marker >> /etc/hosts' >/dev/null 2>&1 || true
        amd5_a=$(md5sum "$ALP_HOSTS" | awk '{print $1}')
        [[ "$amd5_b" == "$amd5_a" ]] \
            && ok "alpine CoW isolation (libcow-musl.so works)" \
            || fail "alpine CoW broken — image /etc/hosts md5 changed"
    fi
fi

# ---- B3: bind mount via proot -b ----
BIND=$(mktemp -d)
echo host-marker-b3 > "$BIND/host.txt"
$DOCKER run --rm -v "$BIND:/data" ubuntu:22.04 \
    /bin/bash -c 'cat /data/host.txt && echo guest-write > /data/guest.txt' \
    >/tmp/b3-out 2>/dev/null || true
grep -q "host-marker-b3" /tmp/b3-out \
    && ok "B3: bind mount host→container readable" \
    || fail "B3: container did not see host file"
[[ -f "$BIND/guest.txt" ]] \
    && ok "B3: bind mount container→host writable" \
    || fail "B3: container write did not appear on host"
rm -rf "$BIND" /tmp/b3-out

# ---- B4: docker cp (both directions) ----
CID=$($DOCKER create ubuntu:22.04 /bin/bash -c 'sleep 60')
$DOCKER start "$CID" >/dev/null
echo cp-host-content > /tmp/cp-host.txt
$DOCKER cp /tmp/cp-host.txt "$CID:/tmp/cp-host.txt" 2>/dev/null \
    && ok "B4: docker cp host→container" \
    || fail "B4: cp host→container failed"
out=$($DOCKER exec "$CID" cat /tmp/cp-host.txt 2>/dev/null | tr -d '\r')
[[ "$out" == "cp-host-content" ]] \
    && ok "B4: copied file readable inside container" \
    || fail "B4: container can't read copied file"
$DOCKER cp "$CID:/etc/hostname" /tmp/cp-from-cnt 2>/dev/null \
    && [[ -s /tmp/cp-from-cnt ]] \
    && ok "B4: docker cp container→host" \
    || fail "B4: cp container→host failed"
$DOCKER kill "$CID" >/dev/null 2>&1 || true
$DOCKER rm -f "$CID" >/dev/null 2>&1 || true
rm -f /tmp/cp-host.txt /tmp/cp-from-cnt

# ---- B5: docker stats (no-stream, proc-walking) ----
CID=$($DOCKER create ubuntu:22.04 /bin/bash -c 'while :; do :; done')
$DOCKER start "$CID" >/dev/null
sleep 1
stats_out=$($DOCKER stats --no-stream "$CID" 2>/dev/null | tail -1)
echo "$stats_out" | grep -qE '[0-9]+\.[0-9]+%' \
    && ok "B5: docker stats CPU% present" \
    || fail "B5: stats output: $stats_out"
echo "$stats_out" | grep -qiE 'MiB|KiB|GiB|B / ' \
    && ok "B5: docker stats memory column present" \
    || fail "B5: memory column missing in: $stats_out"
$DOCKER kill "$CID" >/dev/null 2>&1 || true
$DOCKER rm -f "$CID" >/dev/null 2>&1 || true

# ---- B2: network DNS alias injection ----
$DOCKER network create verify-net >/dev/null 2>&1 \
    && ok "B2: docker network create" \
    || fail "B2: network create failed"
WCID=$($DOCKER create --network verify-net --name verify-webby \
    ubuntu:22.04 /bin/bash -c 'sleep 30')
CCID=$($DOCKER create --network verify-net --name verify-client \
    ubuntu:22.04 /bin/bash -c 'sleep 30')
$DOCKER start "$WCID" >/dev/null
$DOCKER start "$CCID" >/dev/null
sleep 1
out=$($DOCKER exec "$CCID" getent hosts verify-webby 2>/dev/null | awk '{print $1}')
[[ "$out" == "127.0.0.1" ]] \
    && ok "B2: network alias resolves (verify-webby → 127.0.0.1)" \
    || fail "B2: alias resolution: '$out'"
$DOCKER kill "$WCID" "$CCID" >/dev/null 2>&1 || true
$DOCKER rm -f "$WCID" "$CCID" >/dev/null 2>&1 || true
$DOCKER network rm verify-net >/dev/null 2>&1 || true

if is_full; then

# ---- B1: build pseudo-layers / docker history ----
DF=$(mktemp -d)
cat > "$DF/Dockerfile" <<EOF
FROM ubuntu:22.04
ENV B1=ok
WORKDIR /app
RUN echo step1 > /app/m.txt
COPY Dockerfile /app/df.copy
RUN echo step2 >> /app/m.txt
CMD ["/bin/cat", "/app/m.txt"]
EOF
( cd "$DF" && docker_build -t verify-b1 . ) >/dev/null 2>&1 || true
hist_lines=$($DOCKER history verify-b1 2>/dev/null | tail -n +2 | wc -l)
[[ "$hist_lines" -ge 7 ]] \
    && ok "B1: docker history shows >=7 layers ($hist_lines)" \
    || fail "B1: only $hist_lines history lines (expected 7+)"
$DOCKER history verify-b1 2>/dev/null | grep -q "WORKDIR /app" \
    && ok "B1: WORKDIR layer present in history" \
    || fail "B1: WORKDIR layer missing"
$DOCKER history verify-b1 2>/dev/null | grep -q "echo step2" \
    && ok "B1: RUN layer text preserved" \
    || fail "B1: RUN layer text missing"
$DOCKER rmi verify-b1 >/dev/null 2>&1 || true
rm -rf "$DF"

# ---- B1b: build context honors .dockerignore for API clients ----
DFI=$(mktemp -d)
cat > "$DFI/Dockerfile" <<'EOF'
FROM ubuntu:22.04
WORKDIR /ctx
COPY . /ctx/
CMD ["/bin/sh", "-c", "test -f keep.txt && test -f nested/keep.cfg && test ! -e secret.txt && test ! -e node_modules/junk.txt"]
EOF
cat > "$DFI/.dockerignore" <<'EOF'
secret.txt
node_modules/
*.tmp
!nested/keep.cfg
EOF
echo keep > "$DFI/keep.txt"
echo secret > "$DFI/secret.txt"
echo ignored > "$DFI/ignored.tmp"
mkdir -p "$DFI/node_modules" "$DFI/nested"
echo junk > "$DFI/node_modules/junk.txt"
echo keep-cfg > "$DFI/nested/keep.cfg"
( cd "$DFI" && docker_build -t verify-dockerignore . ) >/dev/null 2>&1 \
    && ok "B1b: docker build with .dockerignore succeeds" \
    || fail "B1b: docker build with .dockerignore failed"
did=$($DOCKER create verify-dockerignore /bin/sh -c 'test -f /ctx/keep.txt && test -f /ctx/nested/keep.cfg && test ! -e /ctx/secret.txt && test ! -e /ctx/node_modules/junk.txt')
$DOCKER start "$did" >/dev/null
$DOCKER wait "$did" >/dev/null
code=$($DOCKER inspect "$did" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["State"]["ExitCode"])')
[[ "$code" == "0" ]] \
    && ok "B1b: .dockerignore excluded ignored files from COPY context" \
    || fail "B1b: .dockerignore COPY validation exit=$code"
$DOCKER rm -f "$did" >/dev/null 2>&1 || true
$DOCKER rmi verify-dockerignore >/dev/null 2>&1 || true
rm -rf "$DFI"

# ---- C1: docker compose up/ps/down lifecycle ----
CD=$(mktemp -d)
cat > "$CD/Dockerfile" <<'EOF'
FROM ubuntu:22.04
RUN echo verify-compose-built > /build-marker
CMD ["/bin/sh", "-c", "cat /build-marker; sleep 60"]
EOF
cat > "$CD/docker-compose.yml" <<'EOF'
services:
  builtsvc:
    build: .
    container_name: verify-compose-built
  pulledsvc:
    image: alpine:3.18
    container_name: verify-compose-pulled
    command: ["/bin/sh", "-c", "echo from-pulled-svc; sleep 60"]
EOF
(
    cd "$CD"
    DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 \
        $DOCKER compose up -d >/dev/null 2>&1
) && ok "C1: compose up (build + pull) succeeds" \
   || fail "C1: compose up failed"
sleep 2
up_count=$( (cd "$CD" && $DOCKER compose ps 2>/dev/null) \
    | tail -n +2 | grep -c " Up" || true)
[[ "$up_count" -eq 2 ]] \
    && ok "C1: compose ps shows 2 Up containers" \
    || fail "C1: compose ps up_count=$up_count (expected 2)"
( cd "$CD" && $DOCKER compose down >/dev/null 2>&1 ) \
    && ok "C1: compose down succeeds (network removed)" \
    || fail "C1: compose down failed"
leftover=$($DOCKER network ls 2>/dev/null | grep -c verify-compose || true)
[[ "$leftover" -eq 0 ]] \
    && ok "C1: compose down removed the network cleanly" \
    || fail "C1: $leftover compose networks left after down"
rm -rf "$CD"

# ---------------- D. overlayfs-compat layer regression ----------------
step "D. overlayfs-compat regression (Phase 1-6 layer semantics)"

IMGDIR_AL=$(ls -d "$PDOCKER_HOME/images"/*alpine_3.18* 2>/dev/null | head -1)

# D1: layer-aware pull (pdockerd経由) — manifest/config の diff_ids 整合 + layer tree 実在
# Note: bash `pdocker pull` (step 3) is legacy flat-rootfs path without manifest.
# D1 targets alpine which was pulled via pdockerd in step 7 (OCI-compliant).
if [[ -f "$IMGDIR_AL/manifest.json" && -f "$IMGDIR_AL/config.json" ]]; then
    ok "D1: alpine manifest.json + config.json present"
else
    fail "D1: alpine manifest or config missing ($IMGDIR_AL)"
fi

if d1_out=$(python3 - "$IMGDIR_AL" "$PDOCKER_HOME/layers" <<'PY' 2>&1
import json, os, sys
img, layers = sys.argv[1], sys.argv[2]
with open(os.path.join(img, "manifest.json")) as f: m = json.load(f)
with open(os.path.join(img, "config.json")) as f: c = json.load(f)
mdiffs = [l["diff_id"] for l in m.get("layers", [])]
cdiffs = c.get("rootfs", {}).get("diff_ids", [])
if mdiffs != cdiffs:
    print(f"DIFFIDS_MISMATCH manifest={len(mdiffs)} config={len(cdiffs)}")
    sys.exit(1)
missing = [d.split(":",1)[-1][:12] for d in mdiffs
           if not os.path.isdir(os.path.join(layers, d.split(":",1)[-1], "tree"))]
if missing:
    print("MISSING_LAYERS " + ",".join(missing))
    sys.exit(1)
print(f"{len(mdiffs)} layers")
PY
); then
    ok "D1: manifest diff_ids == config diff_ids, all layer trees present ($d1_out)"
else
    fail "D1: $d1_out"
fi

# D2: container creation = layer merge + hardlink inode sharing
CID_D2=$($DOCKER create alpine:3.18 /bin/sh -c "true" 2>/dev/null)
ROOTFS_D2="$PDOCKER_HOME/containers/$CID_D2/rootfs"
[[ -f "$ROOTFS_D2/etc/alpine-release" ]] \
    && ok "D2: merged rootfs has /etc/alpine-release" \
    || fail "D2: /etc/alpine-release missing from merged rootfs"

if [[ -f "$IMGDIR_AL/manifest.json" ]]; then
    ALPINE_LAYER=$(
python3 - "$IMGDIR_AL" <<'PY' 2>/dev/null
import json
import sys

with open(sys.argv[1]) as f:
    m = json.load(f)
print(m["layers"][-1]["diff_id"].split(":", 1)[-1])
PY
)
    LAYER_FILE="$PDOCKER_HOME/layers/$ALPINE_LAYER/tree/etc/alpine-release"
    if [[ -f "$LAYER_FILE" && -f "$ROOTFS_D2/etc/alpine-release" ]]; then
        INO_L=$(stat -c %i "$LAYER_FILE")
        INO_R=$(stat -c %i "$ROOTFS_D2/etc/alpine-release")
        [[ "$INO_L" == "$INO_R" ]] \
            && ok "D2: rootfs file hardlink-shares inode with layer tree" \
            || fail "D2: inode mismatch ($INO_L vs $INO_R)"
    fi
fi
$DOCKER rm -f "$CID_D2" >/dev/null 2>&1 || true

# D3: Dockerfile build — per-instruction layer + whiteout emission + isolation
DF_D3=$(mktemp -d)
cat > "$DF_D3/Dockerfile" <<'EOF'
FROM alpine:3.18
RUN echo layer-a > /a.txt
RUN rm /a.txt && echo layer-b > /b.txt
RUN echo layer-c > /c.txt
EOF
( cd "$DF_D3" && docker_build -t verify-d3 . ) >/dev/null 2>&1 \
    && ok "D3: build verify-d3 succeeded" \
    || fail "D3: build failed"

IMGDIR_D3=$(ls -d "$PDOCKER_HOME/images"/*verify-d3* 2>/dev/null | head -1)
if [[ -n "$IMGDIR_D3" && -n "$IMGDIR_AL" ]]; then
    if d3_out=$(python3 - "$IMGDIR_D3" "$IMGDIR_AL" "$PDOCKER_HOME/layers" <<'PY' 2>&1
import json, os, sys
new_img, base_img, layers = sys.argv[1], sys.argv[2], sys.argv[3]
nd = json.load(open(os.path.join(new_img, "manifest.json")))
bd = json.load(open(os.path.join(base_img, "manifest.json")))
new = [l["diff_id"] for l in nd["layers"]]
base = [l["diff_id"] for l in bd["layers"]]
added = [l for l in new if l not in base]
if len(added) != 3:
    print(f"LAYER_COUNT want=3 got={len(added)}")
    sys.exit(1)
wh_layer = added[1].split(":",1)[-1]
tree = os.path.join(layers, wh_layer, "tree")
found_wh = any(f.startswith(".wh.")
               for r,_,fs in os.walk(tree) for f in fs)
if not found_wh:
    print(f"NO_WHITEOUT in layer {wh_layer[:12]}")
    sys.exit(1)
c_layer = added[2].split(":",1)[-1]
c_tree = os.path.join(layers, c_layer, "tree")
has_c = os.path.exists(os.path.join(c_tree, "c.txt"))
has_a = os.path.exists(os.path.join(c_tree, "a.txt"))
has_b = os.path.exists(os.path.join(c_tree, "b.txt"))
if not has_c or has_a or has_b:
    print(f"ISOLATION_BROKEN c={has_c} a={has_a} b={has_b}")
    sys.exit(1)
print(f"+{len(added)} layers, whiteout in #2, isolation OK")
PY
    ); then
        ok "D3: $d3_out"
    else
        fail "D3: $d3_out"
    fi
else
    fail "D3: verify-d3 image dir not found"
fi

# D4: docker save — OCI + Docker v1.2 tar layout
SAVE_D4=$(mktemp)
$DOCKER save alpine:3.18 -o "$SAVE_D4" >/dev/null 2>&1 \
    && ok "D4: docker save alpine produced tar" \
    || fail "D4: save failed"
if d4_out=$(python3 - "$SAVE_D4" <<'PY' 2>&1
import sys, tarfile
want = {"oci-layout", "index.json", "manifest.json"}
with tarfile.open(sys.argv[1]) as t: names = set(t.getnames())
missing = want - names
has_blobs = any(n.startswith("blobs/sha256/") for n in names)
if missing or not has_blobs:
    print(f"missing={missing} blobs={has_blobs}")
    sys.exit(1)
print(f"{len(names)} entries, oci-layout+index+manifest+blobs OK")
PY
); then
    ok "D4: $d4_out"
else
    fail "D4: $d4_out"
fi
rm -f "$SAVE_D4"

# D5: _hardlink_clone_tree upper-on-lower 7 transitions
if python3 - "$SETUP_ROOT/bin/pdockerd" <<'PY' >/dev/null 2>&1
import importlib.machinery, importlib.util, os, shutil, sys, tempfile
loader = importlib.machinery.SourceFileLoader('pdockerd', sys.argv[1])
spec = importlib.util.spec_from_loader('pdockerd', loader)
m = importlib.util.module_from_spec(spec); loader.exec_module(m)
work = tempfile.mkdtemp(prefix='d5_')
lower, upper, merged = (os.path.join(work, x) for x in ['l','u','r'])
for p in [lower, upper, merged]: os.makedirs(p, exist_ok=True)
os.makedirs(f'{lower}/a'); os.makedirs(f'{lower}/b'); os.makedirs(f'{lower}/c')
open(f'{lower}/f1','w').write('L'); os.symlink('/x', f'{lower}/s1')
open(f'{lower}/a/keep','w').write('K')
open(f'{lower}/b/tofile','w').write('L')
os.symlink('/y', f'{lower}/c/tofile')
open(f'{lower}/f2','w').write('L')
os.makedirs(f'{upper}/a'); os.makedirs(f'{upper}/b/tofile'); os.makedirs(f'{upper}/c')
open(f'{upper}/f1','w').write('U')
os.symlink('/z', f'{upper}/s1')
open(f'{upper}/b/tofile/inside','w').write('U')
open(f'{upper}/c/tofile','w').write('U')
os.symlink('/w', f'{upper}/f2')
m._hardlink_clone_tree(lower, merged)
m._hardlink_clone_tree(upper, merged)
tests = [
    open(f'{merged}/f1').read() == 'U',
    os.readlink(f'{merged}/s1') == '/z',
    open(f'{merged}/a/keep').read() == 'K',
    os.path.isdir(f'{merged}/b/tofile'),
    open(f'{merged}/b/tofile/inside').read() == 'U',
    os.path.isfile(f'{merged}/c/tofile') and not os.path.islink(f'{merged}/c/tofile'),
    os.path.islink(f'{merged}/f2') and os.readlink(f'{merged}/f2') == '/w',
]
shutil.rmtree(work)
sys.exit(0 if all(tests) else 1)
PY
then
    ok "D5: _hardlink_clone_tree upper-on-lower 7/7 transitions"
else
    fail "D5: upper-on-lower transition broken"
fi

# D6: layer dedup — derived image shares all base diff_ids
DF_D6=$(mktemp -d)
cat > "$DF_D6/Dockerfile" <<'EOF'
FROM alpine:3.18
RUN echo unique-d6 > /d6-marker
EOF
( cd "$DF_D6" && docker_build -t verify-d6 . ) >/dev/null 2>&1
IMGDIR_D6=$(ls -d "$PDOCKER_HOME/images"/*verify-d6* 2>/dev/null | head -1)
if [[ -n "$IMGDIR_D6" && -n "$IMGDIR_AL" ]]; then
    if d6_out=$(python3 - "$IMGDIR_AL" "$IMGDIR_D6" <<'PY' 2>&1
import json, os, sys
a = json.load(open(os.path.join(sys.argv[1], "manifest.json")))
b = json.load(open(os.path.join(sys.argv[2], "manifest.json")))
al = [l["diff_id"] for l in a["layers"]]
bl = [l["diff_id"] for l in b["layers"]]
shared = [x for x in al if x in bl]
if len(shared) != len(al):
    print(f"DEDUP_BROKEN al={len(al)} shared={len(shared)}")
    sys.exit(1)
print(f"base {len(al)} layer(s) shared, +{len(bl)-len(al)} new")
PY
    ); then
        ok "D6: $d6_out"
    else
        fail "D6: $d6_out"
    fi
fi
$DOCKER rmi verify-d3 verify-d6 >/dev/null 2>&1 || true
rm -rf "$DF_D3" "$DF_D6"

# D8: /build persists LABEL / USER / EXPOSE / VOLUME into image config
#     (previously these instructions only appeared in layer history).
DF_D8=$(mktemp -d)
cat > "$DF_D8/Dockerfile" <<'EOF'
FROM alpine:3.18
LABEL role=d8-test maintainer="x@y"
USER 1000:1000
EXPOSE 8080 9090/udp
VOLUME ["/data", "/cache"]
EOF
( cd "$DF_D8" && docker_build -t verify-d8 . ) >/tmp/d8-build.log 2>&1 || true
$DOCKER image inspect verify-d8 >/tmp/d8-inspect.json 2>/dev/null || true
if [[ ! -s /tmp/d8-inspect.json ]] || head -c2 /tmp/d8-inspect.json | grep -q '^\[\]'; then
    fail "D8: build produced no image (build log: $(tail -5 /tmp/d8-build.log | tr '\n' ' '))"
elif d8_out=$(python3 - <<'PY' 2>&1
import json
info = json.load(open("/tmp/d8-inspect.json"))[0]
c = info.get("Config", {})
errs = []
if c.get("User") != "1000:1000":
    errs.append(f"User={c.get('User')!r}")
if (c.get("Labels") or {}).get("role") != "d8-test":
    errs.append(f"Labels.role={(c.get('Labels') or {}).get('role')!r}")
eports = c.get("ExposedPorts") or {}
if "8080/tcp" not in eports or "9090/udp" not in eports:
    errs.append(f"ExposedPorts={eports}")
vols = c.get("Volumes") or {}
if "/data" not in vols or "/cache" not in vols:
    errs.append(f"Volumes={vols}")
if errs:
    print("BROKEN: " + ", ".join(errs))
    import sys; sys.exit(1)
print(f"User+Labels+ExposedPorts+Volumes persisted ({len(eports)} ports, {len(vols)} vols)")
PY
); then
    ok "D8: $d8_out"
else
    fail "D8: $d8_out"
fi
rm -f /tmp/d8-inspect.json /tmp/d8-build.log
$DOCKER rmi verify-d8 >/dev/null 2>&1 || true
rm -rf "$DF_D8"

# D7: unlink/rename isolation — container rm + recreate of /etc/hosts
#     must not mutate the image layer, and a second container must still
#     see the pristine image /etc/hosts (no cross-container leak).
ALP_HOSTS_D7="$PDOCKER_HOME/images/docker.io_library_alpine_3.18/rootfs/etc/hosts"
if [[ -f "$ALP_HOSTS_D7" ]]; then
    md5_b=$(md5sum "$ALP_HOSTS_D7" | awk '{print $1}')
    $DOCKER run --rm alpine:3.18 /bin/sh -c \
        'rm /etc/hosts && echo D7-MARKER > /etc/hosts' >/dev/null 2>&1 || true
    md5_a=$(md5sum "$ALP_HOSTS_D7" | awk '{print $1}')
    [[ "$md5_b" == "$md5_a" ]] \
        && ok "D7: unlink+recreate does not mutate image layer" \
        || fail "D7: image /etc/hosts md5 changed ($md5_b -> $md5_a)"
    second=$($DOCKER run --rm alpine:3.18 /bin/sh -c 'cat /etc/hosts' 2>&1 | head -1)
    [[ "$second" != "D7-MARKER" ]] \
        && ok "D7: second container sees pristine /etc/hosts" \
        || fail "D7: D7-MARKER leaked across containers"
fi

else
    step "fast profile: skipping heavy compose/layer/overlay/ndk sections"
    printf "  \033[1;33mskip\033[0m full-mode sections B1/B1b/C1/D1-D8/D7 are disabled in --quick\n"
fi

# ---------------- 8. Android NDK build (optional) ----------------
if is_full; then
    step "8. Android NDK cross-build (skip if NDK not set)"
    if [[ -n "${NDK:-}" ]]; then
        ( cd "$SETUP_ROOT/src/overlay" && make android-arm64 ) >/dev/null 2>&1 \
            && ok "android-arm64 libcow built" \
            || fail "android-arm64 build failed"
        ( cd "$SETUP_ROOT/src/overlay" && make android-x86_64 ) >/dev/null 2>&1 \
            && ok "android-x86_64 libcow built" \
            || fail "android-x86_64 build failed"
    else
        printf "  \033[1;33mskip\033[0m NDK env not set (set NDK=/path/to/android-ndk to enable)\n"
    fi
fi

# ---------------- 9. pdocker GPU request parsing ----------------
step "9. pdocker GPU request parsing"
if gpu_out=$(python3 - "$SETUP_ROOT/bin/pdockerd" <<'PY' 2>&1
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader("pdockerd", sys.argv[1])
spec = importlib.util.spec_from_loader("pdockerd", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
state = {
    "HostConfig": {
        "DeviceRequests": [{
            "Driver": "nvidia",
            "Count": -1,
            "Capabilities": [["gpu", "compute", "utility"]],
            "Options": {"pdocker.cuda": "compat"},
        }],
    },
    "Labels": {},
}
modes = mod._gpu_request_modes(state)
env = mod._gpu_env(state)
assert "vulkan" in modes, modes
assert "cuda-compat" in modes, modes
assert env["PDOCKER_VULKAN_PASSTHROUGH"] == "1", env
assert env["PDOCKER_CUDA_COMPAT"] == "1", env
assert env["PDOCKER_GPU_VIRTUAL_MEMORY"] == "guarded", env
assert env["PDOCKER_VULKAN_MAX_BUFFER_BYTES"] == "2147483648", env
assert env["PDOCKER_VULKAN_DISABLE_8BIT_STORAGE"] == "0", env
assert env["PDOCKER_GPU_REWRITE_DUPLICATE_DESCRIPTOR_BINDINGS"] == "1", env
assert env["PDOCKER_GPU_DISABLE_PIPELINE_OPTIMIZATION"] == "0", env
assert env["GGML_VK_SUBALLOCATION_BLOCK_SIZE"] == "268435456", env
print(",".join(sorted(modes)))
PY
); then
    ok "GPU: DeviceRequests parsed as $gpu_out"
else
    fail "GPU: request parser failed: $gpu_out"
fi

# ---------------- 10. pdocker network identity + port plan ----------------
step "10. pdocker network identity + port plan"
if net_out=$(python3 - "$SETUP_ROOT/bin/pdockerd" <<'PY' 2>&1
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader("pdockerd", sys.argv[1])
spec = importlib.util.spec_from_loader("pdockerd", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
cid = "00112233445566778899aabbccddeeff"
cfg = {
    "ExposedPorts": {"80/tcp": {}},
    "HostConfig": {
        "PortBindings": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18080"}]},
    },
}
ports, rewrite = mod._network_port_plan(cid, cfg, {"ExposedPorts": {"443/tcp": {}}})
warnings = mod._network_warnings(cfg, rewrite)
assert mod._container_virtual_ip(cid) == "10.88.0.17"
assert ports["80/tcp"][0]["HostPort"] == "18080", ports
assert "443/tcp" in ports, ports
assert any(r["ContainerPort"] == 80 and r["HostPort"] == 18080 for r in rewrite), rewrite
assert any("not active yet" in w for w in warnings), warnings
summary = mod._container_ports_summary({"NetworkSettings": {"Ports": ports}})
assert any(p["PrivatePort"] == 80 and p["PublicPort"] == 18080 for p in summary), summary
bad_ports, bad_rewrite = mod._network_port_plan(cid, {
    "HostConfig": {"PortBindings": {"8080/tcp": [{"HostIp": "", "HostPort": "bad"}]}},
}, {})
assert bad_ports["8080/tcp"][0]["HostPort"].isdigit(), bad_ports
assert bad_rewrite[0]["HostPort"] == int(bad_ports["8080/tcp"][0]["HostPort"]), bad_rewrite
print(f"{mod._container_virtual_ip(cid)} ports={sorted(ports)} rewrites={len(rewrite)} summary={len(summary)}")
PY
); then
    ok "Network: $net_out"
else
    fail "Network: port plan failed: $net_out"
fi

# ---------------- 11. pdocker event stream ----------------
step "11. pdocker event stream"
if event_out=$(python3 - "$SETUP_ROOT/bin/pdockerd" <<'PY' 2>&1
import importlib.machinery, importlib.util, os, shutil, sys, tempfile
loader = importlib.machinery.SourceFileLoader("pdockerd", sys.argv[1])
spec = importlib.util.spec_from_loader("pdockerd", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
tmp = tempfile.mkdtemp(prefix="pdevents_")
try:
    mod.EVENTS_PATH = os.path.join(tmp, "events.jsonl")
    ev = mod.record_event("container", "create", "abcdef123456", {
        "name": "web",
        "image": "docker.io/library/alpine:3.20",
        "com.example": "ok",
    })
    assert ev["Type"] == "container", ev
    assert ev["Action"] == "create", ev
    assert ev["status"] == "create", ev
    assert isinstance(ev["timeNano"], int), ev
    loaded = mod._load_events_unlocked()
    assert len(loaded) == 1 and loaded[0]["id"] == "abcdef123456", loaded
    assert mod._event_matches(ev, {
        "type": {"container"},
        "event": {"create"},
        "container": {"abcdef", "web"},
        "image": {"alpine:3.20"},
        "label": {"com.example=ok"},
    }), ev
    assert not mod._event_matches(ev, {"event": {"start"}}), ev
    assert mod._parse_event_time("2026-05-02T00:00:00Z") > 0
    print(f"{ev['Type']}:{ev['Action']} loaded={len(loaded)}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
PY
); then
    ok "Events: $event_out"
else
    fail "Events: lifecycle stream helpers failed: $event_out"
fi

# ---------------- 12. summary ----------------
step "summary"
printf "passed: \033[1;32m%d\033[0m, failed: \033[1;31m%d\033[0m\n" "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
