#!/bin/sh
set -eu

SERVERS_REPO="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
SECRETS_REPO="${CPK_SECRETS_REPO:-$(CDPATH= cd -- "$SERVERS_REPO/../control-plane-kit-secrets" && pwd)}"
CONTROLLER_IMAGE="${CPK_SERVERS_TEST_IMAGE:-control-plane-kit-servers-test:local}"
SERVER_IMAGE="${CPK_SECRET_PROVIDER_SERVER_IMAGE:-control-plane-kit-servers/cpk-server:source-1202}"
SECRETS_IMAGE="${CPK_SECRETS_TEST_IMAGE:-control-plane-kit-secrets:source-1202}"
BUILD_IMAGES="${CPK_SECRET_PROVIDER_BUILD_IMAGES:-1}"
NETWORK="cpk-secret-provider-source-live-$$"
LABEL="org.openj92.project=control-plane-kit-servers"
STATE_ROOT="$(mktemp -d)"
PROVIDER_DATA_DIR="$STATE_ROOT/provider-data"
BOOTSTRAP_DIR="$STATE_ROOT/bootstrap"
OPERATIONS_DUMP="$STATE_ROOT/operations.sql"
POSTGRES_CONTAINER=""
SECRETS_CONTAINER=""
SERVER_CONTAINER=""
WORKSPACE_LABEL_KEY="org.openj92.cpk.workspace"

mkdir -p "$PROVIDER_DATA_DIR" "$BOOTSTRAP_DIR"

cleanup_workspace_resources() {
  docker ps -aq --filter "label=$WORKSPACE_LABEL_KEY" \
    | while IFS= read -r resource; do
        if [ -n "$resource" ]; then
          workspace="$(docker inspect -f "{{ index .Config.Labels \"$WORKSPACE_LABEL_KEY\" }}" "$resource" 2>/dev/null || true)"
          case "$workspace" in
            workspace-secret-provider-live|\
            workspace-secret-denied-scope|\
            workspace-secret-wrong-source|\
            workspace-secret-wrong-target|\
            workspace-secret-wrong-intent|\
            workspace-secret-revoked-provider|\
            workspace-secret-revoked-reference|\
            workspace-secret-missing|\
            workspace-secret-wrong-credential|\
            workspace-secret-unavailable|\
            workspace-secret-revoked-before-use|\
            workspace-secret-concurrent-a|\
            workspace-secret-concurrent-b|\
            workspace-secret-concurrent-c)
              docker rm -f "$resource" >/dev/null 2>&1 || true
              ;;
          esac
        fi
      done
  docker volume ls -q --filter "label=$WORKSPACE_LABEL_KEY" \
    | while IFS= read -r resource; do
        if [ -n "$resource" ]; then
          workspace="$(docker volume inspect -f "{{ index .Labels \"$WORKSPACE_LABEL_KEY\" }}" "$resource" 2>/dev/null || true)"
          case "$workspace" in
            workspace-secret-provider-live|\
            workspace-secret-denied-scope|\
            workspace-secret-wrong-source|\
            workspace-secret-wrong-target|\
            workspace-secret-wrong-intent|\
            workspace-secret-revoked-provider|\
              workspace-secret-revoked-reference|\
              workspace-secret-missing|\
              workspace-secret-wrong-credential|\
              workspace-secret-unavailable|\
              workspace-secret-revoked-before-use|\
              workspace-secret-concurrent-a|\
              workspace-secret-concurrent-b|\
              workspace-secret-concurrent-c)
              docker volume rm "$resource" >/dev/null 2>&1 || true
              ;;
          esac
        fi
      done
  docker network ls -q --filter "label=$WORKSPACE_LABEL_KEY" \
    | while IFS= read -r resource; do
        if [ -n "$resource" ]; then
          workspace="$(docker network inspect -f "{{ index .Labels \"$WORKSPACE_LABEL_KEY\" }}" "$resource" 2>/dev/null || true)"
          case "$workspace" in
            workspace-secret-provider-live|\
            workspace-secret-denied-scope|\
            workspace-secret-wrong-source|\
            workspace-secret-wrong-target|\
            workspace-secret-wrong-intent|\
            workspace-secret-revoked-provider|\
              workspace-secret-revoked-reference|\
              workspace-secret-missing|\
              workspace-secret-wrong-credential|\
              workspace-secret-unavailable|\
              workspace-secret-revoked-before-use|\
              workspace-secret-concurrent-a|\
              workspace-secret-concurrent-b|\
              workspace-secret-concurrent-c)
              docker network rm "$resource" >/dev/null 2>&1 || true
              ;;
          esac
        fi
      done
}

