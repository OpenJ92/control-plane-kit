#!/usr/bin/env sh
set -eu

NETWORK_NAME="${CPK_DEMO_NETWORK_NAME:-control-plane-kit-read-demo}"
POSTGRES_CONTAINER="${CPK_DEMO_POSTGRES_CONTAINER:-cpk-read-demo-postgres}"
SERVER_CONTAINER="${CPK_DEMO_SERVER_CONTAINER:-cpk-read-demo-server}"

docker rm -f "${SERVER_CONTAINER}" >/dev/null 2>&1 || true
docker rm -f "${POSTGRES_CONTAINER}" >/dev/null 2>&1 || true
docker network rm "${NETWORK_NAME}" >/dev/null 2>&1 || true

echo "Read demo containers removed"
