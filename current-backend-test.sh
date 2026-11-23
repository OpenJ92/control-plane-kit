#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
cd "$ROOT"
if [ "${1:-}" = "--unit-only" ]; then
  [ "$#" = 1 ] || { echo 'Usage: --unit-only (no backend report)' >&2; exit 2; }
  echo 'Focused Current Backend tooling tests; not backend composition acceptance.'
  unit_directory="$(mktemp -d)"
  trap 'rm -f "$unit_directory/image"; rmdir "$unit_directory"' EXIT
  docker build -f current_backend/Dockerfile.test --iidfile "$unit_directory/image" current_backend
  image="$(cat "$unit_directory/image")"
  docker run --rm --network none -v "$ROOT:/source:ro" "$image"
  exit "$?"
fi
exec python3 -m current_backend.runner "$@"