cleanup() {
  if [ -n "$SERVER_CONTAINER" ]; then
    docker rm -f "$SERVER_CONTAINER" >/dev/null 2>&1 || true
  fi
  if [ -n "$SECRETS_CONTAINER" ]; then
    docker rm -f "$SECRETS_CONTAINER" >/dev/null 2>&1 || true
  fi
  if [ -n "$POSTGRES_CONTAINER" ]; then
    docker rm -f "$POSTGRES_CONTAINER" >/dev/null 2>&1 || true
  fi
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  cleanup_workspace_resources
  rm -rf "$STATE_ROOT"
}
trap cleanup EXIT INT TERM

if [ "$BUILD_IMAGES" = "1" ]; then
  docker build -f "$SERVERS_REPO/Dockerfile.test" -t "$CONTROLLER_IMAGE" "$SERVERS_REPO"
  docker build -f "$SERVERS_REPO/products/cpk_server/Dockerfile" -t "$SERVER_IMAGE" "$SERVERS_REPO"
  docker build -f "$SECRETS_REPO/Dockerfile.test" -t "$SECRETS_IMAGE" "$SECRETS_REPO"
fi

docker run --rm \
  -v "$BOOTSTRAP_DIR:/bootstrap" \
  "$SECRETS_IMAGE" \
  python -c '
from pathlib import Path
import os
import secrets
from control_plane_kit_secrets.crypto import encode_master_key_for_file

base = Path("/bootstrap")
base.joinpath("master.key").write_text(
    encode_master_key_for_file(os.urandom(32)),
    encoding="utf-8",
)
base.joinpath("client-token").write_text(
    secrets.token_urlsafe(48),
    encoding="utf-8",
)
base.joinpath("wrong-token").write_text(
    secrets.token_urlsafe(48),
    encoding="utf-8",
)
base.joinpath("postgres-password").write_text(
    secrets.token_urlsafe(40),
    encoding="utf-8",
)
base.joinpath("postgres-password-v2").write_text(
    secrets.token_urlsafe(40),
    encoding="utf-8",
)
base.joinpath("postgres-revoked").write_text(
    secrets.token_urlsafe(40),
    encoding="utf-8",
)
for index in range(1, 4):
    base.joinpath(f"postgres-concurrent-{index}").write_text(
        secrets.token_urlsafe(40),
        encoding="utf-8",
    )
'
chmod 0400 "$BOOTSTRAP_DIR/master.key"
chmod 0400 "$BOOTSTRAP_DIR/client-token"
chmod 0400 "$BOOTSTRAP_DIR/wrong-token"
chmod 0400 "$BOOTSTRAP_DIR/postgres-password"
chmod 0400 "$BOOTSTRAP_DIR/postgres-password-v2"
chmod 0400 "$BOOTSTRAP_DIR/postgres-revoked"
chmod 0400 "$BOOTSTRAP_DIR"/postgres-concurrent-*

BOOTSTRAP_DIR="$BOOTSTRAP_DIR" python3 -c '
import json
import os
from pathlib import Path

token = Path(os.environ["BOOTSTRAP_DIR"], "client-token").read_text(encoding="utf-8")
credentials = [{
    "subject": "cpk-server-source-live",
    "token": token,
    "grants": [
        {
            "action": "secret.write",
            "workspace_id": "*",
            "intents": ["postgres.password"],
        },
        {
            "action": "secret.resolve",
            "workspace_id": "*",
            "intents": ["postgres.password"],
        },
        {
            "action": "secret.rotate",
            "workspace_id": "*",
        },
        {
            "action": "secret.revoke",
            "workspace_id": "*",
        },
        {
            "action": "secret.metadata",
            "workspace_id": "*",
        },
    ],
}]
Path(os.environ["BOOTSTRAP_DIR"], "credentials.json").write_text(
    json.dumps(credentials, separators=(",", ":"), sort_keys=True),
    encoding="utf-8",
)
'
chmod 0400 "$BOOTSTRAP_DIR/credentials.json"

WORKSPACES='[
  "workspace-secret-provider-live",
  "workspace-secret-denied-scope",
  "workspace-secret-wrong-source",
  "workspace-secret-wrong-target",
  "workspace-secret-wrong-intent",
  "workspace-secret-revoked-provider",
  "workspace-secret-revoked-reference",
  "workspace-secret-missing",
  "workspace-secret-wrong-credential",
  "workspace-secret-unavailable",
  "workspace-secret-revoked-before-use",
  "workspace-secret-concurrent-a",
  "workspace-secret-concurrent-b",
  "workspace-secret-concurrent-c"
]'
CPK_CONTROL_AUTH_STATIC_PRINCIPALS_JSON="$(
  WORKSPACES="$WORKSPACES" python3 -c '
