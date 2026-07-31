#!/bin/sh
set -eu

SERVERS_REPO="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
SECRETS_REPO="${CPK_SECRETS_REPO:-$(CDPATH= cd -- "$SERVERS_REPO/../control-plane-kit-secrets" && pwd)}"
CONTROLLER_IMAGE="${CPK_SERVERS_TEST_IMAGE:-control-plane-kit-servers-test:local}"
SERVER_IMAGE="${CPK_REMOTE_TLS_CUSTODY_SERVER_IMAGE:-control-plane-kit-servers/cpk-server:source-1229}"
SECRETS_IMAGE="${CPK_SECRETS_TEST_IMAGE:-control-plane-kit-secrets:source-1229}"
DIND_IMAGE="${CPK_REMOTE_TLS_DIND_IMAGE:-docker:27-dind}"
BUILD_IMAGES="${CPK_REMOTE_TLS_CUSTODY_BUILD_IMAGES:-1}"
RUN_SUFFIX="$(date +%s)-$$"
WORKSPACE_ID="workspace-remote-docker-tls-$RUN_SUFFIX"
NETWORK="cpk-remote-docker-tls-source-live-$RUN_SUFFIX"
PROJECT_LABEL="org.openj92.project=control-plane-kit-servers"
WORKSPACE_LABEL="org.openj92.cpk.workspace=$WORKSPACE_ID"
STATE_ROOT="$(mktemp -d)"
PROVIDER_DATA_DIR="$STATE_ROOT/provider-data"
BOOTSTRAP_DIR="$STATE_ROOT/bootstrap"
OPERATIONS_DUMP="$STATE_ROOT/operations.sql"
POSTGRES_CONTAINER=""
SECRETS_CONTAINER=""
DIND_CONTAINER=""
SERVER_CONTAINER=""

umask 077
mkdir -p "$PROVIDER_DATA_DIR" "$BOOTSTRAP_DIR"

cleanup() {
  for container in "$SERVER_CONTAINER" "$SECRETS_CONTAINER" "$POSTGRES_CONTAINER" "$DIND_CONTAINER"; do
    if [ -n "$container" ]; then
      docker rm -f "$container" >/dev/null 2>&1 || true
    fi
  done
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  docker ps -aq --filter "label=$WORKSPACE_LABEL" \
    | while IFS= read -r resource; do
        if [ -n "$resource" ]; then
          docker rm -f "$resource" >/dev/null 2>&1 || true
        fi
      done
  docker volume ls -q --filter "label=$WORKSPACE_LABEL" \
    | while IFS= read -r resource; do
        if [ -n "$resource" ]; then
          docker volume rm "$resource" >/dev/null 2>&1 || true
        fi
      done
  docker network ls -q --filter "label=$WORKSPACE_LABEL" \
    | while IFS= read -r resource; do
        if [ -n "$resource" ]; then
          docker network rm "$resource" >/dev/null 2>&1 || true
        fi
      done
  rm -rf "$STATE_ROOT"
}
trap cleanup EXIT INT TERM

if [ "$BUILD_IMAGES" = "1" ]; then
  docker build -f "$SERVERS_REPO/Dockerfile.test" -t "$CONTROLLER_IMAGE" "$SERVERS_REPO"
  docker build -f "$SERVERS_REPO/products/cpk_server/Dockerfile" -t "$SERVER_IMAGE" "$SERVERS_REPO"
  docker build -f "$SECRETS_REPO/Dockerfile.test" -t "$SECRETS_IMAGE" "$SECRETS_REPO"
fi

docker pull "$DIND_IMAGE"
docker network create --label "$WORKSPACE_LABEL" "$NETWORK" >/dev/null

POSTGRES_CONTAINER="$(docker run -d \
  --label "$PROJECT_LABEL" \
  --label "$WORKSPACE_LABEL" \
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

DIND_CONTAINER="$(docker run -d \
  --privileged \
  --label "$PROJECT_LABEL" \
  --label "$WORKSPACE_LABEL" \
  --network "$NETWORK" \
  --network-alias remote-docker \
  -e DOCKER_TLS_CERTDIR=/certs \
  "$DIND_IMAGE")"

