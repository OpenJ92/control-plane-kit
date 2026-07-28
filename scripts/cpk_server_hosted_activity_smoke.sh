#!/bin/sh
set -eu

default_image() {
  python3 - <<'PY'
import json
import os
from pathlib import Path

descriptor = os.environ.get("CPK_SERVER_DESCRIPTOR_PATH")
if descriptor is None:
    if os.environ.get("CPK_HOSTED_ACTIVITY_SCENARIO") == "public-gateway-ingress":
        descriptor = "products/cpk_server/product.docker-cloudflare.cpk.json"
    else:
        descriptor = "products/cpk_server/product.docker.cpk.json"
image = json.loads(Path(descriptor).read_text())["product"]["image"]
print(f"{image['registry']}/{image['repository']}@{image['digest']}")
PY
}

SCENARIO="${CPK_HOSTED_ACTIVITY_SCENARIO:-single-hello}"
IMAGE="${CPK_SERVER_IMAGE:-$(default_image)}"
CONTROLLER_IMAGE="${CPK_SERVERS_TEST_IMAGE:-control-plane-kit-servers-test:local}"
BUILD_CONTROLLER="${CPK_HOSTED_ACTIVITY_BUILD_CONTROLLER:-1}"
KEEP_ON_FAILURE="${CPK_HOSTED_ACTIVITY_KEEP_ON_FAILURE:-0}"
NETWORK="cpk-server-hosted-activity-$$"
LABEL="org.openj92.project=control-plane-kit-servers"
WORKSPACE_LABEL_KEY="org.openj92.cpk.workspace"
WORKSPACE_LABEL="org.openj92.cpk.workspace=cpk-hosted-activity-basic"
POSTGRES_CONTAINER=""
SERVER_CONTAINER=""
DOCKER_SOCKET_GROUP="${CPK_DOCKER_SOCKET_GROUP:-0}"
AUTH_CONFIG_SOURCE="${CPK_DOCKER_AUTH_CONFIG:-$HOME/.docker/config.json}"
AUTH_CONFIG_DIR=""
IMAGE_PULL_RESOLVER="none"
INGRESS_INTERPRETERS="none"
PRODUCT_SECRET_VALUES_JSON='{"secret://control-plane-kit/postgres/password":"cpk-postgres-smoke-password"}'

if [ "$SCENARIO" = "public-gateway-ingress" ]; then
  CLOUDFLARE_ENV_FILE="${CPK_CLOUDFLARE_ENV_FILE:-env/cloudflare.openj92.local.dev}"
  if [ -r "$CLOUDFLARE_ENV_FILE" ]; then
    set -a
    . "$CLOUDFLARE_ENV_FILE"
    set +a
  fi
  : "${OPENJ92_CLOUDFLARE_ACCOUNT_ID:?OPENJ92_CLOUDFLARE_ACCOUNT_ID is required}"
  : "${OPENJ92_CLOUDFLARE_ZONE_ID:?OPENJ92_CLOUDFLARE_ZONE_ID is required}"
  : "${OPENJ92_CLOUDFLARE_API_TOKEN:?OPENJ92_CLOUDFLARE_API_TOKEN is required}"
  : "${OPENJ92_CLOUDFLARE_ZONE:=openj92.dev}"
  INGRESS_INTERPRETERS="cloudflare"
  PRODUCT_SECRET_VALUES_JSON="$(python3 - <<'PY'
import json
import os

print(json.dumps({
    "secret://control-plane-kit/postgres/password": "cpk-postgres-smoke-password",
    "secret://cloudflare/openj92/api-token": os.environ["OPENJ92_CLOUDFLARE_API_TOKEN"],
}, separators=(",", ":"), sort_keys=True))
PY
)"
fi

cleanup_activity_resources() {
  docker ps -aq --filter "label=$WORKSPACE_LABEL_KEY" \
    | while IFS= read -r container; do
        if [ -n "$container" ]; then
          workspace="$(docker inspect -f "{{ index .Config.Labels \"$WORKSPACE_LABEL_KEY\" }}" "$container" 2>/dev/null || true)"
          case "$workspace" in
            cpk-hosted-activity-basic|workspace-a-router|workspace-b-multiplexer|\
workspace-c-postgres|workspace-d-negative-cleanup)
              docker rm -f "$container" >/dev/null 2>&1 || true
              ;;
          esac
        fi
      done
  docker volume ls -q --filter "label=$WORKSPACE_LABEL_KEY" \
    | while IFS= read -r volume; do
        if [ -n "$volume" ]; then
          workspace="$(docker volume inspect -f "{{ index .Labels \"$WORKSPACE_LABEL_KEY\" }}" "$volume" 2>/dev/null || true)"
          case "$workspace" in
            cpk-hosted-activity-basic|workspace-a-router|workspace-b-multiplexer|\
workspace-c-postgres|workspace-d-negative-cleanup)
              docker volume rm "$volume" >/dev/null 2>&1 || true
              ;;
          esac
        fi
      done
  docker network ls -q --filter "label=$WORKSPACE_LABEL_KEY" \
    | while IFS= read -r network; do
        if [ -n "$network" ]; then
          workspace="$(docker network inspect -f "{{ index .Labels \"$WORKSPACE_LABEL_KEY\" }}" "$network" 2>/dev/null || true)"
          case "$workspace" in
            cpk-hosted-activity-basic|workspace-a-router|workspace-b-multiplexer|\
workspace-c-postgres|workspace-d-negative-cleanup)
              docker network rm "$network" >/dev/null 2>&1 || true
              ;;
          esac
        fi
      done
}

