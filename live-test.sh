#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${CPK_LIVE_TEST_IMAGE_NAME:-control-plane-kit-live-test:local}"
RUNNER_NAME="${CPK_LIVE_TEST_RUNNER_NAME:-cpk-live-test-runner}"

run_adapter() {
  docker rm -f "$RUNNER_NAME" >/dev/null 2>&1 || true
  docker run --rm \
    --name "$RUNNER_NAME" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    "$IMAGE_NAME" \
    python tests/live_docker_publication.py "$1"
}

cleanup() {
  run_adapter cleanup >/dev/null 2>&1 || true
  docker rm -f "$RUNNER_NAME" >/dev/null 2>&1 || true
}

trap cleanup EXIT

docker build --target live-test -t "$IMAGE_NAME" .

port="$(run_adapter start)"
response="$(curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:${port}/")"

test "$response" = "Hello, published world!"
run_adapter cleanup >/dev/null

echo "Live Docker publication passed on 127.0.0.1:${port}"
