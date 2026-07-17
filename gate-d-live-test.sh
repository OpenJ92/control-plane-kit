#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${CPK_GATE_D_IMAGE_NAME:-control-plane-kit-live-test:local}"
CONTROL_NETWORK="${CPK_GATE_D_CONTROL_NETWORK:-cpk-gate-d-control}"
RUNTIME_NETWORK="cpk-gate-d-live"
POSTGRES_CONTAINER="${CPK_GATE_D_POSTGRES_CONTAINER:-cpk-gate-d-postgres}"
CONTROLLER="${CPK_GATE_D_CONTROLLER:-cpk-gate-d-controller}"
DATABASE_URL="postgresql://cpk:cpk@${POSTGRES_CONTAINER}:5432/cpk"
TEST_LABEL="io.control-plane-kit.test"
TEST_ID="gate-d-live"

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

remove_runtime_container() {
  local name="$1"
  local node_id="$2"
  if ! docker container inspect "$name" >/dev/null 2>&1; then
    return
  fi
  if test "$(docker inspect -f '{{ index .Config.Labels "io.control-plane-kit.package" }}' "$name")" != control-plane-kit \
    || test "$(docker inspect -f '{{ index .Config.Labels "io.control-plane-kit.workspace" }}' "$name")" != gate-d-live \
    || test "$(docker inspect -f '{{ index .Config.Labels "io.control-plane-kit.runtime" }}' "$name")" != gate-d-runtime \
    || test "$(docker inspect -f '{{ index .Config.Labels "io.control-plane-kit.resource" }}' "$name")" != container \
    || test "$(docker inspect -f '{{ index .Config.Labels "io.control-plane-kit.node" }}' "$name")" != "$node_id"; then
    echo "Refusing to remove unowned runtime container: $name" >&2
    return 1
  fi
  docker rm -f "$name" >/dev/null
}

remove_owned_network() {
  local name="$1"
  local ownership="$2"
  if ! docker network inspect "$name" >/dev/null 2>&1; then
    return
  fi
  if test "$ownership" = test; then
    test "$(docker network inspect -f "{{ index .Labels \"$TEST_LABEL\" }}" "$name")" = "$TEST_ID" || {
      echo "Refusing to remove unowned network: $name" >&2
      return 1
    }
  elif test "$(docker network inspect -f '{{ index .Labels "io.control-plane-kit.package" }}' "$name")" != control-plane-kit \
    || test "$(docker network inspect -f '{{ index .Labels "io.control-plane-kit.workspace" }}' "$name")" != gate-d-live \
    || test "$(docker network inspect -f '{{ index .Labels "io.control-plane-kit.runtime" }}' "$name")" != gate-d-runtime \
    || test "$(docker network inspect -f '{{ index .Labels "io.control-plane-kit.resource" }}' "$name")" != network; then
    echo "Refusing to remove unowned network: $name" >&2
    return 1
  fi
  docker network rm "$name" >/dev/null
}

cleanup() {
  local status=0
  remove_test_container "$CONTROLLER" || status=1
  remove_runtime_container gate-d-runtime-router router || status=1
  remove_runtime_container gate-d-runtime-hello-blue hello-blue || status=1
  remove_runtime_container gate-d-runtime-hello-green hello-green || status=1
  remove_owned_network "$RUNTIME_NETWORK" runtime || status=1
  remove_test_container "$POSTGRES_CONTAINER" || status=1
  remove_owned_network "$CONTROL_NETWORK" test || status=1
  return "$status"
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

docker run --rm \
  --network "$CONTROL_NETWORK" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e CPK_GATE_D_DATABASE_URL="$DATABASE_URL" \
  "$IMAGE_NAME" \
  python -m examples.gate_d_live_smoke prepare

docker run -d \
  --name "$CONTROLLER" \
  --label "$TEST_LABEL=$TEST_ID" \
  --network "$CONTROL_NETWORK" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e CPK_GATE_D_DATABASE_URL="$DATABASE_URL" \
  "$IMAGE_NAME" sleep infinity >/dev/null
docker network connect "$RUNTIME_NETWORK" "$CONTROLLER"

docker exec "$CONTROLLER" python -m examples.gate_d_live_smoke resume-deploy \
  --run-id deploy-run-1 \
  --plan-id deploy-plan-1 \
  --graph-id deploy-graph-1

blue="$(curl --fail --silent --show-error --max-time 10 http://127.0.0.1:18180/)"
test "$blue" = "Hello, blue!"

unauthorized="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --request POST \
  --header 'Content-Type: application/json' \
  --data '{"target_id":"hello-green"}' \
  http://127.0.0.1:18180/__deploy/active-target)"
test "$unauthorized" = "401"
test "$(curl --fail --silent --show-error --max-time 10 http://127.0.0.1:18180/)" = "Hello, blue!"

docker exec "$CONTROLLER" python -m examples.gate_d_live_smoke switch \
  --graph-id deploy-graph-1

green="$(curl --fail --silent --show-error --max-time 10 http://127.0.0.1:18180/)"
test "$green" = "Hello, green!"

docker exec "$CONTROLLER" python -m examples.gate_d_live_smoke begin-teardown \
  --graph-id switch-graph-1
remove_test_container "$CONTROLLER"
docker run --rm \
  --network "$CONTROL_NETWORK" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e CPK_GATE_D_DATABASE_URL="$DATABASE_URL" \
  "$IMAGE_NAME" \
  python -m examples.gate_d_live_smoke finish-teardown \
    --run-id teardown-run-1 \
    --plan-id teardown-plan-1 \
    --graph-id teardown-graph-1

cleanup
trap - EXIT
echo "Gate D live smoke passed: ${blue} -> ${green}; unauthorized mutation returned 401."
