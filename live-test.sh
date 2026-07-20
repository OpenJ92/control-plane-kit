#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${CPK_LIVE_TEST_IMAGE_NAME:-control-plane-kit-live-test:local}"
RUNNER_NAME="${CPK_LIVE_TEST_RUNNER_NAME:-cpk-live-test-runner}"
TEST_LABEL="io.control-plane-kit.test"
TEST_ID="host-publication-live"

remove_runner() {
  if ! docker container inspect "$RUNNER_NAME" >/dev/null 2>&1; then
    return
  fi
  if test "$(docker inspect -f "{{ index .Config.Labels \"$TEST_LABEL\" }}" "$RUNNER_NAME")" != "$TEST_ID"; then
    echo "Refusing to remove unowned live-test runner: $RUNNER_NAME" >&2
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
    "$IMAGE_NAME" \
    python tests/live_docker_publication.py "$1"
}

cleanup() {
  run_adapter cleanup >/dev/null 2>&1 || true
  remove_runner || true
}

trap cleanup EXIT

docker build --target live-test -t "$IMAGE_NAME" .

port="$(run_adapter start)"

read_published_hello() {
  local attempt
  local response
  for attempt in {1..30}; do
    if response="$(
      curl --fail --silent --max-time 2 "http://127.0.0.1:${port}/" 2>/dev/null
    )"; then
      printf '%s' "$response"
      return
    fi
    sleep 0.25
  done
  curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:${port}/"
}

response="$(read_published_hello)"

test "$response" = "Hello, published world!"
run_adapter cleanup >/dev/null

cleanup
trap - EXIT
echo "Live Docker publication passed on 127.0.0.1:${port}"
