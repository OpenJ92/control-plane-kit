#!/bin/sh
set -eu

IMAGE="${CPK_SERVER_IMAGE:-localhost/control-plane-kit-servers/cpk-server:local}"
BUILD_IMAGE="${CPK_SERVER_BUILD_IMAGE:-1}"
CONTAINER=""
POSTGRES_CONTAINER=""
NETWORK="cpk-server-smoke-$$"
PHASE="bootstrap"
LABEL="org.openj92.project=control-plane-kit-servers"
MISSING_CONFIG_OUTPUT="/tmp/cpk-server-missing-config-$$.out"
IMPORT_BODY="/tmp/cpk-server-import-product-$$.json"
DATABASE_URL="${CPK_DATABASE_URL:-postgresql://cpk:cpk@cpk-postgres:5432/cpk}"
WORKPLACE_DATABASE_URL="${CPK_WORKPLACE_DATABASE_URL:-$DATABASE_URL}"
ACTIVITY_HISTORY_DATABASE_URL="${CPK_ACTIVITY_HISTORY_DATABASE_URL:-$DATABASE_URL}"
OBSERVER_STATE_DATABASE_URL="${CPK_OBSERVER_STATE_DATABASE_URL:-$DATABASE_URL}"
GRAPH_TOPOLOGY_DATABASE_URL="${CPK_GRAPH_TOPOLOGY_DATABASE_URL:-$DATABASE_URL}"
RUNTIME_INTERPRETERS="${CPK_RUNTIME_INTERPRETERS:-none}"
STATIC_WORKSPACE_GRANTS_JSON="${CPK_CONTROL_AUTH_STATIC_WORKSPACE_GRANTS_JSON:-{\"workspace-a\":[\"hub:instance:create\",\"instance:workspace:read\",\"instance:workspace:edit\",\"plan:request\"]}}"
HEALTH_ATTEMPTS="${CPK_SERVER_HEALTH_ATTEMPTS:-30}"

cleanup() {
  rm -f "$MISSING_CONFIG_OUTPUT" "$IMPORT_BODY"
  if [ -n "$CONTAINER" ]; then
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi
  if [ -n "$POSTGRES_CONTAINER" ]; then
    docker rm -f "$POSTGRES_CONTAINER" >/dev/null 2>&1 || true
  fi
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}

finish() {
  status=$?
  trap - EXIT INT TERM
  if [ "$status" -ne 0 ]; then
    echo "cpk-server image smoke failed during phase: $PHASE" >&2
    if [ -n "$CONTAINER" ]; then
      echo "cpk-server bounded log tail:" >&2
      docker logs --tail 80 "$CONTAINER" >&2 2>/dev/null || true
    fi
    if [ -n "$POSTGRES_CONTAINER" ]; then
      echo "postgres bounded log tail:" >&2
      docker logs --tail 40 "$POSTGRES_CONTAINER" >&2 2>/dev/null || true
    fi
  fi
  cleanup
  exit "$status"
}

phase() {
  PHASE="$1"
  echo "cpk-server image smoke: $PHASE"
}

curl_with_retry() {
  url="$1"
  output=""
  attempt=1
  while [ "$attempt" -le "$HEALTH_ATTEMPTS" ]; do
    if output="$(curl -fsS "$url" 2>/dev/null)"; then
      printf '%s' "$output"
      return 0
    fi
    sleep 1
    attempt=$((attempt + 1))
  done
  return 1
}

trap finish EXIT
trap 'exit 130' INT TERM

phase "build product image"
if [ "$BUILD_IMAGE" = "1" ]; then
  docker build -f products/cpk_server/Dockerfile -t "$IMAGE" .
fi

phase "reject missing bootstrap configuration"
if docker run --rm "$IMAGE" >"$MISSING_CONFIG_OUTPUT" 2>&1; then
  echo "cpk-server started without required configuration" >&2
  exit 1
fi

phase "verify non-root image contract"
docker inspect "$IMAGE" --format '{{.Config.User}}' | grep -q '^cpk$'

phase "create owned runtime network"
docker network create "$NETWORK" >/dev/null

