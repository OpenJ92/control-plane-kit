#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLICY="${1:-foundation}"

docker run --rm \
  -v "$ROOT_DIR:/workspace" \
  -w /workspace \
  python:3.14-slim \
  python -m extraction_parity.validation \
    --manifest artifacts/extraction/parity-manifest.json \
    --ownership artifacts/extraction/reference-law-ownership.json \
    --demos artifacts/extraction/reference-demos.json \
    --evidence artifacts/extraction/successor-evidence.json \
    --report artifacts/extraction/parity-validation-report.json \
    --policy "$POLICY"
