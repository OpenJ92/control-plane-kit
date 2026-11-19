#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
cd "$ROOT"
if [ "${1:-}" = "--unit-only" ]; then
  [ "$#" = 1 ] || { echo 'Usage: --unit-only (no backend report)' >&2; exit 2; }
  echo 'Focused Current Backend tooling tests; not backend composition acceptance.'
  image="control-plane-kit-current-backend-unit:local"
  docker build -f current_backend/Dockerfile.test -t "$image" .
  exec docker run --rm --network none -v "$ROOT:/source:ro" "$image"
fi
exec python3 -m current_backend.runner "$@"
