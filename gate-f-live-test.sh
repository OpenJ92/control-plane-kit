#!/usr/bin/env bash
set -euo pipefail

# Gate F composes the focused host-publication proof with the complete
# Postgres-backed blue/green deployment transition. Each child harness owns and
# cleans only resources carrying its exact test labels.
./live-test.sh
./gate-d-live-test.sh

echo "Gate F live proof passed: publication, auth, deployment switch, and cleanup."
