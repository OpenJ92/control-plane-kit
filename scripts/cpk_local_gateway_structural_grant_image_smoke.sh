#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
IMAGE="${CPK_LOCAL_GATEWAY_IMAGE:-}"
BUILD_SOURCE="${CPK_LOCAL_GATEWAY_STRUCTURAL_BUILD_SOURCE:-0}"

if [ -z "$IMAGE" ]; then
  IMAGE="$(
    python3 - "$ROOT/products/cpk_local_gateway/product.cpk.json" <<'PY'
import json
import sys
from pathlib import Path

image = json.loads(Path(sys.argv[1]).read_text())["product"]["image"]
print(f"{image['registry']}/{image['repository']}@{image['digest']}")
PY
  )"
fi

if [ "$BUILD_SOURCE" = "1" ]; then
  IMAGE="${CPK_LOCAL_GATEWAY_IMAGE:-control-plane-kit-servers/cpk-local-gateway:structural-source}"
  docker build \
    -f "$ROOT/products/cpk_local_gateway/Dockerfile" \
    -t "$IMAGE" \
    "$ROOT"
else
  docker pull "$IMAGE" >/dev/null
fi

docker run --rm \
  --entrypoint python \
  -v "$ROOT/scripts/cpk_local_gateway_structural_grant_check.py:/tmp/check.py:ro" \
  "$IMAGE" \
  /tmp/check.py
