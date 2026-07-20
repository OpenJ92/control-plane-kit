#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REFERENCE_TAG="${CPK_REFERENCE_TAG:-pre-server-product-extraction-2026-07-20}"
EXPECTED_COMMIT="${CPK_REFERENCE_EXPECTED_COMMIT:-20129959d3b0f8e8bd5dbdafdf51c0a5d592a9ec}"
OUTPUT_PATH="${CPK_REFERENCE_INVENTORY_PATH:-$ROOT_DIR/artifacts/extraction/reference-tests.json}"
OVERRIDES_PATH="${CPK_REFERENCE_LAW_OVERRIDES:-$ROOT_DIR/artifacts/extraction/law-overrides.json}"
IMAGE="cpk-reference-inventory-${EXPECTED_COMMIT:0:12}:local"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cpk-inventory.XXXXXX")"

cleanup() {
  docker image rm -f "$IMAGE" >/dev/null 2>&1 || true
  rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

REFERENCE_COMMIT="$(git -C "$ROOT_DIR" rev-list -n 1 "$REFERENCE_TAG")"
if [ "$REFERENCE_COMMIT" != "$EXPECTED_COMMIT" ]; then
  printf 'Reference tag resolved to %s, expected %s\n' \
    "$REFERENCE_COMMIT" "$EXPECTED_COMMIT" >&2
  exit 1
fi

mkdir -p "$TEMP_DIR/source" "$(dirname "$OUTPUT_PATH")"
git -C "$ROOT_DIR" archive "$REFERENCE_TAG" | tar -x -C "$TEMP_DIR/source"
docker build --target test -t "$IMAGE" "$TEMP_DIR/source" >/dev/null

OUTPUT_DIR="$(cd "$(dirname "$OUTPUT_PATH")" && pwd)"
docker run --rm \
  -v "$ROOT_DIR/extraction_parity:/parity/extraction_parity:ro" \
  -v "$OVERRIDES_PATH:/parity/law-overrides.json:ro" \
  -v "$OUTPUT_DIR:/evidence" \
  -e PYTHONPATH=/parity \
  -w /app \
  "$IMAGE" \
  python -m extraction_parity.inventory \
    --reference-tag "$REFERENCE_TAG" \
    --reference-commit "$REFERENCE_COMMIT" \
    --overrides /parity/law-overrides.json \
    --output "/evidence/$(basename "$OUTPUT_PATH")"

printf 'Frozen test inventory written to %s\n' "$OUTPUT_PATH"
