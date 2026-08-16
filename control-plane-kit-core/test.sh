#!/usr/bin/env sh
set -eu

IMAGE="${CPK_CORE_TEST_IMAGE:-python:3.14-slim}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$ROOT/.." && pwd)"

docker run --rm \
  -v "$REPO_ROOT/test_support:/test-support:ro" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  "$IMAGE" \
  sh -c 'cd /test-support && python -m unittest discover -s tests -v'

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

docker run --rm \
  -v "$ROOT:/source:ro" \
  -v "$REPO_ROOT/docs/architecture/package-module-inventory.json:/package-module-inventory.json:ro" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e CPK_PACKAGE_MODULE_INVENTORY=/package-module-inventory.json \
  "$IMAGE" \
  sh -c 'cp -a /source /tmp/pkg && cd /tmp/pkg && python -m pip install --root-user-action=ignore . >/tmp/pip.log && python -m unittest discover -s tests'

docker run --rm \
  -v "$ROOT:/source:ro" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  "$IMAGE" \
  sh -c 'cp -a /source /tmp/pkg && cd /tmp/pkg && python -m compileall src tests'

docker run --rm \
  -v "$ROOT:/source:ro" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  "$IMAGE" \
  sh -c 'cp -a /source /tmp/pkg && cd /tmp/pkg && python -m pip install --root-user-action=ignore . >/tmp/pip.log && cd /tmp && python - <<'"'"'PY'"'"'
import control_plane_kit_core

if control_plane_kit_core.__version__ != "0.1.0":
    raise SystemExit("unexpected control_plane_kit_core version")

print("control-plane-kit-core import ok")
PY'