import json
import os

workspaces = json.loads(os.environ["WORKSPACES"])
operator_scopes = [
    "hub:instance:create",
    "hub:instance:read",
    "instance:workspace:read",
    "instance:workspace:edit",
    "plan:request",
    "plan:approve",
    "plan:approve-destructive",
    "plan:execute",
    "execution:operate",
    "runtime-authority:register",
    "runtime-authority:read",
    "runtime-authority:revoke",
    "runtime-authority:use",
    "secret-provider:register",
    "secret-provider:read",
    "secret-provider:revoke",
]
worker_scopes = ["execution:operate", "secret-provider:use"]
limited_worker_scopes = ["execution:operate"]
print(json.dumps([
    {
        "credential": "present",
        "subject_id": "hosted-operator",
        "kind": "operator",
        "workspace_grants": {workspace: operator_scopes for workspace in workspaces},
    },
    {
        "credential": "worker-present",
        "subject_id": "hosted-worker",
        "kind": "worker",
        "workspace_grants": {workspace: worker_scopes for workspace in workspaces},
    },
    {
        "credential": "worker-no-secret",
        "subject_id": "hosted-worker-no-secret",
        "kind": "worker",
        "workspace_grants": {
            workspace: limited_worker_scopes for workspace in workspaces
        },
    },
], separators=(",", ":"), sort_keys=True))
'
)"

PROVIDER_ROUTES_JSON='{"source-live-secrets":"http://cpk-secrets:8081"}'
PROVIDER_BOOTSTRAP_FILES_JSON='{"secret://bootstrap/provider/client-token":"/run/secrets/cpk-provider/client-token","secret://bootstrap/provider/wrong-token":"/run/secrets/cpk-provider/wrong-token"}'

docker network create "$NETWORK" >/dev/null

POSTGRES_CONTAINER="$(docker run -d \
  --label "$LABEL" \
  --network "$NETWORK" \
  --network-alias cpk-postgres \
  -e POSTGRES_DB=cpk \
  -e POSTGRES_USER=cpk \
  -e POSTGRES_PASSWORD=cpk \
  postgres:16-alpine)"

POSTGRES_READY=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if docker exec "$POSTGRES_CONTAINER" psql -U cpk -d cpk -c 'SELECT 1' >/dev/null 2>&1; then
    POSTGRES_READY=1
    break
  fi
  sleep 1
done
if [ "$POSTGRES_READY" != "1" ]; then
  echo "operations Postgres did not become query-ready" >&2
  exit 1
fi

SECRETS_CONTAINER="$(docker run -d \
  --label "$LABEL" \
  --network "$NETWORK" \
  --network-alias cpk-secrets \
  -v "$PROVIDER_DATA_DIR:/var/lib/cpk-secrets" \
  -v "$BOOTSTRAP_DIR/master.key:/run/secrets/cpk-secrets/master-key:ro" \
  -v "$BOOTSTRAP_DIR/credentials.json:/run/secrets/cpk-secrets/credentials.json:ro" \
  -e CPK_SECRETS_DATABASE_PATH=/var/lib/cpk-secrets/secrets.sqlite3 \
  -e CPK_SECRETS_MASTER_KEY_FILE=/run/secrets/cpk-secrets/master-key \
  -e CPK_SECRETS_CREDENTIALS_FILE=/run/secrets/cpk-secrets/credentials.json \
  -e CPK_SECRETS_PROVIDER_ID=control-plane-kit \
  "$SECRETS_IMAGE" \
  python -m uvicorn control_plane_kit_secrets.server:app \
    --host 0.0.0.0 --port 8081 --log-level warning)"

SECRETS_READY=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if docker run --rm \
    --network "$NETWORK" \
    "$SECRETS_IMAGE" \
    python -c '
from urllib.request import urlopen
with urlopen("http://cpk-secrets:8081/health/ready", timeout=2) as response:
    raise SystemExit(0 if response.status == 200 else 1)
' >/dev/null 2>&1; then
    SECRETS_READY=1
    break
  fi
  sleep 1
done
if [ "$SECRETS_READY" != "1" ]; then
  echo "secrets provider did not become ready" >&2
  exit 1