phase "start Postgres dependency"
POSTGRES_CONTAINER="$(docker run -d \
  --label "$LABEL" \
  --network "$NETWORK" \
  --network-alias cpk-postgres \
  -e POSTGRES_DB=cpk \
  -e POSTGRES_USER=cpk \
  -e POSTGRES_PASSWORD=cpk \
  postgres:16-alpine)"

phase "wait for Postgres semantic readiness"
POSTGRES_READY=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if docker exec "$POSTGRES_CONTAINER" psql -U cpk -d cpk -c 'SELECT 1' >/dev/null 2>&1; then
    POSTGRES_READY=1
    break
  fi
  sleep 1
done

if [ "$POSTGRES_READY" != "1" ]; then
  echo "postgres did not become query-ready" >&2
  exit 1
fi

phase "start configured cpk-server"
CONTAINER="$(docker run -d \
  --label "$LABEL" \
  --network "$NETWORK" \
  -p 127.0.0.1::8080 \
  -e CPK_SERVER_MODE=execution-capable \
  -e CPK_CONTROL_AUTH_VERIFIER=static-development \
  -e CPK_CONTROL_AUTH_STATIC_CREDENTIAL=valid-token \
  -e CPK_CONTROL_AUTH_STATIC_WORKSPACE_GRANTS_JSON="$STATIC_WORKSPACE_GRANTS_JSON" \
  -e CPK_PORT=8080 \
  -e CPK_RUNTIME_INTERPRETERS="$RUNTIME_INTERPRETERS" \
  -e CPK_WORKPLACE_DATABASE_URL="$WORKPLACE_DATABASE_URL" \
  -e CPK_ACTIVITY_HISTORY_DATABASE_URL="$ACTIVITY_HISTORY_DATABASE_URL" \
  -e CPK_OBSERVER_STATE_DATABASE_URL="$OBSERVER_STATE_DATABASE_URL" \
  -e CPK_GRAPH_TOPOLOGY_DATABASE_URL="$GRAPH_TOPOLOGY_DATABASE_URL" \
  "$IMAGE")"

PORT="$(docker port "$CONTAINER" 8080/tcp | sed 's/.*://')"
BASE="http://127.0.0.1:$PORT"

phase "wait for cpk-server liveness"
if ! live="$(curl_with_retry "$BASE/health/live")"; then
  echo "cpk-server did not become live" >&2
  exit 1
fi

phase "verify cpk-server readiness"
printf '%s' "$live" | grep -q '"live"'
if ! ready="$(curl_with_retry "$BASE/health/ready")"; then
  echo "cpk-server did not become ready" >&2
  exit 1
fi
printf '%s' "$ready" | grep -q '"ready"'
printf '%s' "$ready" | grep -q '"stores"'
printf '%s' "$ready" | grep -q '"configured"'
if printf '%s' "$ready" | grep -q 'postgres://'; then
  echo "ready response leaked store endpoint" >&2
  exit 1
fi

phase "reject unauthenticated HTTP"
unauthorized_status="$(curl -sS -o /tmp/cpk-server-unauthorized.json -w '%{http_code}' \
"$BASE/workspaces/workspace-a/graphs/current")"
[ "$unauthorized_status" = "401" ]

phase "reject unauthenticated MCP"
mcp_unauthorized_status="$(curl -sS -o /tmp/cpk-server-mcp-unauthorized.json -w '%{http_code}' \
  -H 'Accept: application/json' \
  -H 'MCP-Protocol-Version: 2025-06-18' \
  -H 'Mcp-Method: tools/call' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"call-unauthorized","method":"tools/call","params":{"name":"command.deployment.plan","arguments":{"workspace_id":"workspace-a"}}}' \
"$BASE/mcp")"
[ "$mcp_unauthorized_status" = "401" ]

phase "authorize bounded missing-workspace read"
authorized_read="$(curl -sS \
  -H 'Authorization: Bearer valid-token' \
  "$BASE/workspaces/workspace-a")"
printf '%s' "$authorized_read" | grep -q 'missing workspace'
if printf '%s' "$authorized_read" | grep -q '"service"'; then
  echo "authorized read returned demo service echo" >&2
  exit 1
fi

phase "create workspace"
workspace_response="$(curl -fsS \
  -H 'Authorization: Bearer valid-token' \
  -H 'Content-Type: application/json' \
  -d '{"workspace_id":"workspace-a","name":"Workspace A","actor_id":"operator-a","idempotency_key":"workspace-a"}' \
  "$BASE/workspaces")"
