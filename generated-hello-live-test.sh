#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${CPK_GENERATED_HELLO_IMAGE_NAME:-control-plane-kit-live-test:local}"
CONTROL_NETWORK="${CPK_GENERATED_HELLO_CONTROL_NETWORK:-cpk-generated-hello-control}"
RUNTIME_NETWORK="cpk-hello-stress"
POSTGRES_CONTAINER="${CPK_GENERATED_HELLO_POSTGRES_CONTAINER:-cpk-generated-hello-postgres}"
CONTROLLER="${CPK_GENERATED_HELLO_CONTROLLER:-cpk-generated-hello-controller}"
DATABASE_URL="postgresql://cpk:cpk@${POSTGRES_CONTAINER}:5432/cpk"
TEST_LABEL="io.control-plane-kit.test"
TEST_ID="generated-hello-live"
WORKSPACE_ID="generated-hello-live"
RUNTIME_ID="hello-stress-runtime"
ROOT_PORT=18280

export CPK_GENERATED_HELLO_BRANCHING_FACTOR="${CPK_GENERATED_HELLO_BRANCHING_FACTOR:-2}"
export CPK_GENERATED_HELLO_DEPTH="${CPK_GENERATED_HELLO_DEPTH:-1}"

test "$CPK_GENERATED_HELLO_BRANCHING_FACTOR" -ge 1
test "$CPK_GENERATED_HELLO_DEPTH" -ge 1

remove_test_container() {
  local name="$1"
  if ! docker container inspect "$name" >/dev/null 2>&1; then
    return
  fi
  if test "$(docker inspect -f "{{ index .Config.Labels \"$TEST_LABEL\" }}" "$name")" != "$TEST_ID"; then
    echo "Refusing to remove unowned test container: $name" >&2
    return 1
  fi
  docker rm -f "$name" >/dev/null
}

assert_runtime_labels() {
  local kind="$1"
  local name="$2"
  local node_id="${3:-}"
  local data_id="${4:-}"
  local expected="control-plane-kit|${WORKSPACE_ID}|${RUNTIME_ID}|${kind}|${node_id}|${data_id}"
  local actual
  case "$kind" in
    container)
      actual="$(docker container inspect -f '{{ index .Config.Labels "io.control-plane-kit.package" }}|{{ index .Config.Labels "io.control-plane-kit.workspace" }}|{{ index .Config.Labels "io.control-plane-kit.runtime" }}|{{ index .Config.Labels "io.control-plane-kit.resource" }}|{{ index .Config.Labels "io.control-plane-kit.node" }}|{{ index .Config.Labels "io.control-plane-kit.data-resource" }}' "$name")"
      ;;
    network)
      actual="$(docker network inspect -f '{{ index .Labels "io.control-plane-kit.package" }}|{{ index .Labels "io.control-plane-kit.workspace" }}|{{ index .Labels "io.control-plane-kit.runtime" }}|{{ index .Labels "io.control-plane-kit.resource" }}|{{ index .Labels "io.control-plane-kit.node" }}|{{ index .Labels "io.control-plane-kit.data-resource" }}' "$name")"
      ;;
    volume)
      actual="$(docker volume inspect -f '{{ index .Labels "io.control-plane-kit.package" }}|{{ index .Labels "io.control-plane-kit.workspace" }}|{{ index .Labels "io.control-plane-kit.runtime" }}|{{ index .Labels "io.control-plane-kit.resource" }}|{{ index .Labels "io.control-plane-kit.node" }}|{{ index .Labels "io.control-plane-kit.data-resource" }}' "$name")"
      ;;
    *) echo "Unknown runtime resource kind: $kind" >&2; return 1 ;;
  esac
  if test "$actual" != "$expected"; then
    echo "Refusing to remove runtime resource with mismatched ownership: $name" >&2
    return 1
  fi
}

remove_runtime_containers() {
  local name node_id
  while IFS= read -r name; do
    test -n "$name" || continue
    node_id="$(docker container inspect -f '{{ index .Config.Labels "io.control-plane-kit.node" }}' "$name")"
    assert_runtime_labels container "$name" "$node_id"
    docker rm -f "$name" >/dev/null
  done < <(docker ps -a --filter "label=io.control-plane-kit.workspace=${WORKSPACE_ID}" --filter "label=io.control-plane-kit.runtime=${RUNTIME_ID}" --format '{{.Names}}')
}

