#!/bin/sh
set -eu

SERVERS_REPO="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
COORDINATES_FILE="$SERVERS_REPO/coordinates/server-products.json"
CONTROLLER_IMAGE="${CPK_SERVERS_TEST_IMAGE:-control-plane-kit-servers-test:published-1206}"
DIND_IMAGE="${CPK_REMOTE_TLS_DIND_IMAGE:-docker.io/library/docker@sha256:aa3df78ecf320f5fafdce71c659f1629e96e9de0968305fe1de670e0ca9176ce}"

if [ "${CPK_SECRET_CONSUMERS_PUBLISHED_LIVE_ACCEPTANCE:-}" != "1" ]; then
  echo "set CPK_SECRET_CONSUMERS_PUBLISHED_LIVE_ACCEPTANCE=1 to run published secret-consumer acceptance" >&2
  exit 1
fi

coordinate_image() {
  product_id="$1"
  PRODUCT_ID="$product_id" COORDINATES_FILE="$COORDINATES_FILE" python3 -c '
import json
import os
from pathlib import Path

document = json.loads(
    Path(os.environ["COORDINATES_FILE"]).read_text(encoding="utf-8")
)
matches = [
    product
    for product in document["products"]
    if product["product_id"] == os.environ["PRODUCT_ID"]
]
if len(matches) != 1:
    raise SystemExit("expected exactly one product coordinate")
image = matches[0]["image"]
print("{}/{}@{}".format(image["registry"], image["repository"], image["digest"]))
'
}

require_digest() {
  name="$1"
  image="$2"
  case "$image" in
    *@sha256:*) ;;
    *)
      echo "$name must use an immutable sha256 digest" >&2
      exit 1
      ;;
  esac
}

CPK_IMAGE="$(coordinate_image cpk-server)"
SECRETS_IMAGE="$(coordinate_image secrets-server)"
GATEWAY_IMAGE="$(coordinate_image cpk-local-gateway)"
CLOUDFLARED_IMAGE="$(coordinate_image cloudflared-connector)"
POSTGRES_IMAGE="$(coordinate_image postgres-server)"
HELLO_IMAGE="$(coordinate_image hello-server)"

require_digest cpk-server "$CPK_IMAGE"
require_digest secrets-server "$SECRETS_IMAGE"
require_digest cpk-local-gateway "$GATEWAY_IMAGE"
require_digest cloudflared-connector "$CLOUDFLARED_IMAGE"
require_digest postgres-server "$POSTGRES_IMAGE"
require_digest hello-server "$HELLO_IMAGE"
require_digest docker-dind "$DIND_IMAGE"

if [ "${CPK_SECRET_CONSUMERS_PUBLISHED_LIVE_PLAN_ONLY:-}" = "1" ]; then
  printf '%s\n' \
    "$CPK_IMAGE" \
    "$SECRETS_IMAGE" \
    "$GATEWAY_IMAGE" \
    "$CLOUDFLARED_IMAGE" \
    "$POSTGRES_IMAGE" \
    "$HELLO_IMAGE" \
    "$DIND_IMAGE"
  exit 0
fi

for image in \
  "$CPK_IMAGE" \
  "$SECRETS_IMAGE" \
  "$GATEWAY_IMAGE" \
  "$CLOUDFLARED_IMAGE" \
  "$POSTGRES_IMAGE" \
  "$HELLO_IMAGE" \
  "$DIND_IMAGE"
do
  docker pull "$image" >/dev/null
done

# The controller is test orchestration only. All realized product/process images
# above are immutable published artifacts and all underlying builds are disabled.
docker build -f "$SERVERS_REPO/Dockerfile.test" -t "$CONTROLLER_IMAGE" "$SERVERS_REPO"

export CPK_SERVERS_TEST_IMAGE="$CONTROLLER_IMAGE"
export CPK_SECRETS_TEST_IMAGE="$SECRETS_IMAGE"
export CPK_LIVE_POSTGRES_IMAGE="$POSTGRES_IMAGE"
export CPK_REMOTE_TLS_DIND_IMAGE="$DIND_IMAGE"
export CPK_SECRET_PROVIDER_SERVER_IMAGE="$CPK_IMAGE"
export CPK_CLOUDFLARE_CUSTODY_SERVER_IMAGE="$CPK_IMAGE"
export CPK_REMOTE_TLS_CUSTODY_SERVER_IMAGE="$CPK_IMAGE"
export CPK_SECRET_PROVIDER_BUILD_IMAGES=0
export CPK_CLOUDFLARE_CUSTODY_BUILD_IMAGES=0
export CPK_REMOTE_TLS_CUSTODY_BUILD_IMAGES=0

sh "$SERVERS_REPO/scripts/cpk_server_secret_provider_source_live_smoke.sh"
sh "$SERVERS_REPO/scripts/cpk_server_cloudflare_secret_custody_source_live_smoke.sh"
sh "$SERVERS_REPO/scripts/cpk_server_remote_tls_secret_custody_source_live_smoke.sh"
sh "$SERVERS_REPO/scripts/docker_residue_audit.sh"

echo "cpk-server published secret-consumer acceptance passed"
