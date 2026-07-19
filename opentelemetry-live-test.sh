#!/usr/bin/env bash
set -euo pipefail

RUNNER_IMAGE="${CPK_OTEL_RUNNER_IMAGE:-control-plane-kit-live-test:otel}"
RUNNER_NAME="${CPK_OTEL_RUNNER_NAME:-cpk-otel-live-runner}"
PROJECT="cpk-live-otel"
NETWORK_NAME="${PROJECT}-network"
COLLECTOR_NAME="${PROJECT}-docker-collector"
CONFIG_VOLUME="${PROJECT}-docker-collector-config-opentelemetry-collector-config"
TEST_LABEL="io.control-plane-kit.test"
TEST_ID="otel-live"

remove_runner() {
  if ! docker container inspect "$RUNNER_NAME" >/dev/null 2>&1; then
    return
  fi
  if test "$(docker inspect -f "{{ index .Config.Labels \"$TEST_LABEL\" }}" "$RUNNER_NAME")" != "$TEST_ID"; then
    echo "Refusing to remove unowned Collector test runner: $RUNNER_NAME" >&2
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
    python tests/live_opentelemetry_collector.py "$1"
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
until docker run --rm --network "$NETWORK_NAME" "$RUNNER_IMAGE" python -c \
  "import urllib.request; urllib.request.urlopen('http://${COLLECTOR_NAME}:13133/', timeout=2)"; do
  attempt=$((attempt + 1))
  if test "$attempt" -ge 30; then
    docker logs "$COLLECTOR_NAME" >&2
    echo "Collector health endpoint did not become ready" >&2
    exit 1
  fi
  sleep 1
done

docker run --rm \
  --network "$NETWORK_NAME" \
  -e CPK_OTEL_LIVE_ENDPOINT="http://${COLLECTOR_NAME}:4318" \
  "$RUNNER_IMAGE" \
  python tests/live_opentelemetry_collector.py send

attempt=0
until docker logs "$COLLECTOR_NAME" 2>&1 | grep -q "cpk-live-span"; do
  attempt=$((attempt + 1))
  if test "$attempt" -ge 30; then
    docker logs "$COLLECTOR_NAME" >&2
    echo "Collector debug exporter did not emit the accepted span" >&2
    exit 1
  fi
  sleep 1
done

run_adapter cleanup

if docker container inspect "$COLLECTOR_NAME" >/dev/null 2>&1; then
  echo "Owned Collector container remains after cleanup" >&2
  exit 1
fi
if docker volume inspect "$CONFIG_VOLUME" >/dev/null 2>&1; then
  echo "Owned Collector configuration volume remains after cleanup" >&2
  exit 1
fi
if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  echo "Owned Collector network remains after cleanup" >&2
  exit 1
fi

cleanup
trap - EXIT
echo "Live OpenTelemetry Collector passed: graph-pinned startup, health, OTLP trace, debug export, replay, and cleanup"