remove_runtime_volumes() {
  local name node_id data_id
  while IFS= read -r name; do
    test -n "$name" || continue
    node_id="$(docker volume inspect -f '{{ index .Labels "io.control-plane-kit.node" }}' "$name")"
    data_id="$(docker volume inspect -f '{{ index .Labels "io.control-plane-kit.data-resource" }}' "$name")"
    assert_runtime_labels volume "$name" "$node_id" "$data_id"
    docker volume rm "$name" >/dev/null
  done < <(docker volume ls --filter "label=io.control-plane-kit.workspace=${WORKSPACE_ID}" --filter "label=io.control-plane-kit.runtime=${RUNTIME_ID}" --format '{{.Name}}')
}

remove_runtime_network() {
  if ! docker network inspect "$RUNTIME_NETWORK" >/dev/null 2>&1; then
    return
  fi
  assert_runtime_labels network "$RUNTIME_NETWORK"
  docker network rm "$RUNTIME_NETWORK" >/dev/null
}

remove_control_network() {
  if ! docker network inspect "$CONTROL_NETWORK" >/dev/null 2>&1; then
    return
  fi
  if test "$(docker network inspect -f "{{ index .Labels \"$TEST_LABEL\" }}" "$CONTROL_NETWORK")" != "$TEST_ID"; then
    echo "Refusing to remove unowned control network: $CONTROL_NETWORK" >&2
    return 1
  fi
  docker network rm "$CONTROL_NETWORK" >/dev/null
}

cleanup() {
  local status=0
  remove_test_container "$CONTROLLER" || status=1
  remove_runtime_containers || status=1
  remove_runtime_network || status=1
  remove_runtime_volumes || status=1
  remove_test_container "$POSTGRES_CONTAINER" || status=1
  remove_control_network || status=1
  return "$status"
}

run_example() {
  docker run --rm \
    --network "$CONTROL_NETWORK" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -e CPK_GENERATED_HELLO_DATABASE_URL="$DATABASE_URL" \
    -e CPK_GENERATED_HELLO_BRANCHING_FACTOR \
    -e CPK_GENERATED_HELLO_DEPTH \
    "$IMAGE_NAME" \
    python -m examples.generated_hello_live "$@"
}

trap 'cleanup || true' EXIT
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

run_example prove-invalid
if docker network inspect "$RUNTIME_NETWORK" >/dev/null 2>&1; then
  echo "Invalid generated graph unexpectedly created the runtime network" >&2
  exit 1
fi

run_example prepare

docker run -d \
  --name "$CONTROLLER" \
  --label "$TEST_LABEL=$TEST_ID" \
  --network "$CONTROL_NETWORK" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e CPK_GENERATED_HELLO_DATABASE_URL="$DATABASE_URL" \
  -e CPK_GENERATED_HELLO_BRANCHING_FACTOR \
  -e CPK_GENERATED_HELLO_DEPTH \
  "$IMAGE_NAME" sleep infinity >/dev/null
docker network connect "$RUNTIME_NETWORK" "$CONTROLLER"

docker exec "$CONTROLLER" python -m examples.generated_hello_live resume-deploy

root="$(curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:${ROOT_PORT}/")"
test "$root" = "Hello from hello-root!"

docker exec "$CONTROLLER" python -m examples.generated_hello_live verify

docker exec "$CONTROLLER" python -m examples.generated_hello_live begin-teardown

remove_test_container "$CONTROLLER"
run_example finish-teardown

if docker network inspect "$RUNTIME_NETWORK" >/dev/null 2>&1; then
  echo "Canonical teardown left the runtime network behind" >&2
  exit 1
fi
if test -n "$(docker ps -a --filter "label=io.control-plane-kit.workspace=${WORKSPACE_ID}" --format '{{.Names}}')"; then
  echo "Canonical teardown left generated compute behind" >&2
  exit 1
fi

retained_volumes="$(docker volume ls --filter "label=io.control-plane-kit.workspace=${WORKSPACE_ID}" --filter "label=io.control-plane-kit.runtime=${RUNTIME_ID}" --format '{{.Name}}')"
test -n "$retained_volumes"
remove_runtime_volumes

cleanup
trap - EXIT
echo "Generated Hello live proof passed: ${root}; branching=${CPK_GENERATED_HELLO_BRANCHING_FACTOR}, depth=${CPK_GENERATED_HELLO_DEPTH}."
