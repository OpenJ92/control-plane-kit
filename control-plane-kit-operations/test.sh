#!/usr/bin/env sh
set -eu

IMAGE="${CPK_OPERATIONS_TEST_IMAGE:-python:3.14-slim}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$ROOT/.." && pwd)"

docker run --rm \
  -v "$REPO_ROOT/control-plane-kit-core:/core:ro" \
  -v "$ROOT:/source:ro" \
  -e PYTHONDONTWRITEBYTECODE=1 \
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
