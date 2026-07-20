#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REFERENCE_TAG="${CPK_REFERENCE_TAG:-pre-server-product-extraction-2026-07-20}"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cpk-demos.XXXXXX")"
trap 'rm -rf "$TEMP_DIR"' EXIT

git -C "$ROOT_DIR" ls-tree -r "$REFERENCE_TAG" \
  | awk '$1 == "100755" {print $4}' \
  | sort >"$TEMP_DIR/scripts.txt"

git -C "$ROOT_DIR" ls-tree -r --name-only "$REFERENCE_TAG" \
  | awk '
      /^tests\/live_.*\.py$/ {print; next}
      /^examples\/.*_live\.py$/ {print; next}
      $0 == "examples/gate_d_live_smoke.py" {print; next}
      $0 == "examples/read_interface_demo_server.py" {print; next}
      $0 == "examples/backend_swap_planning.py" {print; next}
      $0 == "examples/router_swap.py" {print; next}
    ' \
  | sort -u >"$TEMP_DIR/fixtures.txt"

docker run --rm \
  -v "$ROOT_DIR:/workspace:ro" \
  -v "$TEMP_DIR:/discovery:ro" \
  -w /workspace \
  python:3.14-slim \
  python -c 'import json; from pathlib import Path; from extraction_parity.demos import validate_demo_inventory; lines=lambda p: frozenset(Path(p).read_text().splitlines()); validate_demo_inventory(json.loads(Path("artifacts/extraction/reference-demos.json").read_text()), discovered_scripts=lines("/discovery/scripts.txt"), discovered_fixtures=lines("/discovery/fixtures.txt"))'

printf 'Frozen demo inventory is exhaustive and valid\n'
