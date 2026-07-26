#!/bin/sh
set -eu

image_from_descriptor() {
  product_path="$1"
  python3 - "$product_path" <<'PY'
import json
import sys
from pathlib import Path

image = json.loads(Path(sys.argv[1]).read_text())["product"]["image"]
print(f"{image['registry']}/{image['repository']}@{image['digest']}")
PY
}

find_port() {
  python3 - <<'PY'
import socket

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

GATEWAY_IMAGE="${CPK_LOCAL_GATEWAY_IMAGE:-$(image_from_descriptor products/cpk_local_gateway/product.cpk.json)}"
HELLO_IMAGE="${CPK_HELLO_SERVER_IMAGE:-$(image_from_descriptor products/hello_server/product.cpk.json)}"
POSTGRES_IMAGE="${CPK_POSTGRES_SERVER_IMAGE:-$(image_from_descriptor products/postgres_server/product.cpk.json)}"
NETWORK="cpk-local-gateway-probe-$$"
LABEL="org.openj92.project=control-plane-kit-servers"
OWNED_LABEL="org.openj92.cpk.workspace=local-gateway-private-probe"
GATEWAY_CONTAINER=""
HELLO_CONTAINER=""
POSTGRES_CONTAINER=""
GATEWAY_PORT="${CPK_LOCAL_GATEWAY_PORT:-$(find_port)}"

cleanup() {
  for container in "$GATEWAY_CONTAINER" "$HELLO_CONTAINER" "$POSTGRES_CONTAINER"; do
    if [ -n "$container" ]; then
      docker rm -f "$container" >/dev/null 2>&1 || true
    fi
  done
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker pull "$GATEWAY_IMAGE" >/dev/null
docker pull "$HELLO_IMAGE" >/dev/null
docker pull "$POSTGRES_IMAGE" >/dev/null
docker network create --label "$LABEL" --label "$OWNED_LABEL" "$NETWORK" >/dev/null

POSTGRES_CONTAINER="$(docker run -d \
  --label "$LABEL" \
  --label "$OWNED_LABEL" \
  --network "$NETWORK" \
  --network-alias postgres \
  -e POSTGRES_DB=cpk \
  -e POSTGRES_USER=cpk \
  -e POSTGRES_PASSWORD=cpk \
  "$POSTGRES_IMAGE")"

HELLO_CONTAINER="$(docker run -d \
  --label "$LABEL" \
  --label "$OWNED_LABEL" \
  --network "$NETWORK" \
  --network-alias hello \
  -e HELLO_MESSAGE="Gateway hello" \
  "$HELLO_IMAGE")"

POSTGRES_READY=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if docker exec "$POSTGRES_CONTAINER" psql -U cpk -d cpk -c 'SELECT 1' >/dev/null 2>&1; then
    POSTGRES_READY=1
    break
  fi
  sleep 1
done

if [ "$POSTGRES_READY" != "1" ]; then
  echo "private postgres target did not become query-ready" >&2
  exit 1
fi

TARGETS_JSON='{"hello.http":{"protocol":"http","url":"http://hello:8000"},"postgres.postgres":{"protocol":"postgres","host":"postgres","port":5432,"database":"cpk","username":"cpk","password_environment":"POSTGRES_PASSWORD"}}'

GATEWAY_CONTAINER="$(docker run -d \
  --label "$LABEL" \
  --label "$OWNED_LABEL" \
  --network "$NETWORK" \
  --network-alias gateway \
  -p "127.0.0.1:$GATEWAY_PORT:8000" \
  -e CPK_GATEWAY_TARGETS_JSON="$TARGETS_JSON" \
  -e POSTGRES_PASSWORD=cpk \
  "$GATEWAY_IMAGE")"

BASE_URL="http://127.0.0.1:$GATEWAY_PORT"
GATEWAY_READY=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if curl -fsS "$BASE_URL/health/ready" >/tmp/cpk-local-gateway-ready.json 2>/dev/null; then
    GATEWAY_READY=1
    break
  fi
  sleep 1
done

if [ "$GATEWAY_READY" != "1" ]; then
  echo "gateway did not become ready" >&2
  docker logs "$GATEWAY_CONTAINER" >&2 || true
  exit 1
fi

curl -fsS "$BASE_URL/health/live" >/tmp/cpk-local-gateway-live.json

HTTP_RESULT="$(curl -fsS \
  -H 'Content-Type: application/json' \
  -d '{"kind":"http-status","target_id":"hello.http","path":"/health/ready"}' \
  "$BASE_URL/cpk/probes")"
POSTGRES_RESULT="$(curl -fsS \
  -H 'Content-Type: application/json' \
  -d '{"kind":"postgres-select-one","target_id":"postgres.postgres"}' \
  "$BASE_URL/cpk/probes")"

HTTP_RESULT="$HTTP_RESULT" POSTGRES_RESULT="$POSTGRES_RESULT" python3 - <<'PY'
import json
import os

http_result = json.loads(os.environ["HTTP_RESULT"])
postgres_result = json.loads(os.environ["POSTGRES_RESULT"])
if http_result.get("outcome") != "passed" or http_result.get("status") != 200:
    raise SystemExit(f"HTTP private probe failed: {http_result}")
if postgres_result.get("outcome") != "passed":
    raise SystemExit(f"Postgres private probe failed: {postgres_result}")
combined = json.dumps([http_result, postgres_result]).lower()
if "cpk" in combined or "password" in combined or "secret" in combined:
    raise SystemExit("probe result leaked secret-shaped material")
PY

UNKNOWN_STATUS="$(curl -sS -o /tmp/cpk-local-gateway-unknown.json -w '%{http_code}' \
  -H 'Content-Type: application/json' \
  -d '{"kind":"http-status","target_id":"missing.http","path":"/health/ready"}' \
  "$BASE_URL/cpk/probes")"
UNSUPPORTED_STATUS="$(curl -sS -o /tmp/cpk-local-gateway-unsupported.json -w '%{http_code}' \
  -H 'Content-Type: application/json' \
  -d '{"kind":"tcp-open","target_id":"hello.http"}' \
  "$BASE_URL/cpk/probes")"

if [ "$UNKNOWN_STATUS" != "400" ]; then
  echo "unknown gateway target did not fail closed: $UNKNOWN_STATUS" >&2
  exit 1
fi

if [ "$UNSUPPORTED_STATUS" != "400" ]; then
  echo "unsupported gateway probe did not fail closed: $UNSUPPORTED_STATUS" >&2
  exit 1
fi

cleanup
GATEWAY_CONTAINER=""
HELLO_CONTAINER=""
POSTGRES_CONTAINER=""

echo "cpk-local-gateway private probe smoke passed"
