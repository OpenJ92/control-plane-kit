#!/usr/bin/env bash
set -euo pipefail

RUNNER_IMAGE="${CPK_COREDNS_RUNNER_IMAGE:-control-plane-kit-live-test:coredns}"
RUNNER_NAME="${CPK_COREDNS_RUNNER_NAME:-cpk-coredns-live-runner}"
PROJECT="cpk-live-coredns"
NETWORK_NAME="${PROJECT}-network"
SERVER_NAME="${PROJECT}-docker-coredns"
TEST_LABEL="io.control-plane-kit.test"
TEST_ID="coredns-live"

remove_runner() {
  if ! docker container inspect "$RUNNER_NAME" >/dev/null 2>&1; then return; fi
  if test "$(docker inspect -f "{{ index .Config.Labels \"$TEST_LABEL\" }}" "$RUNNER_NAME")" != "$TEST_ID"; then
    echo "Refusing to remove unowned CoreDNS runner: $RUNNER_NAME" >&2
    return 1
  fi
  docker rm -f "$RUNNER_NAME" >/dev/null
}

run_adapter() {
  remove_runner
  docker run --rm --name "$RUNNER_NAME" --label "$TEST_LABEL=$TEST_ID" \
    -v /var/run/docker.sock:/var/run/docker.sock "$RUNNER_IMAGE" \
    python tests/live_coredns.py "$1"
}

cleanup() {
  run_adapter cleanup >/dev/null 2>&1 || true
  remove_runner || true
}
trap cleanup EXIT

docker build --target live-test -t "$RUNNER_IMAGE" .
run_adapter start
run_adapter start

attempt=0
until docker run --rm --network "$NETWORK_NAME" \
  -e CPK_COREDNS_HOST="$SERVER_NAME" "$RUNNER_IMAGE" \
  python tests/live_coredns.py verify; do
  attempt=$((attempt + 1))
  if test "$attempt" -ge 30; then
    docker logs "$SERVER_NAME" >&2
    exit 1
  fi
  sleep 1
done

mounts="$(docker inspect -f '{{range .Mounts}}{{println .Destination .RW}}{{end}}' "$SERVER_NAME")"
printf '%s\n' "$mounts" | grep -Fx '/etc/coredns/Corefile false' >/dev/null
printf '%s\n' "$mounts" | grep -Fx '/etc/coredns/zones/db.cpk false' >/dev/null

run_adapter cleanup

if docker container inspect "$SERVER_NAME" >/dev/null 2>&1; then
  echo "Owned CoreDNS container remains after cleanup" >&2
  exit 1
fi
for artifact in coredns-corefile coredns-zone; do
  volume="${PROJECT}-docker-coredns-config-${artifact}"
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    echo "Owned CoreDNS configuration volume remains after cleanup: $volume" >&2
    exit 1
  fi
done
if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  echo "Owned CoreDNS network remains after cleanup" >&2
  exit 1
fi

cleanup
trap - EXIT
echo "Live CoreDNS passed: official image, read-only artifacts, DNS TCP/UDP resolution, health, readiness, replay, and cleanup"
