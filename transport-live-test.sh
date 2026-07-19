#!/usr/bin/env bash
set -euo pipefail

RUNNER_IMAGE="${CPK_TRANSPORT_RUNNER_IMAGE:-control-plane-kit-live-test:transport}"
FIXTURE_IMAGE="${CPK_TRANSPORT_FIXTURE_IMAGE:-control-plane-kit-transport-fixture:local}"
RUNNER_NAME="${CPK_TRANSPORT_RUNNER_NAME:-cpk-transport-live-runner}"
PROJECT="cpk-live-transport"
NETWORK_NAME="${PROJECT}-network"
SERVICE_NAME="${PROJECT}-docker-dns"
TEST_LABEL="io.control-plane-kit.test"
TEST_ID="transport-live"

remove_runner() {
  if ! docker container inspect "$RUNNER_NAME" >/dev/null 2>&1; then
    return
  fi
  if test "$(docker inspect -f "{{ index .Config.Labels \"$TEST_LABEL\" }}" "$RUNNER_NAME")" != "$TEST_ID"; then
    echo "Refusing to remove unowned transport-test runner: $RUNNER_NAME" >&2
    return 1
  fi
  docker rm -f "$RUNNER_NAME" >/dev/null
}

run_adapter() {
  remove_runner
  docker run --rm \
    --name "$RUNNER_NAME" \
    --label "$TEST_LABEL=$TEST_ID" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    "$RUNNER_IMAGE" \
    python tests/live_docker_transport.py "$1"
}

cleanup() {
  run_adapter cleanup >/dev/null 2>&1 || true
  remove_runner || true
}

trap cleanup EXIT

docker build --target live-test -t "$RUNNER_IMAGE" .
docker build -t "$FIXTURE_IMAGE" -f tests/fixtures/Dockerfile.transport tests/fixtures

run_adapter start

tcp_response="$(
  docker run --rm --network "$NETWORK_NAME" "$FIXTURE_IMAGE" \
    python dual_transport_server.py probe tcp "$SERVICE_NAME"
)"
udp_response="$(
  docker run --rm --network "$NETWORK_NAME" "$FIXTURE_IMAGE" \
    python dual_transport_server.py probe udp "$SERVICE_NAME"
)"

test "$tcp_response" = "tcp-ok"
test "$udp_response" = "udp-ok"

run_adapter cleanup

if docker container inspect "$SERVICE_NAME" >/dev/null 2>&1; then
  echo "Owned transport fixture container remains after cleanup" >&2
  exit 1
fi
if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  echo "Owned transport fixture network remains after cleanup" >&2
  exit 1
fi

cleanup
trap - EXIT
echo "Live Docker transport passed: TCP and UDP on private ${SERVICE_NAME}:5353"
