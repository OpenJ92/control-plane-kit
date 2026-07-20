#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REFERENCE_TAG="${CPK_REFERENCE_TAG:-pre-server-product-extraction-2026-07-20}"
EXPECTED_COMMIT="${CPK_REFERENCE_EXPECTED_COMMIT:-20129959d3b0f8e8bd5dbdafdf51c0a5d592a9ec}"
PYTHON_IMAGE="${CPK_REFERENCE_PYTHON_IMAGE:-python:3.14-slim}"
MAXIMUM_OUTPUT_BYTES="${CPK_REFERENCE_MAXIMUM_OUTPUT_BYTES:-8388608}"
EVIDENCE_PATH="${CPK_REFERENCE_EVIDENCE_PATH:-$ROOT_DIR/artifacts/extraction/reference-baseline.json}"
RUN_ID="${CPK_REFERENCE_RUN_ID:-${EXPECTED_COMMIT:0:12}}"
TEST_IMAGE="cpk-reference-test-${RUN_ID}:local"
NETWORK_NAME="cpk-reference-${RUN_ID}"
POSTGRES_CONTAINER="cpk-reference-postgres-${RUN_ID}"
TEST_CONTAINER="cpk-reference-runner-${RUN_ID}"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cpk-reference.XXXXXX")"

cleanup() {
  docker image rm -f "$TEST_IMAGE" >/dev/null 2>&1 || true
  rm -rf "$TEMP_DIR"
}

trap cleanup EXIT

snapshot_resources() {
  local suffix="$1"
  docker ps -aq --no-trunc | sort >"$TEMP_DIR/containers-$suffix.txt"
  docker network ls -q --no-trunc | sort >"$TEMP_DIR/networks-$suffix.txt"
  docker volume ls -q | sort >"$TEMP_DIR/volumes-$suffix.txt"
}

REFERENCE_COMMIT="$(git -C "$ROOT_DIR" rev-list -n 1 "$REFERENCE_TAG")"
if [ "$REFERENCE_COMMIT" != "$EXPECTED_COMMIT" ]; then
  printf 'Reference tag %s resolved to %s, expected %s\n' \
    "$REFERENCE_TAG" "$REFERENCE_COMMIT" "$EXPECTED_COMMIT" >&2
  exit 1
fi

mkdir -p "$TEMP_DIR/source" "$(dirname "$EVIDENCE_PATH")"
git -C "$ROOT_DIR" archive "$REFERENCE_TAG" | tar -x -C "$TEMP_DIR/source"

snapshot_resources before

if ! (
  cd "$TEMP_DIR/source"
  CPK_TEST_IMAGE_NAME="$TEST_IMAGE" \
  CPK_TEST_NETWORK_NAME="$NETWORK_NAME" \
  CPK_TEST_POSTGRES_CONTAINER="$POSTGRES_CONTAINER" \
  CPK_TEST_CONTAINER="$TEST_CONTAINER" \
    ./test.sh >"$TEMP_DIR/test-output.txt" 2>&1
); then
  tail -n 200 "$TEMP_DIR/test-output.txt" >&2
  exit 1
fi

docker run --rm "$TEST_IMAGE" \
  python -m compileall -q control_plane_kit tests examples

PYTHON_IMAGE_ID="$(docker image inspect "$PYTHON_IMAGE" --format '{{.Id}}')"
POSTGRES_IMAGE_ID="$(docker image inspect postgres:16-alpine --format '{{.Id}}')"
TEST_IMAGE_ID="$(docker image inspect "$TEST_IMAGE" --format '{{.Id}}')"

snapshot_resources after

comm -13 "$TEMP_DIR/volumes-before.txt" "$TEMP_DIR/volumes-after.txt" \
  >"$TEMP_DIR/owned-volume-candidates.txt"
while IFS= read -r volume_id; do
  [ -n "$volume_id" ] || continue
  if [ -n "$(docker ps -aq --filter "volume=$volume_id")" ]; then
    printf 'Run-local volume %s is still attached; refusing cleanup\n' "$volume_id" >&2
    exit 1
  fi
  docker volume rm "$volume_id" >/dev/null
done <"$TEMP_DIR/owned-volume-candidates.txt"

mv "$TEMP_DIR/volumes-after.txt" "$TEMP_DIR/volumes-observed.txt"
snapshot_resources after

EVIDENCE_DIR="$(cd "$(dirname "$EVIDENCE_PATH")" && pwd)"
EVIDENCE_NAME="$(basename "$EVIDENCE_PATH")"
docker run --rm \
  -v "$ROOT_DIR:/tool:ro" \
  -v "$TEMP_DIR:/run:ro" \
  -v "$EVIDENCE_DIR:/evidence" \
  -w /tool \
  "$PYTHON_IMAGE" \
  python -m extraction_parity.reference \
    --reference-tag "$REFERENCE_TAG" \
    --reference-commit "$REFERENCE_COMMIT" \
    --expected-commit "$EXPECTED_COMMIT" \
    --python-image "$PYTHON_IMAGE" \
    --python-image-id "$PYTHON_IMAGE_ID" \
    --postgres-image-id "$POSTGRES_IMAGE_ID" \
    --test-image-id "$TEST_IMAGE_ID" \
    --dependency-input /run/source/pyproject.toml \
    --dependency-input /run/source/Dockerfile \
    --dependency-input /run/source/test.sh \
    --dependency-input /run/source/packaging-test.sh \
    --test-output /run/test-output.txt \
    --maximum-output-bytes "$MAXIMUM_OUTPUT_BYTES" \
    --containers-before /run/containers-before.txt \
    --containers-after /run/containers-after.txt \
    --networks-before /run/networks-before.txt \
    --networks-after /run/networks-after.txt \
    --volumes-before /run/volumes-before.txt \
    --volumes-observed /run/volumes-observed.txt \
    --volumes-after /run/volumes-after.txt \
    --evidence "/evidence/$EVIDENCE_NAME"

printf 'Frozen reference evidence written to %s\n' "$EVIDENCE_PATH"