fi

SERVER_CONTAINER="$(docker run -d \
  --label "$LABEL" \
  --network "$NETWORK" \
  --network-alias cpk-server \
  --group-add "${CPK_DOCKER_SOCKET_GROUP:-0}" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$BOOTSTRAP_DIR/client-token:/run/secrets/cpk-provider/client-token:ro" \
  -v "$BOOTSTRAP_DIR/wrong-token:/run/secrets/cpk-provider/wrong-token:ro" \
  -e CPK_SERVER_MODE=execution-capable \
  -e CPK_CONTROL_AUTH_VERIFIER=static-development \
  -e CPK_CONTROL_AUTH_STATIC_PRINCIPALS_JSON="$CPK_CONTROL_AUTH_STATIC_PRINCIPALS_JSON" \
  -e CPK_PORT=8080 \
  -e CPK_RUNTIME_INTERPRETERS=docker \
  -e CPK_INGRESS_INTERPRETERS=none \
  -e CPK_PRODUCT_MATERIAL_RESOLVER=provider \
  -e CPK_MATERIAL_PROVIDER_ROUTES_JSON="$PROVIDER_ROUTES_JSON" \
  -e CPK_MATERIAL_PROVIDER_BOOTSTRAP_FILES_JSON="$PROVIDER_BOOTSTRAP_FILES_JSON" \
  -e CPK_WORKPLACE_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
  -e CPK_ACTIVITY_HISTORY_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
  -e CPK_OBSERVER_STATE_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
  -e CPK_GRAPH_TOPOLOGY_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
  "$SERVER_IMAGE")"

if ! docker run --rm \
  --label "$LABEL" \
  --network "$NETWORK" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$BOOTSTRAP_DIR:/run/secrets/cpk-source-live:ro" \
  -e CPK_HOSTED_ACTIVITY_BASE_URL=http://cpk-server:8080 \
  -e CPK_HOSTED_ACTIVITY_SERVER_CONTAINER="$SERVER_CONTAINER" \
  -e CPK_SECRET_PROVIDER_CONTAINER="$SECRETS_CONTAINER" \
  -e CPK_SECRET_PROVIDER_TOKEN_FILE=/run/secrets/cpk-source-live/client-token \
  -e CPK_SECRET_PROVIDER_BOOTSTRAP_DIR=/run/secrets/cpk-source-live \
  -e CPK_HOSTED_ACTIVITY_SERVERS_REPO=/app \
  -e CPK_OPERATIONS_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
  "$CONTROLLER_IMAGE" \
  python scripts/cpk_server_secret_provider_source_live.py; then
  docker logs "$SERVER_CONTAINER" 2>&1 | tail -n 100 >&2 || true
  docker logs "$SECRETS_CONTAINER" 2>&1 | tail -n 100 >&2 || true
  exit 1
fi

docker exec "$POSTGRES_CONTAINER" pg_dump -U cpk -d cpk >"$OPERATIONS_DUMP"
for secret_file in \
  client-token \
  wrong-token \
  postgres-password \
  postgres-password-v2 \
  postgres-revoked \
  postgres-concurrent-1 \
  postgres-concurrent-2 \
  postgres-concurrent-3
do
  if docker logs "$SERVER_CONTAINER" 2>&1 \
    | grep -F -f "$BOOTSTRAP_DIR/$secret_file" >/dev/null 2>&1; then
    echo "cpk-server logs contain forbidden source-live material" >&2
    exit 1
  fi
  if docker logs "$SECRETS_CONTAINER" 2>&1 \
    | grep -F -f "$BOOTSTRAP_DIR/$secret_file" >/dev/null 2>&1; then
    echo "provider logs contain forbidden source-live material" >&2
    exit 1
  fi
  if grep -aF -f "$BOOTSTRAP_DIR/$secret_file" "$OPERATIONS_DUMP" >/dev/null 2>&1; then
    echo "operations database contains forbidden source-live material" >&2
    exit 1
  fi
  if grep -aF -f "$BOOTSTRAP_DIR/$secret_file" \
    "$PROVIDER_DATA_DIR/secrets.sqlite3" >/dev/null 2>&1; then
    echo "provider database contains plaintext source-live secret" >&2
    exit 1
  fi
done

cleanup
SERVER_CONTAINER=""
SECRETS_CONTAINER=""
POSTGRES_CONTAINER=""

sh "$SERVERS_REPO/scripts/docker_residue_audit.sh"
echo "cpk-server durable secret-provider source-live smoke passed"
