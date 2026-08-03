#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 -m extraction_parity.retirement \
  --root "$ROOT_DIR" \
  --promote-completed-owners

if [ -d "$ROOT_DIR/control_plane_kit" ]; then
  python3 -m extraction_parity.retirement \
    --root "$ROOT_DIR" \
    --manifest artifacts/extraction/harden-tests-parity-1318-retirement-manifest.json \
    --build \
    --evidence artifacts/extraction/harden-tests-parity-1318-evidence.json
else
  python3 -m extraction_parity.retirement \
    --root "$ROOT_DIR" \
    --manifest artifacts/extraction/harden-tests-parity-1318-retirement-manifest.json \
    --build \
    --require-deleted \
    --evidence artifacts/extraction/harden-tests-parity-1318-evidence.json
fi
