#!/usr/bin/env sh
set -eu

IMAGE="${CPK_OPERATIONS_TEST_IMAGE:-python:3.14-slim}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$ROOT/.." && pwd)"
NETWORK_NAME="${CPK_OPERATIONS_TEST_NETWORK_NAME:-cpk-operations-test}"
POSTGRES_CONTAINER="${CPK_OPERATIONS_TEST_POSTGRES_CONTAINER:-cpk-operations-test-postgres}"

cleanup() {
  docker rm -f "$POSTGRES_CONTAINER" >/dev/null 2>&1 || true
  docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
}

trap cleanup EXIT

cleanup
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

docker run --rm \
  -v "$REPO_ROOT/control-plane-kit-core:/core:ro" \
  -v "$ROOT:/source:ro" \
  --network "$NETWORK_NAME" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e CPK_OPERATIONS_TEST_DATABASE_URL=postgresql://cpk:cpk@"$POSTGRES_CONTAINER":5432/cpk \
  "$IMAGE" \
  sh -c 'cp -a /core /tmp/core && cp -a /source /tmp/pkg && python -m pip install --root-user-action=ignore /tmp/core >/tmp/pip-core.log && python -m pip install --root-user-action=ignore /tmp/pkg >/tmp/pip-operations.log && cd /tmp/pkg && python -m unittest discover -s tests'

docker run --rm \
  -v "$REPO_ROOT/control-plane-kit-core:/core:ro" \
  -v "$ROOT:/source:ro" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  "$IMAGE" \
  sh -c 'cp -a /core /tmp/core && cp -a /source /tmp/pkg && python -m pip install --root-user-action=ignore /tmp/core >/tmp/pip-core.log && python -m pip install --root-user-action=ignore /tmp/pkg >/tmp/pip-operations.log && cd /tmp/pkg && python -m compileall src tests'

docker run --rm \
  -v "$REPO_ROOT/control-plane-kit-core:/core:ro" \
  -v "$ROOT:/source:ro" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  "$IMAGE" \
  sh -c 'cp -a /core /tmp/core && cp -a /source /tmp/pkg && python -m pip install --root-user-action=ignore /tmp/core >/tmp/pip-core.log && python -m pip install --root-user-action=ignore /tmp/pkg >/tmp/pip-operations.log && cd /tmp && python - <<'"'"'PY'"'"'
import control_plane_kit_operations

if control_plane_kit_operations.__version__ != "0.1.0":
    raise SystemExit("unexpected control_plane_kit_operations version")

print("control-plane-kit-operations import ok")
PY'
