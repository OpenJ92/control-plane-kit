#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GIT_COMMON_DIR="$(git -C "$ROOT_DIR" rev-parse --path-format=absolute --git-common-dir)"
REPOSITORY_PARENT="$(dirname "$(dirname "$GIT_COMMON_DIR")")"
INTERPRETERS_REPO="${CPK_INTERPRETERS_REPO:-$REPOSITORY_PARENT/control-plane-kit-interpreters}"
SERVERS_REPO="${CPK_SERVERS_REPO:-$REPOSITORY_PARENT/control-plane-kit-servers}"
SECRETS_REPO="${CPK_SECRETS_REPO:-$REPOSITORY_PARENT/control-plane-kit-secrets}"
REFERENCE_TAG="${CPK_REFERENCE_TAG:-pre-server-product-extraction-2026-07-20}"
EXPECTED_REFERENCE_COMMIT="${CPK_REFERENCE_EXPECTED_COMMIT:-20129959d3b0f8e8bd5dbdafdf51c0a5d592a9ec}"
COORDINATION_REF="${CPK_COORDINATION_INVENTORY_REF:-f45384e72a79f59c93a715fd08f409f86a91218a}"
INTERPRETERS_REF="${CPK_INTERPRETERS_INVENTORY_REF:-2335a21adc5c0b0ae2f592bd15757c6ca1a55e4b}"
SERVERS_REF="${CPK_SERVERS_INVENTORY_REF:-43e9f359ca828c83fe4994ed1b62e1be54277ddd}"
SECRETS_REF="${CPK_SECRETS_INVENTORY_REF:-96e86dc3248d578780d64d5d7fc5d6359631d1d6}"
OUTPUT_PATH="${CPK_SEMANTIC_MIGRATION_INVENTORY_PATH:-$ROOT_DIR/artifacts/extraction/semantic-test-migration-inventory.json}"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cpk-semantic-test-inventory.XXXXXX")"

cleanup() {
  rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

archive_source() {
  local repository="$1"
  local reference="$2"
  local destination="$3"
  mkdir -p "$destination"
  git -C "$repository" archive "$reference" | tar -x -C "$destination"
}

reference_commit="$(git -C "$ROOT_DIR" rev-list -n 1 "$REFERENCE_TAG")"
if [ "$reference_commit" != "$EXPECTED_REFERENCE_COMMIT" ]; then
  printf 'Reference tag resolved to %s, expected %s\n' \
    "$reference_commit" "$EXPECTED_REFERENCE_COMMIT" >&2
  exit 1
fi

coordination_commit="$(git -C "$ROOT_DIR" rev-parse "$COORDINATION_REF")"
interpreters_commit="$(git -C "$INTERPRETERS_REPO" rev-parse "$INTERPRETERS_REF")"
servers_commit="$(git -C "$SERVERS_REPO" rev-parse "$SERVERS_REF")"
secrets_commit="$(git -C "$SECRETS_REPO" rev-parse "$SECRETS_REF")"

archive_source "$ROOT_DIR" "$REFERENCE_TAG" "$TEMP_DIR/reference"
archive_source "$ROOT_DIR" "$COORDINATION_REF" "$TEMP_DIR/coordination"
archive_source "$INTERPRETERS_REPO" "$INTERPRETERS_REF" "$TEMP_DIR/interpreters"
archive_source "$SERVERS_REPO" "$SERVERS_REF" "$TEMP_DIR/servers"
archive_source "$SECRETS_REPO" "$SECRETS_REF" "$TEMP_DIR/secrets"

cat >"$TEMP_DIR/source-commits.json" <<EOF
{
  "legacy-reference": "$reference_commit",
  "legacy-mutable": "$coordination_commit",
  "control-plane-kit-core": "$coordination_commit",
  "control-plane-kit-operations": "$coordination_commit",
  "control-plane-kit-interpreters": "$interpreters_commit",
  "control-plane-kit-servers": "$servers_commit",
  "control-plane-kit-secrets": "$secrets_commit"
}
EOF

mkdir -p "$(dirname "$OUTPUT_PATH")"
output_directory="$(cd "$(dirname "$OUTPUT_PATH")" && pwd)"

docker run --rm \
  -v "$ROOT_DIR/extraction_parity:/workspace/extraction_parity:ro" \
  -v "$ROOT_DIR/artifacts/extraction:/workspace/artifacts/extraction:ro" \
  -v "$TEMP_DIR/reference:/snapshots/reference:ro" \
  -v "$TEMP_DIR/coordination:/snapshots/coordination:ro" \
  -v "$TEMP_DIR/interpreters:/snapshots/interpreters:ro" \
  -v "$TEMP_DIR/servers:/snapshots/servers:ro" \
  -v "$TEMP_DIR/secrets:/snapshots/secrets:ro" \
  -v "$TEMP_DIR/source-commits.json:/workspace/source-commits.json:ro" \
  -v "$output_directory:/output" \
  -w /workspace \
  python:3.14-slim \
  python -m extraction_parity.migration_inventory \
    --reference-tests artifacts/extraction/reference-tests.json \
    --manifest artifacts/extraction/parity-manifest.json \
    --demos artifacts/extraction/reference-demos.json \
    --rules artifacts/extraction/semantic-test-migration-rules.json \
    --reference-root /snapshots/reference \
    --coordination-root /snapshots/coordination \
    --interpreters-root /snapshots/interpreters \
    --servers-root /snapshots/servers \
    --secrets-root /snapshots/secrets \
    --source-commits source-commits.json \
    --output "/output/$(basename "$OUTPUT_PATH")"

printf 'Semantic test migration inventory written to %s\n' "$OUTPUT_PATH"
