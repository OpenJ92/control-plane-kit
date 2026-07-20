#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${CPK_DISCOVERY_IMAGE_NAME:-control-plane-kit-live-test:discovery}"
NETWORK_NAME="${CPK_DISCOVERY_NETWORK_NAME:-cpk-discovery-live-network}"
POSTGRES_CONTAINER="${CPK_DISCOVERY_POSTGRES:-cpk-discovery-live-postgres}"
SERVER_CONTAINER="${CPK_DISCOVERY_SERVER:-cpk-discovery-live-server}"
RUNNER_CONTAINER="${CPK_DISCOVERY_RUNNER:-cpk-discovery-live-runner}"
TOKEN="${CPK_DISCOVERY_TOKEN:-cpk-discovery-live-attestation}"
TEST_LABEL="io.control-plane-kit.test"
TEST_ID="discovery-live"

remove_owned_container() {
  local name="$1"
  if ! docker container inspect "$name" >/dev/null 2>&1; then
    return
  fi
  if test "$(docker inspect -f "{{ index .Config.Labels \"$TEST_LABEL\" }}" "$name")" != "$TEST_ID"; then
    echo "Refusing to remove unowned discovery container: $name" >&2
    return 1
  fi
  docker rm -f "$name" >/dev/null
}

cleanup() {
  remove_owned_container "$RUNNER_CONTAINER" || true
  remove_owned_container "$SERVER_CONTAINER" || true
  remove_owned_container "$POSTGRES_CONTAINER" || true
  if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    if test "$(docker network inspect -f "{{ index .Labels \"$TEST_LABEL\" }}" "$NETWORK_NAME")" = "$TEST_ID"; then
      docker network rm "$NETWORK_NAME" >/dev/null
    else
      echo "Refusing to remove unowned discovery network: $NETWORK_NAME" >&2
      return 1
    fi
  fi
}

trap 'cleanup || true' EXIT
cleanup

docker build --target live-test -t "$IMAGE_NAME" .
docker network create --label "$TEST_LABEL=$TEST_ID" "$NETWORK_NAME" >/dev/null
docker run -d \
  --name "$POSTGRES_CONTAINER" \
  --label "$TEST_LABEL=$TEST_ID" \
  --network "$NETWORK_NAME" \
  -e POSTGRES_DB=cpk_discovery \
  -e POSTGRES_USER=cpk \
  -e POSTGRES_PASSWORD=cpk \
  --health-cmd "pg_isready -U cpk -d cpk_discovery" \
  --health-interval 1s \
  --health-timeout 5s \
  --health-retries 30 \
  postgres:16-alpine >/dev/null

until test "$(docker inspect -f '{{.State.Health.Status}}' "$POSTGRES_CONTAINER")" = healthy; do
  sleep 1
done

docker run -d \
  --name "$SERVER_CONTAINER" \
  --label "$TEST_LABEL=$TEST_ID" \
  --network "$NETWORK_NAME" \
  -e DISCOVERY_DATABASE_URL="postgresql://cpk:cpk@${POSTGRES_CONTAINER}:5432/cpk_discovery" \
  -e CPK_DISCOVERY_IDENTITY_TOKEN="$TOKEN" \
  --health-cmd "python -c 'import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:8080/health/ready\", timeout=2)'" \
  --health-interval 1s \
  --health-timeout 5s \
  --health-retries 30 \
  "$IMAGE_NAME" python -m control_plane_kit.discovery_server.main >/dev/null

until test "$(docker inspect -f '{{.State.Health.Status}}' "$SERVER_CONTAINER")" = healthy; do
  sleep 1
done

docker run --rm \
  --name "$RUNNER_CONTAINER" \
  --label "$TEST_LABEL=$TEST_ID" \
  --network "$NETWORK_NAME" \
  -e CPK_DISCOVERY_LIVE_URL="http://${SERVER_CONTAINER}:8080" \
  -e CPK_DISCOVERY_LIVE_TOKEN="$TOKEN" \
  "$IMAGE_NAME" python tests/live_service_discovery.py

cleanup
trap - EXIT

if docker container inspect "$SERVER_CONTAINER" >/dev/null 2>&1 \
  || docker container inspect "$POSTGRES_CONTAINER" >/dev/null 2>&1 \
  || docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
  echo "Owned discovery resources remain after cleanup" >&2
  exit 1
fi
