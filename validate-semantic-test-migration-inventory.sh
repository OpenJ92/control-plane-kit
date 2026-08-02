#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPECTED="$ROOT_DIR/artifacts/extraction/semantic-test-migration-inventory.json"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cpk-semantic-test-inventory-check.XXXXXX")"

cleanup() {
  rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

CPK_SEMANTIC_MIGRATION_INVENTORY_PATH="$TEMP_DIR/inventory.json" \
  "$ROOT_DIR/build-semantic-test-migration-inventory.sh" >/dev/null

if ! cmp -s "$EXPECTED" "$TEMP_DIR/inventory.json"; then
  printf 'Semantic test migration inventory is stale; regenerate it with:\n' >&2
  printf '  ./build-semantic-test-migration-inventory.sh\n' >&2
  exit 1
fi

printf 'Semantic test migration inventory is current\n'
