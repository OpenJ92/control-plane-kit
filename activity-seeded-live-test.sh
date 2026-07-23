#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${CPK_ACTIVITY_LIVE_IMAGE:-control-plane-kit-activity-live-test:local}"
SERVER_REPO="${CPK_SERVERS_REPO:-../control-plane-kit-servers}"
CONTROL_NETWORK="${CPK_ACTIVITY_CONTROL_NETWORK:-cpk-activity-control}"
POSTGRES_CONTAINER="${CPK_ACTIVITY_POSTGRES:-cpk-activity-control-postgres}"
CONTROLLER="${CPK_ACTIVITY_CONTROLLER:-cpk-activity-controller}"
DATABASE_URL="postgresql://cpk:cpk@${POSTGRES_CONTAINER}:5432/cpk"
WORKSPACE_PREFIX="activity-live"

remove_container() {
  local name="$1"
  if docker container inspect "$name" >/dev/null 2>&1; then
    docker rm -f "$name" >/dev/null
  fi
}

cleanup_activity_resources() {
  local name
  while IFS= read -r name; do
    test -n "$name" || continue
    if test "$(docker inspect -f '{{ index .Config.Labels "control-plane-kit.owner" }}' "$name")" != "operations"; then
      echo "Refusing to remove unowned ACTIVITY container: $name" >&2
      return 1
    fi
    docker rm -f "$name" >/dev/null
  done < <(docker ps -a --filter "label=control-plane-kit.workspace-id" --format '{{.Names}}' | grep "control-plane-kit-${WORKSPACE_PREFIX}" || true)

  while IFS= read -r name; do
    test -n "$name" || continue
    if test "$(docker network inspect -f '{{ index .Labels "control-plane-kit.owner" }}' "$name")" != "operations"; then
      echo "Refusing to remove unowned ACTIVITY network: $name" >&2
      return 1
    fi
    docker network rm "$name" >/dev/null || true
  done < <(docker network ls --filter "label=control-plane-kit.workspace-id" --format '{{.Name}}' | grep "${WORKSPACE_PREFIX}" || true)

  while IFS= read -r name; do
    test -n "$name" || continue
    if test "$(docker volume inspect -f '{{ index .Labels "control-plane-kit.owner" }}' "$name")" != "operations"; then
      echo "Refusing to remove unowned ACTIVITY volume: $name" >&2
      return 1
    fi
    docker volume rm "$name" >/dev/null || true
  done < <(docker volume ls --filter "label=control-plane-kit.workspace-id" --format '{{.Name}}' | grep "${WORKSPACE_PREFIX}" || true)
}

cleanup() {
  remove_container "$CONTROLLER" || true
  cleanup_activity_resources || true
  remove_container "$POSTGRES_CONTAINER" || true
  docker network rm "$CONTROL_NETWORK" >/dev/null 2>&1 || true
}

if test ! -d "$SERVER_REPO/products"; then
  echo "CPK_SERVERS_REPO must point at control-plane-kit-servers" >&2
  exit 1
fi

trap cleanup EXIT
cleanup

docker build --target activity-live-test -t "$IMAGE_NAME" .
docker network create "$CONTROL_NETWORK" >/dev/null
docker run -d \
  --name "$POSTGRES_CONTAINER" \
  --network "$CONTROL_NETWORK" \
  -e POSTGRES_DB=cpk \
  -e POSTGRES_USER=cpk \
  -e POSTGRES_PASSWORD=cpk \
  --health-cmd "pg_isready -U cpk -d cpk" \
  --health-interval 1s \
  --health-timeout 5s \
  --health-retries 30 \
  postgres:16-alpine >/dev/null

until test "$(docker inspect -f '{{.State.Health.Status}}' "$POSTGRES_CONTAINER")" = healthy; do
  sleep 1
done

docker run -d \
  --name "$CONTROLLER" \
  --network "$CONTROL_NETWORK" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$(cd "$SERVER_REPO" && pwd):/workspace/control-plane-kit-servers:ro" \
  -e CPK_ACTIVITY_DATABASE_URL="$DATABASE_URL" \
  -e CPK_ACTIVITY_CONTROLLER="$CONTROLLER" \
  -e CPK_ACTIVITY_SERVERS_REPO=/workspace/control-plane-kit-servers \
  "$IMAGE_NAME" sleep infinity >/dev/null

docker exec "$CONTROLLER" python -m examples.activity_seeded_live

remove_container "$CONTROLLER"
cleanup_activity_resources
remove_container "$POSTGRES_CONTAINER"
docker network rm "$CONTROL_NETWORK" >/dev/null
trap - EXIT

echo "ACTIVITY seeded live proof passed."
