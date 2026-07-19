#!/usr/bin/env bash
set -euo pipefail

RUNNER_IMAGE="${CPK_SECRET_RUNNER_IMAGE:-control-plane-kit-live-test:secret}"
RUNNER_NAME="${CPK_SECRET_RUNNER_NAME:-cpk-secret-live-runner}"
PROJECT="cpk-live-secret"
NETWORK_NAME="${PROJECT}-network"
SERVICE_NAME="${PROJECT}-docker-consumer"
VOLUME_NAME="${PROJECT}-docker-consumer-secret-1"
TEST_LABEL="io.control-plane-kit.test"
TEST_ID="secret-live"

remove_runner() {
  if ! docker container inspect "$RUNNER_NAME" >/dev/null 2>&1; then
    return
  fi
  if test "$(docker inspect -f "{{ index .Config.Labels \"$TEST_LABEL\" }}" "$RUNNER_NAME")" != "$TEST_ID"; then
    echo "Refusing to remove unowned secret-test runner: $RUNNER_NAME" >&2
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
    python tests/live_docker_secret.py "$1"
}

cleanup() {
  run_adapter cleanup >/dev/null 2>&1 || true
  remove_runner || true
}

trap cleanup EXIT

docker build --target live-test -t "$RUNNER_IMAGE" .

run_adapter denied
if docker container inspect "$SERVICE_NAME" >/dev/null 2>&1; then
  echo "Denied secret reference created a container" >&2
  exit 1
fi
if docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
  echo "Denied secret reference created a volume" >&2
  exit 1
fi

run_adapter start
run_adapter start

test "$(docker exec "$SERVICE_NAME" cat /tmp/secret-ready)" = "ready"
test "$(docker exec "$SERVICE_NAME" stat -c '%a' /run/secrets/fixture-token)" = "400"

docker exec "$SERVICE_NAME" python -c $'from pathlib import Path\ntry:\n    Path("/run/secrets/fixture-token").write_text("changed")\nexcept OSError:\n    raise SystemExit(0)\nraise SystemExit(1)'

run_adapter cleanup

if docker container inspect "$SERVICE_NAME" >/dev/null 2>&1; then
  echo "Owned secret fixture container remains after cleanup" >&2
  exit 1
fi
if docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
  echo "Owned secret volume remains after cleanup" >&2
  exit 1
fi
if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  echo "Owned secret fixture network remains after cleanup" >&2
  exit 1
fi

cleanup
trap - EXIT
echo "Live Docker secret passed: denied bootstrap, protected delivery, replay, and cleanup"
