#!/bin/sh
set -eu

default_image() {
  python3 - <<'PY'
import json
from pathlib import Path

image = json.loads(Path("products/cpk_server/product.docker.cpk.json").read_text())["product"]["image"]
print(f"{image['registry']}/{image['repository']}@{image['digest']}")
PY
}

IMAGE="${CPK_SERVER_IMAGE:-$(default_image)}"
CONTROLLER_IMAGE="${CPK_SERVERS_TEST_IMAGE:-control-plane-kit-servers-test:local}"
BUILD_CONTROLLER="${CPK_RECURSIVE_BUILD_CONTROLLER:-1}"
NETWORK="cpk-server-recursive-$$"
LABEL="org.openj92.project=control-plane-kit-servers"
WORKSPACE_LABEL="org.openj92.cpk.workspace=recursive-cpk-server"
POSTGRES_CONTAINER=""
PARENT_CONTAINER=""
DOCKER_SOCKET_GROUP="${CPK_DOCKER_SOCKET_GROUP:-0}"
AUTH_CONFIG_SOURCE="${CPK_DOCKER_AUTH_CONFIG:-$HOME/.docker/config.json}"
AUTH_CONFIG_DIR=""
IMAGE_PULL_RESOLVER="none"

cleanup_recursive_resources() {
  docker ps -aq --filter "label=$WORKSPACE_LABEL" \
    | while IFS= read -r container; do
        if [ -n "$container" ]; then
          docker rm -f "$container" >/dev/null 2>&1 || true
        fi
      done
  docker volume ls -q --filter "label=$WORKSPACE_LABEL" \
    | while IFS= read -r volume; do
        if [ -n "$volume" ]; then
          docker volume rm "$volume" >/dev/null 2>&1 || true
        fi
      done
  docker network ls -q --filter "label=$WORKSPACE_LABEL" \
    | while IFS= read -r network; do
        if [ -n "$network" ]; then
          docker network rm "$network" >/dev/null 2>&1 || true
        fi
      done
}

cleanup() {
  if [ -n "$PARENT_CONTAINER" ]; then
    docker rm -f "$PARENT_CONTAINER" >/dev/null 2>&1 || true
  fi
  if [ -n "$POSTGRES_CONTAINER" ]; then
    docker rm -f "$POSTGRES_CONTAINER" >/dev/null 2>&1 || true
  fi
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  cleanup_recursive_resources
  if [ -n "$AUTH_CONFIG_DIR" ]; then
    rm -rf "$AUTH_CONFIG_DIR"
  fi
}
trap cleanup EXIT INT TERM

if [ "$BUILD_CONTROLLER" = "1" ]; then
  docker build -f Dockerfile.test -t "$CONTROLLER_IMAGE" .
fi

docker pull "$IMAGE"
docker network create "$NETWORK" >/dev/null

POSTGRES_CONTAINER="$(docker run -d \
  --label "$LABEL" \
  --network "$NETWORK" \
  --network-alias cpk-postgres \
  -e POSTGRES_DB=cpk \
  -e POSTGRES_USER=cpk \
  -e POSTGRES_PASSWORD=cpk \
  postgres:16-alpine)"

POSTGRES_READY=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if docker exec "$POSTGRES_CONTAINER" psql -U cpk -d cpk -c 'SELECT 1' >/dev/null 2>&1; then
    POSTGRES_READY=1
    break
  fi
  sleep 1
done

if [ "$POSTGRES_READY" != "1" ]; then
  echo "parent postgres did not become query-ready" >&2
  exit 1
fi

