#!/bin/sh
set -eu

CPK_HOSTED_ACTIVITY_SCENARIO=seeded-stress-public-ingress \
  scripts/cpk_server_hosted_activity_smoke.sh
