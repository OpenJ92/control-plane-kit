#!/bin/sh
set -eu

IMAGE="$(python3 scripts/product_image_coordinate.py http-multiplexer)"

docker pull "$IMAGE" >/dev/null
HTTP_MULTIPLEXER_IMAGE="$IMAGE" HTTP_MULTIPLEXER_BUILD_IMAGE=0 \
  scripts/http_multiplexer_image_smoke.sh
