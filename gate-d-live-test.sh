#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${CPK_GATE_D_IMAGE_NAME:-control-plane-kit-live-test:local}"
CONTROL_NETWORK="${CPK_GATE_D_CONTROL_NETWORK:-cpk-gate-d-control}"
RUNTIME_NETWORK="cpk-gate-d-live"
POSTGRES_CONTAINER="${CPK_GATE_D_POSTGRES_CONTAINER:-cpk-gate-d-postgres}"
CONTROLLER="${CPK_GATE_D_CONTROLLER:-cpk-gate-d-controller}"
DATABASE_URL="postgresql://cpk:cpk@${POSTGRES_CONTAINER}:5432/cpk"

cleanup() {
  docker rm -f "$CONTROLLER" >/dev/null 2>&1 || true
  docker rm -f gate-d-runtime-router >/dev/null 2>&1 || true
  docker rm -f gate-d-runtime-hello-blue >/dev/null 2>&1 || true
  docker rm -f gate-d-runtime-hello-green >/dev/null 2>&1 || true
  docker network rm "$RUNTIME_NETWORK" >/dev/null 2>&1 || true
  docker rm -f "$POSTGRES_CONTAINER" >/dev/null 2>&1 || true
  docker network rm "$CONTROL_NETWORK" >/dev/null 2>&1 || true
}

trap cleanup EXIT
cleanup

docker build --target live-test -t "$IMAGE_NAME" .
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

docker run --rm \
  --network "$CONTROL_NETWORK" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e CPK_GATE_D_DATABASE_URL="$DATABASE_URL" \
  "$IMAGE_NAME" \
  python -m examples.gate_d_live_smoke prepare

docker run -d \
  --name "$CONTROLLER" \
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
docker rm -f "$CONTROLLER" >/dev/null
docker run --rm \
  --network "$CONTROL_NETWORK" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e CPK_GATE_D_DATABASE_URL="$DATABASE_URL" \
  "$IMAGE_NAME" \
  python -m examples.gate_d_live_smoke finish-teardown \
    --run-id teardown-run-1 \
    --plan-id teardown-plan-1 \
    --graph-id teardown-graph-1

echo "Gate D live smoke passed: ${blue} -> ${green}; unauthorized mutation returned 401."
