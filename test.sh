#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${CPK_TEST_IMAGE_NAME:-control-plane-kit-test:local}"
NETWORK_NAME="${CPK_TEST_NETWORK_NAME:-control-plane-kit-test}"
POSTGRES_CONTAINER="${CPK_TEST_POSTGRES_CONTAINER:-cpk-test-postgres}"
TEST_CONTAINER="${CPK_TEST_CONTAINER:-cpk-test-runner}"

cleanup() {
  docker rm -f "$TEST_CONTAINER" >/dev/null 2>&1 || true
  docker rm -f "$POSTGRES_CONTAINER" >/dev/null 2>&1 || true
  docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
}

trap cleanup EXIT

cleanup

./packaging-test.sh
./control-plane-kit-core/test.sh
./control-plane-kit-operations/test.sh

docker build --target test -t "$IMAGE_NAME" .

docker network create "$NETWORK_NAME" >/dev/null

docker run -d \
  --name "$POSTGRES_CONTAINER" \
  --network "$NETWORK_NAME" \
  -e POSTGRES_DB=cpk \
  -e POSTGRES_USER=cpk \
  -e POSTGRES_PASSWORD=cpk \
  --health-cmd "pg_isready -U cpk -d cpk" \
  --health-interval 1s \
  --health-timeout 5s \
  --health-retries 30 \
  postgres:16-alpine >/dev/null

attempt=0
while [ "$attempt" -lt 60 ]; do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$POSTGRES_CONTAINER")"
  if [ "$status" = "healthy" ]; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done

if [ "$(docker inspect -f '{{.State.Health.Status}}' "$POSTGRES_CONTAINER")" != "healthy" ]; then
  echo "Postgres did not become healthy" >&2
  docker logs "$POSTGRES_CONTAINER" >&2
  exit 1
fi

docker run \
  --name "$TEST_CONTAINER" \
  --network "$NETWORK_NAME" \
  -e CPK_TEST_DATABASE_URL=postgresql://cpk:cpk@"$POSTGRES_CONTAINER":5432/cpk \
  "$IMAGE_NAME"