cleanup() {
  if [ -n "$SERVER_CONTAINER" ]; then
    docker rm -f "$SERVER_CONTAINER" >/dev/null 2>&1 || true
  fi
  if [ -n "$POSTGRES_CONTAINER" ]; then
    docker rm -f "$POSTGRES_CONTAINER" >/dev/null 2>&1 || true
  fi
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  cleanup_activity_resources
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
  echo "postgres did not become query-ready" >&2
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
  SERVER_CONTAINER="$(docker run -d \
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
    -e CPK_INGRESS_INTERPRETERS="$INGRESS_INTERPRETERS" \
    -e CPK_IMAGE_PULL_CREDENTIAL_RESOLVER="$IMAGE_PULL_RESOLVER" \
    -e CPK_PRODUCT_SECRET_RESOLVER=local-development \
    -e CPK_PRODUCT_SECRET_VALUES_JSON="$PRODUCT_SECRET_VALUES_JSON" \
    -e CPK_WORKPLACE_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
    -e CPK_ACTIVITY_HISTORY_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
    -e CPK_OBSERVER_STATE_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
    -e CPK_GRAPH_TOPOLOGY_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
    "$IMAGE")"
else
  SERVER_CONTAINER="$(docker run -d \
    --label "$LABEL" \
    --network "$NETWORK" \
    --network-alias cpk-server \
    --group-add "$DOCKER_SOCKET_GROUP" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -e CPK_SERVER_MODE=execution-capable \
    -e CPK_CONTROL_AUTH_CONFIGURED=true \
    -e CPK_PORT=8080 \
    -e CPK_RUNTIME_INTERPRETERS=docker \
    -e CPK_INGRESS_INTERPRETERS="$INGRESS_INTERPRETERS" \
    -e CPK_PRODUCT_SECRET_RESOLVER=local-development \
    -e CPK_PRODUCT_SECRET_VALUES_JSON="$PRODUCT_SECRET_VALUES_JSON" \
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
  -e CPK_HOSTED_ACTIVITY_BASE_URL=http://cpk-server:8080 \
  -e CPK_HOSTED_ACTIVITY_SERVER_CONTAINER="$SERVER_CONTAINER" \
  -e CPK_HOSTED_ACTIVITY_SERVERS_REPO=/app \
  -e CPK_HOSTED_ACTIVITY_SCENARIO="$SCENARIO" \
  -e CPK_HOSTED_ACTIVITY_REGISTER_PULL_AUTHORITY="$IMAGE_PULL_RESOLVER" \
  -e OPENJ92_CLOUDFLARE_ACCOUNT_ID="${OPENJ92_CLOUDFLARE_ACCOUNT_ID:-}" \
  -e OPENJ92_CLOUDFLARE_ZONE_ID="${OPENJ92_CLOUDFLARE_ZONE_ID:-}" \
  -e OPENJ92_CLOUDFLARE_ZONE="${OPENJ92_CLOUDFLARE_ZONE:-}" \
  -e OPENJ92_CLOUDFLARE_API_TOKEN="${OPENJ92_CLOUDFLARE_API_TOKEN:-}" \
  "$CONTROLLER_IMAGE" \
  python scripts/cpk_server_hosted_activity.py; then
  docker logs "$SERVER_CONTAINER" 2>&1 | tail -n 100 >&2 || true
  if [ "$KEEP_ON_FAILURE" = "1" ]; then
    trap - EXIT INT TERM
    echo "cpk-server hosted activity smoke failed; preserving containers for inspection" >&2
    echo "server_container=$SERVER_CONTAINER" >&2
    echo "postgres_container=$POSTGRES_CONTAINER" >&2
    echo "network=$NETWORK" >&2
  fi
  exit 1
fi

cleanup
POSTGRES_CONTAINER=""
SERVER_CONTAINER=""

sh scripts/docker_residue_audit.sh

echo "cpk-server hosted activity smoke passed"
