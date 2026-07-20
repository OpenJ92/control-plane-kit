#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker run --rm -v "$ROOT_DIR:/workspace" -w /workspace python:3.14-slim \
  python -c 'import json; from pathlib import Path; from extraction_parity.manifest import build_manifest, write_manifest; root=Path("artifacts/extraction"); load=lambda name: json.loads((root/name).read_text()); write_manifest(root/"parity-manifest.json", build_manifest(load("reference-law-ownership.json"), load("reference-demos.json")))'
