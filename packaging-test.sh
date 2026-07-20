#!/usr/bin/env bash
set -euo pipefail

images=(
  "control-plane-kit-package-base:local"
  "control-plane-kit-package-http:local"
  "control-plane-kit-package-postgres:local"
  "control-plane-kit-package-server:local"
)

cleanup() {
  docker image rm -f "${images[@]}" >/dev/null 2>&1 || true
}

trap cleanup EXIT
cleanup

docker build --target base-wheel-test -t "${images[0]}" .
docker build --target http-wheel-test -t "${images[1]}" .
docker build --target postgres-wheel-test -t "${images[2]}" .
docker build --target server-wheel-test -t "${images[3]}" .