DIND_READY=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  if docker exec "$DIND_CONTAINER" docker \
    --tlsverify \
    --tlscacert=/certs/client/ca.pem \
    --tlscert=/certs/client/cert.pem \
    --tlskey=/certs/client/key.pem \
    -H tcp://127.0.0.1:2376 version >/dev/null 2>&1; then
    DIND_READY=1
    break
  fi
  sleep 1
done
if [ "$DIND_READY" != "1" ]; then
  echo "ephemeral Docker TLS daemon did not become ready" >&2
  docker logs "$DIND_CONTAINER" 2>&1 | tail -n 100 >&2 || true
  exit 1
fi

docker cp "$DIND_CONTAINER:/certs/client/ca.pem" "$BOOTSTRAP_DIR/ca.pem"
docker cp "$DIND_CONTAINER:/certs/client/cert.pem" "$BOOTSTRAP_DIR/cert.pem"
docker cp "$DIND_CONTAINER:/certs/client/key.pem" "$BOOTSTRAP_DIR/key.pem"
chmod 0400 "$BOOTSTRAP_DIR/ca.pem" "$BOOTSTRAP_DIR/cert.pem" "$BOOTSTRAP_DIR/key.pem"

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
'

BOOTSTRAP_DIR="$BOOTSTRAP_DIR" python3 -c '
import json
import os
from pathlib import Path