if command -v gh >/dev/null 2>&1 && GHCR_TOKEN="$(gh auth token 2>/dev/null)"; then
  AUTH_CONFIG_DIR="$(mktemp -d)"
  GHCR_AUTH="$(printf 'OpenJ92:%s' "$GHCR_TOKEN" | base64 | tr -d '\n')"
  printf '{"auths":{"ghcr.io":{"auth":"%s"}}}\n' "$GHCR_AUTH" >"$AUTH_CONFIG_DIR/config.json"
  unset GHCR_TOKEN
  unset GHCR_AUTH
  chmod 0444 "$AUTH_CONFIG_DIR/config.json"
  IMAGE_PULL_RESOLVER="docker-config"
elif [ -r "$AUTH_CONFIG_SOURCE" ]; then
  AUTH_CONFIG_DIR="$(mktemp -d)"
  cp "$AUTH_CONFIG_SOURCE" "$AUTH_CONFIG_DIR/config.json"
  chmod 0444 "$AUTH_CONFIG_DIR/config.json"
  IMAGE_PULL_RESOLVER="docker-config"
fi

if [ -n "$AUTH_CONFIG_DIR" ]; then
  PARENT_CONTAINER="$(docker run -d \
    --label "$LABEL" \
    --network "$NETWORK" \
    --network-alias cpk-server \
    --group-add "$DOCKER_SOCKET_GROUP" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$AUTH_CONFIG_DIR:/tmp/cpk-docker-config:ro" \
    -e DOCKER_CONFIG=/tmp/cpk-docker-config \
    -e CPK_SERVER_MODE=execution-capable \
    -e CPK_CONTROL_AUTH_CONFIGURED=true \
    -e CPK_PORT=8080 \
    -e CPK_RUNTIME_INTERPRETERS=docker \
    -e CPK_IMAGE_PULL_CREDENTIAL_RESOLVER="$IMAGE_PULL_RESOLVER" \
    -e CPK_PRODUCT_SECRET_RESOLVER=local-development \
    -e CPK_PRODUCT_SECRET_VALUES_JSON='{"secret://control-plane-kit/postgres/password":"cpk"}' \
    -e CPK_WORKPLACE_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
    -e CPK_ACTIVITY_HISTORY_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
    -e CPK_OBSERVER_STATE_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
    -e CPK_GRAPH_TOPOLOGY_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
    "$IMAGE")"
else
  PARENT_CONTAINER="$(docker run -d \
    --label "$LABEL" \
    --network "$NETWORK" \
    --network-alias cpk-server \
    --group-add "$DOCKER_SOCKET_GROUP" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -e CPK_SERVER_MODE=execution-capable \
    -e CPK_CONTROL_AUTH_CONFIGURED=true \
    -e CPK_PORT=8080 \
    -e CPK_RUNTIME_INTERPRETERS=docker \
    -e CPK_PRODUCT_SECRET_RESOLVER=local-development \
    -e CPK_PRODUCT_SECRET_VALUES_JSON='{"secret://control-plane-kit/postgres/password":"cpk"}' \
    -e CPK_WORKPLACE_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
    -e CPK_ACTIVITY_HISTORY_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
    -e CPK_OBSERVER_STATE_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
    -e CPK_GRAPH_TOPOLOGY_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
    "$IMAGE")"
fi

if ! docker run --rm \
  --label "$LABEL" \
  --network "$NETWORK" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e CPK_RECURSIVE_BASE_URL=http://cpk-server:8080 \
  -e CPK_RECURSIVE_PARENT_CONTAINER="$PARENT_CONTAINER" \
  -e CPK_RECURSIVE_SERVERS_REPO=/app \
  -e CPK_RECURSIVE_REGISTER_PULL_AUTHORITY="$IMAGE_PULL_RESOLVER" \
  "$CONTROLLER_IMAGE" \
  python scripts/cpk_server_recursive_activity.py; then
  docker logs "$PARENT_CONTAINER" 2>&1 | tail -n 100 >&2 || true
  exit 1
fi

cleanup
POSTGRES_CONTAINER=""
PARENT_CONTAINER=""

sh scripts/docker_residue_audit.sh

echo "recursive cpk-server activity smoke passed"
