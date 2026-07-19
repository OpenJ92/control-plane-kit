#!/usr/bin/env bash
set -euo pipefail

RUNNER_IMAGE="${CPK_CONFIGURATION_RUNNER_IMAGE:-control-plane-kit-live-test:configuration}"
RUNNER_NAME="${CPK_CONFIGURATION_RUNNER_NAME:-cpk-configuration-live-runner}"
PROJECT="cpk-live-configuration"
NETWORK_NAME="${PROJECT}-network"
SERVICE_NAME="${PROJECT}-docker-configured"
VOLUME_NAME="${PROJECT}-docker-configured-config-service-config"
TEST_LABEL="io.control-plane-kit.test"
TEST_ID="configuration-live"
EXPECTED='{"message":"configuration-live"}'

remove_runner() {
  if ! docker container inspect "$RUNNER_NAME" >/dev/null 2>&1; then
    return
  fi
  if test "$(docker inspect -f "{{ index .Config.Labels \"$TEST_LABEL\" }}" "$RUNNER_NAME")" != "$TEST_ID"; then
    echo "Refusing to remove unowned configuration-test runner: $RUNNER_NAME" >&2
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
    python tests/live_docker_configuration.py "$1"
}

cleanup() {
  run_adapter cleanup >/dev/null 2>&1 || true
  remove_runner || true
}

trap cleanup EXIT

docker build --target live-test -t "$RUNNER_IMAGE" .

run_adapter start
run_adapter start

actual="$(docker exec "$SERVICE_NAME" cat /etc/service/config.json)"
test "$actual" = "$EXPECTED"

docker exec "$SERVICE_NAME" python -c $'from pathlib import Path\ntry:\n    Path("/etc/service/config.json").write_text("changed")\nexcept OSError:\n    raise SystemExit(0)\nraise SystemExit(1)'

run_adapter cleanup

if docker container inspect "$SERVICE_NAME" >/dev/null 2>&1; then
  echo "Owned configuration fixture container remains after cleanup" >&2
  exit 1
fi
if docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
  echo "Owned configuration artifact volume remains after cleanup" >&2
  exit 1
fi
if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  echo "Owned configuration fixture network remains after cleanup" >&2
  exit 1
fi

cleanup
trap - EXIT
echo "Live Docker configuration passed: pinned content, read-only mount, replay, and cleanup"
