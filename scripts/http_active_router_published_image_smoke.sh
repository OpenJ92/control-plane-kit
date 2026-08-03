#!/bin/sh
set -eu

IMAGE="$(python3 scripts/product_image_coordinate.py http-active-router)"

docker pull "$IMAGE" >/dev/null
HTTP_ACTIVE_ROUTER_IMAGE="$IMAGE" HTTP_ACTIVE_ROUTER_BUILD_IMAGE=0 \
  scripts/http_active_router_image_smoke.sh