base = Path(os.environ["BOOTSTRAP_DIR"])
token = base.joinpath("client-token").read_text(encoding="utf-8")
intents = [
    "docker.remote-tls.ca-certificate",
    "docker.remote-tls.client-certificate",
    "docker.remote-tls.client-key",
]
credentials = [{
    "subject": "cpk-server-remote-tls-source-live",
    "token": token,
    "grants": [
        {"action": "secret.write", "workspace_id": "*", "intents": intents},
        {"action": "secret.resolve", "workspace_id": "*", "intents": intents},
        {"action": "secret.revoke", "workspace_id": "*"},
        {"action": "secret.metadata", "workspace_id": "*"},
    ],
}]
base.joinpath("credentials.json").write_text(
    json.dumps(credentials, separators=(",", ":"), sort_keys=True),
    encoding="utf-8",
)
'
chmod 0400 "$BOOTSTRAP_DIR"/*

CPK_CONTROL_AUTH_STATIC_PRINCIPALS_JSON="$(
  WORKSPACE_ID="$WORKSPACE_ID" python3 -c '
import json
import os

workspace_id = os.environ["WORKSPACE_ID"]
operator_scopes = [
    "hub:instance:create",
    "hub:instance:read",
    "instance:workspace:read",
    "instance:workspace:edit",
    "runtime-authority:register",
    "runtime-authority:read",
    "runtime-authority:use",
    "secret-provider:register",
    "secret-provider:read",
    "secret-provider:use",
]
print(json.dumps([{
    "credential": "present",
    "subject_id": "hosted-operator",
    "kind": "operator",
    "workspace_grants": {workspace_id: operator_scopes},
}], separators=(",", ":"), sort_keys=True))
'
)"

SECRETS_CONTAINER="$(docker run -d \
  --label "$PROJECT_LABEL" \
  --label "$WORKSPACE_LABEL" \
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

PROVIDER_ROUTES_JSON='{"source-live-secrets":"http://cpk-secrets:8081"}'
PROVIDER_BOOTSTRAP_FILES_JSON='{"secret://bootstrap/provider/client-token":"/run/secrets/cpk-provider/client-token"}'
SERVER_CONTAINER="$(docker run -d \
  --label "$PROJECT_LABEL" \
  --label "$WORKSPACE_LABEL" \
  --network "$NETWORK" \
  --network-alias cpk-server \
  -v "$BOOTSTRAP_DIR/client-token:/run/secrets/cpk-provider/client-token:ro" \
  -e CPK_SERVER_MODE=execution-capable \
  -e CPK_CONTROL_AUTH_VERIFIER=static-development \
  -e CPK_CONTROL_AUTH_STATIC_PRINCIPALS_JSON="$CPK_CONTROL_AUTH_STATIC_PRINCIPALS_JSON" \
  -e CPK_PORT=8080 \
  -e CPK_RUNTIME_INTERPRETERS=docker \
  -e CPK_PRODUCT_MATERIAL_RESOLVER=provider \
  -e CPK_MATERIAL_PROVIDER_ROUTES_JSON="$PROVIDER_ROUTES_JSON" \
  -e CPK_MATERIAL_PROVIDER_BOOTSTRAP_FILES_JSON="$PROVIDER_BOOTSTRAP_FILES_JSON" \
  -e CPK_WORKPLACE_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
  -e CPK_ACTIVITY_HISTORY_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
  -e CPK_OBSERVER_STATE_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
  -e CPK_GRAPH_TOPOLOGY_DATABASE_URL=postgresql://cpk:cpk@cpk-postgres:5432/cpk \
  "$SERVER_IMAGE")"

if ! docker run --rm \
  --label "$PROJECT_LABEL" \
  --label "$WORKSPACE_LABEL" \
  --network "$NETWORK" \
  -v "$BOOTSTRAP_DIR:/run/secrets/cpk-source-live:ro" \
  -e CPK_HOSTED_ACTIVITY_BASE_URL=http://cpk-server:8080 \
  -e CPK_HOSTED_ACTIVITY_SERVER_CONTAINER="$SERVER_CONTAINER" \
  -e CPK_HOSTED_ACTIVITY_WORKSPACE_ID="$WORKSPACE_ID" \
  -e CPK_REMOTE_DOCKER_TLS_ENDPOINT=tcp://remote-docker:2376 \
  -e CPK_SECRET_PROVIDER_TOKEN_FILE=/run/secrets/cpk-source-live/client-token \
  -e CPK_SECRET_PROVIDER_BOOTSTRAP_DIR=/run/secrets/cpk-source-live \
  "$CONTROLLER_IMAGE" \
  python scripts/cpk_server_remote_tls_secret_custody_source_live.py; then
  docker logs "$SERVER_CONTAINER" 2>&1 | tail -n 100 >&2 || true
  docker logs "$SECRETS_CONTAINER" 2>&1 | tail -n 100 >&2 || true
  exit 1
fi

docker exec "$POSTGRES_CONTAINER" pg_dump -U cpk -d cpk >"$OPERATIONS_DUMP"
for secret_file in ca.pem cert.pem key.pem client-token; do
  if docker logs "$SERVER_CONTAINER" 2>&1 \
    | grep -F -f "$BOOTSTRAP_DIR/$secret_file" >/dev/null 2>&1; then
    echo "cpk-server logs contain forbidden remote-TLS material" >&2
    exit 1
  fi
  if docker logs "$SECRETS_CONTAINER" 2>&1 \
    | grep -F -f "$BOOTSTRAP_DIR/$secret_file" >/dev/null 2>&1; then
    echo "provider logs contain forbidden remote-TLS material" >&2
    exit 1
  fi
  if grep -aF -f "$BOOTSTRAP_DIR/$secret_file" "$OPERATIONS_DUMP" >/dev/null 2>&1; then
    echo "operations database contains forbidden remote-TLS material" >&2
    exit 1
  fi
  if grep -aF -f "$BOOTSTRAP_DIR/$secret_file" \
    "$PROVIDER_DATA_DIR/secrets.sqlite3" >/dev/null 2>&1; then
    echo "provider database contains plaintext remote-TLS material" >&2
    exit 1
  fi
done

cleanup
SERVER_CONTAINER=""
SECRETS_CONTAINER=""
POSTGRES_CONTAINER=""
DIND_CONTAINER=""

sh "$SERVERS_REPO/scripts/docker_residue_audit.sh"
echo "cpk-server remote Docker TLS durable-custody foundation smoke passed"
