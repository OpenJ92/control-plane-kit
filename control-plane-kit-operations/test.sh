#!/usr/bin/env sh
set -eu

IMAGE="${CPK_OPERATIONS_TEST_IMAGE:-python:3.14-slim}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$ROOT/.." && pwd)"
ARCHITECTURE_TESTING_COMMIT="7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef"
ARCHITECTURE_TESTING_ROOT="${CPK_ARCHITECTURE_TESTING_ROOT:-$REPO_ROOT/../control-plane-kit-architecture-testing}"
NETWORK_NAME="${CPK_OPERATIONS_TEST_NETWORK_NAME:-cpk-operations-test}"
POSTGRES_CONTAINER="${CPK_OPERATIONS_TEST_POSTGRES_CONTAINER:-cpk-operations-test-postgres}"

if [ ! -d "$ARCHITECTURE_TESTING_ROOT" ]; then
  echo "Architecture testing checkout is missing" >&2
  exit 1
fi

actual_architecture_testing_commit="$(git -C "$ARCHITECTURE_TESTING_ROOT" rev-parse HEAD)"
if [ "$actual_architecture_testing_commit" != "$ARCHITECTURE_TESTING_COMMIT" ]; then
  echo "Architecture testing checkout is not at the accepted commit" >&2
  exit 1
fi

if [ -n "$(git -C "$ARCHITECTURE_TESTING_ROOT" status --short --untracked-files=all)" ]; then
  echo "Architecture testing checkout is not clean" >&2
  exit 1
fi

cleanup() {
  docker rm -fv "$POSTGRES_CONTAINER" >/dev/null 2>&1 || true
  docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
}

trap cleanup EXIT

docker run --rm \
  -v "$ROOT:/source:ro" \
  -v "$REPO_ROOT/test_support:/test-support:ro" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  "$IMAGE" \
  python /test-support/package_integrity.py \
    --package-root /source \
    --source-root src \
    --test-root tests \
    --gate-file test.sh

cleanup
docker network create "$NETWORK_NAME" >/dev/null

docker run -d \
  --name "$POSTGRES_CONTAINER" \
  --network "$NETWORK_NAME" \
  --tmpfs /var/lib/postgresql/data:rw,noexec,nosuid,size=2g \
  -e POSTGRES_DB=cpk \
  -e POSTGRES_USER=cpk \
  -e POSTGRES_PASSWORD=cpk \
  --health-cmd "pg_isready -U cpk -d cpk" \
  --health-interval 1s \
  --health-timeout 5s \
  --health-retries 30 \
  postgres:16-alpine \
  postgres \
    -c max_wal_size=512MB \
    -c checkpoint_timeout=2min \
    -c log_checkpoints=on >/dev/null

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
  docker logs --timestamps --tail 400 "$POSTGRES_CONTAINER" >&2 || true
  exit 1
fi

if docker run --rm \
  -v "$REPO_ROOT/control-plane-kit-core:/core:ro" \
  -v "$ROOT:/source:ro" \
  -v "$REPO_ROOT/docs/architecture/package-module-inventory.json:/package-module-inventory.json:ro" \
  -v "$REPO_ROOT/docs/READ_INTERFACES.md:/read-interfaces.md:ro" \
  -v "$ARCHITECTURE_TESTING_ROOT:/architecture-testing:ro" \
  -v "$REPO_ROOT/.github/workflows/tests.yml:/cpk-test-evidence/tests-workflow.yml:ro" \
  -v "$REPO_ROOT/docs/TESTING.md:/cpk-test-evidence/TESTING.md:ro" \
  --network "$NETWORK_NAME" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONPATH=/architecture-testing/src \
  -e CPK_TEST_WORKFLOW_PATH=/cpk-test-evidence/tests-workflow.yml \
  -e CPK_TESTING_DOCUMENT_PATH=/cpk-test-evidence/TESTING.md \
  -e CPK_CORE_SOURCE_ROOT=/core \
  -e CPK_OPERATIONS_SOURCE_ROOT=/source \
  -e CPK_PACKAGE_MODULE_INVENTORY=/package-module-inventory.json \
  -e CPK_READ_INTERFACES_DOCUMENT=/read-interfaces.md \
  -e CPK_OPERATIONS_TEST_DATABASE_URL=postgresql://cpk:cpk@"$POSTGRES_CONTAINER":5432/cpk \
  "$IMAGE" \
  sh -c 'cp -a /core /tmp/core && cp -a /source /tmp/pkg && python -m pip install --root-user-action=ignore /tmp/core >/tmp/pip-core.log && python -m pip install --root-user-action=ignore /tmp/pkg >/tmp/pip-operations.log && cd /tmp/pkg && python -m unittest discover -s tests'; then
  :
else
  test_status=$?
  docker logs --timestamps --tail 400 "$POSTGRES_CONTAINER" >&2 || true
  exit "$test_status"
fi

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
from importlib.util import find_spec

if control_plane_kit_operations.__version__ != "0.1.0":
    raise SystemExit("unexpected control_plane_kit_operations version")
architecture_testing_absent = find_spec("control_plane_kit_architecture_testing") is None
if not architecture_testing_absent:
    raise SystemExit("architecture testing leaked into the clean import container")

print("control-plane-kit-operations import ok")
PY'