printf '%s' "$workspace_response" | grep -q '"workspace_id":"workspace-a"'
printf '%s' "$workspace_response" | grep -q '"current_graph_id"'

phase "import product descriptor"
PRODUCT_DESCRIPTOR="$(cat products/hello_server/product.cpk.json)"
printf '{"descriptor_document":%s,"actor_id":"operator-a","imported_at":"2026-07-22T10:02:00Z","idempotency_key":"import-hello"}' \
  "$PRODUCT_DESCRIPTOR" >"$IMPORT_BODY"
product_response="$(curl -fsS \
  -H 'Authorization: Bearer valid-token' \
  -H 'Content-Type: application/json' \
  --data-binary "@$IMPORT_BODY" \
  "$BASE/workspaces/workspace-a/products/import")"
printf '%s' "$product_response" | grep -q '"name":"hello-server"'
printf '%s' "$product_response" | grep -q '"status":"active"'
rm -f "$IMPORT_BODY"

phase "start operation session"
session_response="$(curl -fsS \
  -H 'Authorization: Bearer valid-token' \
  -H 'Content-Type: application/json' \
  -d '{"actor_id":"operator-a","title":"Initial deployment","idempotency_key":"session-a"}' \
  "$BASE/workspaces/workspace-a/sessions")"
printf '%s' "$session_response" | grep -q '"session_id"'
SESSION_ID="$(printf '%s' "$session_response" | sed -n 's/.*"session_id":"\([^"]*\)".*/\1/p')"
if [ -z "$SESSION_ID" ]; then
  echo "session response did not contain parseable session_id" >&2
  exit 1
fi

phase "set desired graph"
desired_response="$(curl -fsS \
  -H 'Authorization: Bearer valid-token' \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"'"$SESSION_ID"'","actor_id":"operator-a","graph":{"name":"desired","runtimes":{},"nodes":{},"edges":{},"public_ingresses":[]},"expected_desired_graph_id":null,"idempotency_key":"desired-a"}' \
  "$BASE/workspaces/workspace-a/graphs/desired")"
printf '%s' "$desired_response" | grep -q '"desired_graph_id"'

phase "read workspace setup"
workspace_after_setup="$(curl -fsS \
  -H 'Authorization: Bearer valid-token' \
  "$BASE/workspaces/workspace-a")"
printf '%s' "$workspace_after_setup" | grep -q '"workspace_id":"workspace-a"'
printf '%s' "$workspace_after_setup" | grep -q '"desired_graph"'

phase "exercise authenticated MCP command"
mcp_response="$(curl -sS \
  -H 'Authorization: Bearer valid-token' \
  -H 'Accept: application/json' \
  -H 'MCP-Protocol-Version: 2025-06-18' \
  -H 'Mcp-Method: tools/call' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"call-1","method":"tools/call","params":{"name":"command.deployment.plan","arguments":{"workspace_id":"workspace-a"}}}' \
  "$BASE/mcp")"
printf '%s' "$mcp_response" | grep -q '"error"'
if printf '%s' "$mcp_response" | grep -q '"service"'; then
  echo "MCP command returned demo service echo" >&2
  exit 1
fi

phase "exercise authenticated MCP read"
mcp_read_response="$(curl -sS \
  -H 'Authorization: Bearer valid-token' \
  -H 'Accept: application/json' \
  -H 'MCP-Protocol-Version: 2025-06-18' \
  -H 'Mcp-Method: resources/read' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"read-1","method":"resources/read","params":{"name":"read.workspace","arguments":{"workspace_id":"workspace-a"}}}' \
  "$BASE/mcp")"
printf '%s' "$mcp_read_response" | grep -q '"workspace_id":"workspace-a"'
printf '%s' "$mcp_read_response" | grep -q '"desired_graph"'
if printf '%s' "$mcp_read_response" | grep -q '"service"'; then
  echo "MCP read returned demo service echo" >&2
  exit 1
fi

phase "clean owned smoke resources"
cleanup
CONTAINER=""
POSTGRES_CONTAINER=""

phase "audit Docker residue"
sh scripts/docker_residue_audit.sh

phase "complete"
echo "cpk-server image smoke passed"
