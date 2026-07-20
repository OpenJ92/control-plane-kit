#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${CPK_SERVICE_INFRASTRUCTURE_IMAGE:-control-plane-kit-live-test:service-infrastructure}"
CONTROL_NETWORK="${CPK_SERVICE_INFRASTRUCTURE_CONTROL_NETWORK:-cpk-service-infrastructure-control}"
RUNTIME_NETWORK="service-infrastructure"
POSTGRES_CONTAINER="${CPK_SERVICE_INFRASTRUCTURE_CONTROL_POSTGRES:-cpk-service-infrastructure-control-postgres}"
CONTROLLER="${CPK_SERVICE_INFRASTRUCTURE_CONTROLLER:-cpk-service-infrastructure-controller}"
DATABASE_URL="postgresql://cpk:cpk@${POSTGRES_CONTAINER}:5432/cpk"
CREATED_AT="${CPK_SERVICE_INFRASTRUCTURE_CREATED_AT:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
WORKSPACE_ID="service-infrastructure-live"
RUNTIME_ID="service-infrastructure"
TEST_LABEL="io.control-plane-kit.test"
TEST_ID="service-infrastructure-live"

remove_test_container() {
  local name="$1"
  if ! docker container inspect "$name" >/dev/null 2>&1; then
    return
  fi
  if test "$(docker inspect -f "{{ index .Config.Labels \"$TEST_LABEL\" }}" "$name")" != "$TEST_ID"; then
    echo "Refusing to remove unowned service acceptance container: $name" >&2
    return 1
  fi
  docker rm -f "$name" >/dev/null
}

remove_runtime_resources() {
  local name
  local data_resource
  local configuration_artifact
  local secret_material
  while IFS= read -r name; do
    test -n "$name" || continue
    if test "$(docker container inspect -f '{{ index .Config.Labels "io.control-plane-kit.package" }}' "$name")" != "control-plane-kit"; then
      echo "Refusing to remove unowned service runtime container: $name" >&2
      return 1
    fi
    docker rm -f "$name" >/dev/null
  done < <(docker ps -a \
    --filter "label=io.control-plane-kit.workspace=${WORKSPACE_ID}" \
    --filter "label=io.control-plane-kit.runtime=${RUNTIME_ID}" \
    --format '{{.Names}}')
  while IFS= read -r name; do
    test -n "$name" || continue
    if test "$(docker volume inspect -f '{{ index .Labels "io.control-plane-kit.package" }}' "$name")" != "control-plane-kit"; then
      echo "Refusing to remove unowned service runtime volume: $name" >&2
      return 1
    fi
    data_resource="$(docker volume inspect -f '{{ index .Labels "io.control-plane-kit.data-resource" }}' "$name")"
    configuration_artifact="$(docker volume inspect -f '{{ index .Labels "io.control-plane-kit.configuration-artifact" }}' "$name")"
    secret_material="$(docker volume inspect -f '{{ index .Labels "io.control-plane-kit.secret-material" }}' "$name")"
    if test -n "$data_resource"; then
      echo "Retaining graph-owned data volume during fallback cleanup: $name" >&2
      continue
    fi
    if test -z "$configuration_artifact" && test -z "$secret_material"; then
      echo "Refusing to remove owned volume with unknown persistence semantics: $name" >&2
      return 1
    fi
    docker volume rm "$name" >/dev/null
  done < <(docker volume ls \
    --filter "label=io.control-plane-kit.workspace=${WORKSPACE_ID}" \
    --format '{{.Name}}')
  if docker network inspect "$RUNTIME_NETWORK" >/dev/null 2>&1; then
    if test "$(docker network inspect -f '{{ index .Labels "io.control-plane-kit.workspace" }}|{{ index .Labels "io.control-plane-kit.runtime" }}|{{ index .Labels "io.control-plane-kit.package" }}' "$RUNTIME_NETWORK")" != "${WORKSPACE_ID}|${RUNTIME_ID}|control-plane-kit"; then
      echo "Refusing to remove unowned service runtime network" >&2
      return 1
    fi
    docker network rm "$RUNTIME_NETWORK" >/dev/null
  fi
}

remove_control_network() {
  if ! docker network inspect "$CONTROL_NETWORK" >/dev/null 2>&1; then
    return
  fi
  if test "$(docker network inspect -f "{{ index .Labels \"$TEST_LABEL\" }}" "$CONTROL_NETWORK")" != "$TEST_ID"; then
    echo "Refusing to remove unowned service control network" >&2
    return 1
  fi
  docker network rm "$CONTROL_NETWORK" >/dev/null
}

cleanup() {
  remove_test_container "$CONTROLLER" || true
  remove_runtime_resources || true
  remove_test_container "$POSTGRES_CONTAINER" || true
  remove_control_network || true
}

run_example() {
  docker run --rm \
    --network "$CONTROL_NETWORK" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -e CPK_SERVICE_INFRASTRUCTURE_DATABASE_URL="$DATABASE_URL" \
    -e CPK_SERVICE_INFRASTRUCTURE_IMAGE="$IMAGE_NAME" \
    -e CPK_SERVICE_INFRASTRUCTURE_CREATED_AT="$CREATED_AT" \
    "$IMAGE_NAME" python -m examples.service_infrastructure_live "$@"
}

trap cleanup EXIT
cleanup

docker build --target live-test -t "$IMAGE_NAME" .
docker network create --label "$TEST_LABEL=$TEST_ID" "$CONTROL_NETWORK" >/dev/null
docker run -d \
  --name "$POSTGRES_CONTAINER" \
  --label "$TEST_LABEL=$TEST_ID" \
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

run_example prepare

docker run -d \
  --name "$CONTROLLER" \
  --label "$TEST_LABEL=$TEST_ID" \
  --network "$CONTROL_NETWORK" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e CPK_SERVICE_INFRASTRUCTURE_DATABASE_URL="$DATABASE_URL" \
  -e CPK_SERVICE_INFRASTRUCTURE_IMAGE="$IMAGE_NAME" \
  -e CPK_SERVICE_INFRASTRUCTURE_CREATED_AT="$CREATED_AT" \
  "$IMAGE_NAME" sleep infinity >/dev/null
docker network connect "$RUNTIME_NETWORK" "$CONTROLLER"

docker exec "$CONTROLLER" python -m examples.service_infrastructure_live resume-deploy
docker exec "$CONTROLLER" python -m examples.service_infrastructure_live verify-products
docker exec "$CONTROLLER" python -m examples.service_infrastructure_live begin-teardown

remove_test_container "$CONTROLLER"
run_example finish-teardown

if docker network inspect "$RUNTIME_NETWORK" >/dev/null 2>&1; then
  echo "Canonical service teardown left the runtime network behind" >&2
  exit 1
fi
if test -n "$(docker ps -a --filter "label=io.control-plane-kit.workspace=${WORKSPACE_ID}" --format '{{.Names}}')"; then
  echo "Canonical service teardown left owned compute behind" >&2
  exit 1
fi
if test -n "$(docker volume ls --filter "label=io.control-plane-kit.workspace=${WORKSPACE_ID}" --format '{{.Name}}')"; then
  echo "Canonical service teardown left owned ephemeral volumes behind" >&2
  exit 1
fi

remove_test_container "$POSTGRES_CONTAINER"
remove_control_network
trap - EXIT

echo "Service infrastructure live proof passed through DeploymentProgram and cleanup."
