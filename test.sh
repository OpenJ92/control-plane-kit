#!/usr/bin/env bash
set -euo pipefail
: "${PYTHON:=python3}"
"$PYTHON" -m unittest discover -s tests -v
