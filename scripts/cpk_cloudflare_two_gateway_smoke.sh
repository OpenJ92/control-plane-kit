#!/bin/sh
set -eu

ENV_FILE="${CPK_CLOUDFLARE_ENV_FILE:-env/cloudflare.openj92.local.dev}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

if [ "${CPK_CLOUDFLARE_LIVE_ACCEPTANCE:-}" != "1" ]; then
  echo "set CPK_CLOUDFLARE_LIVE_ACCEPTANCE=1 to run live Cloudflare acceptance" >&2
  exit 1
fi

python3 scripts/cpk_cloudflare_two_gateway_smoke.py --env-file "$ENV_FILE"
