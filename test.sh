#!/bin/sh
set -eu

IMAGE="${CPK_SERVERS_TEST_IMAGE:-control-plane-kit-servers-test:local}"
POLICY_IMAGE="${CPK_SERVERS_POLICY_IMAGE:-python:3.14-slim}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

cd "$ROOT"

docker run --rm \
  -v "$ROOT:/source:ro" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  "$POLICY_IMAGE" \
  sh -c 'cd /source && PYTHONPATH=/source/src python scripts/apply_coordinates.py --check'

docker run --rm \
  -v "$ROOT:/source:ro" \
  -v "$ROOT/test_support:/test-support:ro" \
  -e CPK_PACKAGE_ROOT=/source \
  -e PYTHONDONTWRITEBYTECODE=1 \
  "$POLICY_IMAGE" \
  sh -c 'cd /test-support && python -m unittest discover -s tests -v'

docker run --rm \
  -v "$ROOT:/source:ro" \
  -v "$ROOT/test_support:/test-support:ro" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  "$POLICY_IMAGE" \
  python /test-support/package_integrity.py \
    --package-root /source \
    --source-root src \
    --source-root products \
    --test-root tests \
    --test-root products \
    --gate-file test.sh

docker build -f Dockerfile.test -t "$IMAGE" .
docker run --rm "$IMAGE"
docker run --rm "$IMAGE" \
  sh -c 'cd /tmp && python -c "import control_plane_kit_servers; print(\"control-plane-kit-servers import ok\")"'
CPK_SERVER_BUILD_IMAGE=1 sh scripts/cpk_server_image_smoke.sh
sh scripts/docker_residue_audit.sh
