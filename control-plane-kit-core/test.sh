#!/usr/bin/env sh
set -eu

IMAGE="${CPK_CORE_TEST_IMAGE:-python:3.14-slim}"
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

docker run --rm \
  -v "$ROOT:/pkg:ro" \
  -w /pkg \
  -e PYTHONPATH=/pkg/src \
  -e PYTHONDONTWRITEBYTECODE=1 \
  "$IMAGE" \
  python -m unittest discover -s tests

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
