#!/usr/bin/env sh
set -eu

IMAGE_NAME="${CPK_DEMO_IMAGE_NAME:-control-plane-kit-read-demo:local}"
NETWORK_NAME="${CPK_DEMO_NETWORK_NAME:-control-plane-kit-read-demo}"
POSTGRES_CONTAINER="${CPK_DEMO_POSTGRES_CONTAINER:-cpk-read-demo-postgres}"
SERVER_CONTAINER="${CPK_DEMO_SERVER_CONTAINER:-cpk-read-demo-server}"
HOST_PORT="${CPK_DEMO_HOST_PORT:-8011}"
TOKEN="${CPK_DEMO_TOKEN:-demo-token}"

echo "Building ${IMAGE_NAME}"
docker build --target demo -t "${IMAGE_NAME}" .

if ! docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1; then
  echo "Creating network ${NETWORK_NAME}"
  docker network create "${NETWORK_NAME}" >/dev/null
fi

docker rm -f "${SERVER_CONTAINER}" >/dev/null 2>&1 || true
docker rm -f "${POSTGRES_CONTAINER}" >/dev/null 2>&1 || true

echo "Starting Postgres container ${POSTGRES_CONTAINER}"
docker run -d \
  --name "${POSTGRES_CONTAINER}" \
  --network "${NETWORK_NAME}" \
  -e POSTGRES_DB=cpk \
  -e POSTGRES_USER=cpk \
  -e POSTGRES_PASSWORD=cpk \
  --health-cmd "pg_isready -U cpk -d cpk" \
  --health-interval 1s \
  --health-timeout 5s \
  --health-retries 30 \
  postgres:16-alpine >/dev/null

echo "Waiting for Postgres health"
attempt=0
while [ "${attempt}" -lt 60 ]; do
  status="$(docker inspect -f '{{.State.Health.Status}}' "${POSTGRES_CONTAINER}")"
  if [ "${status}" = "healthy" ]; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done

if [ "$(docker inspect -f '{{.State.Health.Status}}' "${POSTGRES_CONTAINER}")" != "healthy" ]; then
  echo "Postgres did not become healthy" >&2
  docker logs "${POSTGRES_CONTAINER}" >&2
  exit 1
fi

echo "Starting read demo on http://localhost:${HOST_PORT}"
docker run -d \
  --name "${SERVER_CONTAINER}" \
  --network "${NETWORK_NAME}" \
  -p "${HOST_PORT}:8010" \
  -e CPK_DEMO_DATABASE_URL=postgresql://cpk:cpk@"${POSTGRES_CONTAINER}":5432/cpk \
  -e CPK_DEMO_TOKEN="${TOKEN}" \
  -e CPK_DEMO_RESET=true \
  "${IMAGE_NAME}" >/dev/null

echo "Demo workspace: demo-workspace"
echo "Demo token: ${TOKEN}"
echo "Try: curl -H \"Authorization: Bearer ${TOKEN}\" http://localhost:${HOST_PORT}/workspaces/demo-workspace"
