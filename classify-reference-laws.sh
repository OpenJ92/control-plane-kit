#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker run --rm \
  -v "$ROOT_DIR:/workspace" \
  -w /workspace \
  python:3.14-slim \
  python -c 'import json; from pathlib import Path; from extraction_parity.ownership import classify_inventory, write_ownership; root=Path("/workspace/artifacts/extraction"); write_ownership(root / "reference-law-ownership.json", classify_inventory(json.loads((root / "reference-tests.json").read_text()), json.loads((root / "ownership-rules.json").read_text())))'
