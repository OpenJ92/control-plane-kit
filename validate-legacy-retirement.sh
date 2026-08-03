#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "${1:-}" = "--require-deleted" ]; then
  deletion_argument="--require-deleted"
else
  deletion_argument=""
fi

python3 -m extraction_parity.retirement \
  --root "$ROOT_DIR" \
  --manifest artifacts/extraction/harden-tests-parity-1318-retirement-manifest.json \
  --evidence artifacts/extraction/harden-tests-parity-1318-evidence.json \
  ${deletion_argument:+"$deletion_argument"}
