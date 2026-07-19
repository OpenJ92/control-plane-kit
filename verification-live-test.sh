#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${CPK_VERIFICATION_IMAGE_NAME:-control-plane-kit-live-test:verification}"
NETWORK_NAME="${CPK_VERIFICATION_NETWORK_NAME:-cpk-verification-live-network}"
POSTGRES_CONTAINER="${CPK_VERIFICATION_POSTGRES:-cpk-verification-live-postgres}"
TARGET_CONTAINER="${CPK_VERIFICATION_TARGET:-cpk-verification-live-target}"
RUNNER_CONTAINER="${CPK_VERIFICATION_RUNNER:-cpk-verification-live-runner}"
TEST_LABEL="io.control-plane-kit.test"
TEST_ID="verification-live"

remove_owned_container() {
  local name="$1"
  if ! docker container inspect "$name" >/dev/null 2>&1; then
    return
  fi
  if test "$(docker inspect -f "{{ index .Config.Labels \"$TEST_LABEL\" }}" "$name")" != "$TEST_ID"; then
    echo "Refusing to remove unowned verification container: $name" >&2
    return 1
  fi
  docker rm -f "$name" >/dev/null
}

cleanup() {
  remove_owned_container "$RUNNER_CONTAINER" || true
  remove_owned_container "$TARGET_CONTAINER" || true
  remove_owned_container "$POSTGRES_CONTAINER" || true
  if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    if test "$(docker network inspect -f "{{ index .Labels \"$TEST_LABEL\" }}" "$NETWORK_NAME")" = "$TEST_ID"; then
      docker network rm "$NETWORK_NAME" >/dev/null
    else
      echo "Refusing to remove unowned verification network: $NETWORK_NAME" >&2
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
  -e POSTGRES_DB=cpk \
  -e POSTGRES_USER=cpk \
  -e POSTGRES_PASSWORD=cpk \
  --health-cmd "pg_isready -U cpk -d cpk" \
  --health-interval 1s \
  --health-timeout 5s \
  --health-retries 30 \
  postgres:16-alpine >/dev/null
docker run -d \
  --name "$TARGET_CONTAINER" \
  --label "$TEST_LABEL=$TEST_ID" \
  --network "$NETWORK_NAME" \
  "$IMAGE_NAME" python -m http.server 8080 >/dev/null

until test "$(docker inspect -f '{{.State.Health.Status}}' "$POSTGRES_CONTAINER")" = healthy; do
  sleep 1
done
sleep 1

docker run --rm \
  --name "$RUNNER_CONTAINER" \
  --label "$TEST_LABEL=$TEST_ID" \
  --network "$NETWORK_NAME" \
  -e CPK_VERIFICATION_DATABASE_URL="postgresql://cpk:cpk@${POSTGRES_CONTAINER}:5432/cpk" \
  "$IMAGE_NAME" python -m examples.verification_observation_live

cleanup
trap - EXIT
