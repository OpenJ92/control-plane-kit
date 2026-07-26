#!/bin/sh
set -eu

IMAGE="$(python3 - <<'PY'
import json
from pathlib import Path

image = json.loads(Path("products/hello_server/product.cpk.json").read_text())["product"]["image"]
print(f"{image['registry']}/{image['repository']}@{image['digest']}")
PY
)"

docker pull "$IMAGE"

HELLO_SERVER_IMAGE="$IMAGE" \
HELLO_SERVER_BUILD_IMAGE=0 \
  scripts/hello_server_image_smoke.sh
