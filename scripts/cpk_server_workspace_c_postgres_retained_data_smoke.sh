#!/bin/sh
set -eu

CPK_HOSTED_ACTIVITY_SCENARIO=workspace-c-postgres-retained-data \
  scripts/cpk_server_hosted_activity_smoke.sh
