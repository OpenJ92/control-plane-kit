#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="control-plane-kit-test:local"
EVIDENCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cpk-differential-live.XXXXXX")"

cleanup() {
  rm -rf "$EVIDENCE_DIR"
}
trap cleanup EXIT

docker build --target test -t "$IMAGE" "$ROOT_DIR" >/dev/null

run_capture() {
  local role="$1"
  local identity="$2"
  local port="$3"
  local output="$4"
  local artifact="$5"

  docker run --rm \
    --name "cpk-differential-${role}-$$" \
    -v "$EVIDENCE_DIR:/evidence" \
    -e "SCENARIO_PORT=$port" \
    -e "SCENARIO_RESPONSE=hello" \
    -e "SCENARIO_ARTIFACT_PATH=/evidence/$artifact" \
    "$IMAGE" \
    python -m extraction_parity.runner capture \
      --role "$role" \
      --identity "$identity" \
      --source-digest "sha256:$(printf '%064d' 1)" \
      --output "/evidence/$output" \
      --artifact response text/plain "/evidence/$artifact" \
      -- \
      python tests/fixtures/differential_scenario.py >/dev/null
}

run_capture reference reference-live 49152 reference.json reference-artifact.txt
run_capture successor successor-live 49153 successor.json successor-artifact.txt

cat >"$EVIDENCE_DIR/policy.json" <<'JSON'
{
  "schema": "cpk.normalization-policy",
  "allowed": ["allocated-port"]
}
JSON

docker run --rm \
  --name "cpk-differential-compare-$$" \
  -v "$EVIDENCE_DIR:/evidence" \
  "$IMAGE" \
  python -m extraction_parity.runner compare \
    --reference /evidence/reference.json \
    --successor /evidence/successor.json \
    --policy /evidence/policy.json \
    --result /evidence/result.json \
    --evidence /evidence/evidence.json \
    --evidence-id differential-live-proof >/dev/null

docker run --rm -i \
  --name "cpk-differential-assert-$$" \
  -v "$EVIDENCE_DIR:/evidence" \
  "$IMAGE" \
  python - /evidence/result.json /evidence/evidence.json <<'PY'
import json
import sys

result = json.load(open(sys.argv[1], encoding="utf-8"))
evidence = json.load(open(sys.argv[2], encoding="utf-8"))
assert result["status"] == "equivalent", result
assert result["differences"] == [], result
assert evidence["evidence"][0]["status"] == "passing", evidence
print("Differential live proof passed: reference and successor differ only by allocated port.")
PY
