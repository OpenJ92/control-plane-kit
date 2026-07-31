#!/bin/sh
set -eu

SERVERS_REPO="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
SECRETS_REPO="${CPK_SECRETS_REPO:-$(CDPATH= cd -- "$SERVERS_REPO/../control-plane-kit-secrets" && pwd)}"
CONTROLLER_IMAGE="${CPK_SERVERS_TEST_IMAGE:-control-plane-kit-servers-test:local}"
SERVER_IMAGE="${CPK_REMOTE_TLS_CUSTODY_SERVER_IMAGE:-control-plane-kit-servers/cpk-server:source-1229}"
SECRETS_IMAGE="${CPK_SECRETS_TEST_IMAGE:-control-plane-kit-secrets:source-1229}"
DIND_IMAGE="${CPK_REMOTE_TLS_DIND_IMAGE:-docker:27-dind}"
POSTGRES_IMAGE="${CPK_LIVE_POSTGRES_IMAGE:-postgres:16-alpine}"
BUILD_IMAGES="${CPK_REMOTE_TLS_CUSTODY_BUILD_IMAGES:-1}"
RUN_SUFFIX="$(date +%s)-$$"
WORKSPACE_ID="workspace-remote-docker-tls-$RUN_SUFFIX"
NETWORK="cpk-remote-docker-tls-source-live-$RUN_SUFFIX"
PROJECT_LABEL="org.openj92.project=control-plane-kit-servers"
WORKSPACE_LABEL="org.openj92.cpk.workspace=$WORKSPACE_ID"
STATE_ROOT="$(mktemp -d)"
PROVIDER_DATA_DIR="$STATE_ROOT/provider-data"
BOOTSTRAP_DIR="$STATE_ROOT/bootstrap"
CONTROLLER_STATE_DIR="$STATE_ROOT/controller-state"
OPERATIONS_DUMP="$STATE_ROOT/operations.sql"
OPERATIONS_AUTHORIZATIONS="$STATE_ROOT/operations-authorizations.txt"
PROVIDER_SELECTIONS="$STATE_ROOT/provider-selections.txt"
DENIAL_EVIDENCE_DIR="$STATE_ROOT/denials"
HOST_INVENTORY_BEFORE="$STATE_ROOT/host-inventory-before.txt"
HOST_INVENTORY_AFTER="$STATE_ROOT/host-inventory-after.txt"
REMOTE_INVENTORY="$STATE_ROOT/remote-inventory.txt"
SERVER_LOG="$STATE_ROOT/cpk-server.log"
PROVIDER_LOG="$STATE_ROOT/secrets-provider.log"
POSTGRES_CONTAINER=""
SECRETS_CONTAINER=""
DIND_CONTAINER=""
SERVER_CONTAINER=""

umask 077
mkdir -p \
  "$PROVIDER_DATA_DIR" \
  "$BOOTSTRAP_DIR" \
  "$CONTROLLER_STATE_DIR" \
  "$DENIAL_EVIDENCE_DIR"

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

wait_for_secrets() {
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if docker run --rm \
      --network "$NETWORK" \
      "$SECRETS_IMAGE" \
      python -c '
from urllib.request import urlopen
with urlopen("http://cpk-secrets:8081/health/ready", timeout=2) as response:
    raise SystemExit(0 if response.status == 200 else 1)
' >/dev/null 2>&1; then
      return
    fi
    sleep 1
  done
  echo "secrets provider did not become ready" >&2
  exit 1
}

start_secrets() {
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
  wait_for_secrets
}

start_server() {
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
}

run_controller() {
  phase="$1"
  denial_case="${2:-}"
  denial_action="${3:-}"
  controller_workspace="$WORKSPACE_ID"
  if [ "$phase" = "deny" ]; then
    controller_workspace="$WORKSPACE_ID-denial-$denial_case"
  fi
  if ! docker run --rm \
    --label "$PROJECT_LABEL" \
    --label "$WORKSPACE_LABEL" \
    --network "$NETWORK" \
    -v "$BOOTSTRAP_DIR:/run/secrets/cpk-source-live:ro" \
    -v "$CONTROLLER_STATE_DIR:/run/cpk-state" \
    -e CPK_HOSTED_ACTIVITY_BASE_URL=http://cpk-server:8080 \
    -e CPK_HOSTED_ACTIVITY_SERVER_CONTAINER="$SERVER_CONTAINER" \
    -e CPK_HOSTED_ACTIVITY_SERVERS_REPO=/app \
    -e CPK_HOSTED_ACTIVITY_WORKSPACE_ID="$controller_workspace" \
    -e CPK_REMOTE_DOCKER_TLS_ENDPOINT=tcp://remote-docker:2376 \
    -e CPK_SECRET_PROVIDER_TOKEN_FILE=/run/secrets/cpk-source-live/client-token \
    -e CPK_SECRET_PROVIDER_BOOTSTRAP_DIR=/run/secrets/cpk-source-live \
    -e CPK_REMOTE_TLS_STATE_DIR=/run/cpk-state \
    -e CPK_REMOTE_TLS_PHASE="$phase" \
    -e CPK_REMOTE_TLS_DENIAL_CASE="$denial_case" \
    -e CPK_REMOTE_TLS_DENIAL_ACTION="$denial_action" \
    "$CONTROLLER_IMAGE" \
    python scripts/cpk_server_remote_tls_secret_custody_source_live.py; then
    docker logs "$SERVER_CONTAINER" 2>&1 | tail -n 100 >&2 || true
    docker logs "$SECRETS_CONTAINER" 2>&1 | tail -n 100 >&2 || true
    exit 1
  fi
}

host_denial_inventory() {
  destination="$1"
  {
    docker ps -aq --no-trunc | sort | sed 's/^/container:/'
    docker network ls -q --no-trunc | sort | sed 's/^/network:/'
    docker volume ls -q | sort | sed 's/^/volume:/'
    docker image ls --no-trunc \
      --format '{{.ID}}|{{.Repository}}|{{.Tag}}|{{.Digest}}' \
      | sort | sed 's/^/image:/'
  } >"$destination"
}

remote_denial_inventory() {
  destination="$1"
  docker exec "$DIND_CONTAINER" sh -c "
    docker ps -aq --no-trunc | sort | sed 's/^/container:/'
    docker network ls -q --no-trunc | sort | sed 's/^/network:/'
    docker volume ls -q | sort | sed 's/^/volume:/'
    docker image ls --no-trunc \
      --format '{{.ID}}|{{.Repository}}|{{.Tag}}|{{.Digest}}' \
      | sort | sed 's/^/image:/'
  " >"$destination"
}

assert_denial_inventory_unchanged() {
  before="$1"
  after="$2"
  scope="$3"
  if cmp -s "$before" "$after"; then
    return
  fi
  echo "remote Docker TLS denial mutated $scope inventory" >&2
  diff -u "$before" "$after" >&2 || true
  exit 1
}

host_inventory() {
  destination="$1"
  {
    docker ps -aq --filter "label=$WORKSPACE_LABEL" | sort | sed 's/^/container:/'
    docker network ls -q --filter "label=$WORKSPACE_LABEL" | sort | sed 's/^/network:/'
    docker volume ls -q --filter "label=$WORKSPACE_LABEL" | sort | sed 's/^/volume:/'
  } >"$destination"
}

remote_inventory() {
  docker exec "$DIND_CONTAINER" sh -c "
    docker ps -aq --filter 'label=$WORKSPACE_LABEL' | sort | sed 's/^/container:/'
    docker network ls -q --filter 'label=$WORKSPACE_LABEL' | sort | sed 's/^/network:/'
    docker volume ls -q --filter 'label=$WORKSPACE_LABEL' | sort | sed 's/^/volume:/'
  " >"$REMOTE_INVENTORY"
}

assert_host_inventory_unchanged() {
  host_inventory "$HOST_INVENTORY_AFTER"
  if ! cmp -s "$HOST_INVENTORY_BEFORE" "$HOST_INVENTORY_AFTER"; then
    echo "remote authority execution mutated host Docker inventory" >&2
    diff -u "$HOST_INVENTORY_BEFORE" "$HOST_INVENTORY_AFTER" >&2 || true
    exit 1
  fi
}

assert_no_tls_temp_directory() {
  if docker exec "$SERVER_CONTAINER" sh -c \
    'test -z "$(find /tmp -maxdepth 1 -type d -name "cpk-docker-tls-*" -print -quit)"'; then
    return
  fi
  echo "cpk-server retained an authority-scoped Docker TLS directory" >&2
  exit 1
}

assert_denial_audit() {
  denial_case="$1"
  denial_workspace="$2"
  operations_rows="$DENIAL_EVIDENCE_DIR/$denial_case-operations.txt"
  provider_rows="$DENIAL_EVIDENCE_DIR/$denial_case-provider.txt"
  selection_rows="$DENIAL_EVIDENCE_DIR/$denial_case-selections.txt"

  docker exec "$POSTGRES_CONTAINER" psql -U cpk -d cpk -At -F '|' -c "
SELECT correlation_id, use_intent
FROM cpk_secret_use_authorizations
WHERE workspace_id = '$denial_workspace'
ORDER BY correlation_id, use_intent;
" >"$operations_rows"
  docker exec -e DENIAL_WORKSPACE="$denial_workspace" "$SECRETS_CONTAINER" python -c '
import os
import sqlite3

connection = sqlite3.connect("/var/lib/cpk-secrets/secrets.sqlite3")
for row in connection.execute(
    """
    SELECT correlation_id, outcome, intent
    FROM audit_records
    WHERE workspace_id = ?
    ORDER BY rowid
    """,
    (os.environ["DENIAL_WORKSPACE"],),
):
    print("|".join("" if value is None else str(value) for value in row))
' >"$provider_rows"
  docker exec -e DENIAL_WORKSPACE="$denial_workspace" "$SECRETS_CONTAINER" python -c '
import os
import sqlite3

connection = sqlite3.connect("/var/lib/cpk-secrets/secrets.sqlite3")
for row in connection.execute(
    """
    SELECT correlation_id, intent, version_id
    FROM secret_resolution_selections
    WHERE workspace_id = ?
    ORDER BY correlation_id
    """,
    (os.environ["DENIAL_WORKSPACE"],),
):
    print("|".join(str(value) for value in row))
' >"$selection_rows"

  DENIAL_CASE="$denial_case" \
  OPERATIONS_ROWS="$operations_rows" \
  PROVIDER_ROWS="$provider_rows" \
  SELECTION_ROWS="$selection_rows" python3 -c '
import os
from pathlib import Path

def rows(name):
    return [
        tuple(line.split("|"))
        for line in Path(os.environ[name]).read_text().splitlines()
        if line
    ]

case = os.environ["DENIAL_CASE"]
operations = set(rows("OPERATIONS_ROWS"))
provider = rows("PROVIDER_ROWS")
selections = rows("SELECTION_ROWS")
allowed_intents = {
    "docker.remote-tls.ca-certificate",
    "docker.remote-tls.client-certificate",
    "docker.remote-tls.client-key",
    "oci.pull-credential",
}
if any(intent not in allowed_intents for _, intent in operations):
    raise SystemExit("denial authorized an unexpected secret intent")
if selections:
    raise SystemExit("denied remote Docker TLS use selected a secret version")
operation_correlations = {correlation for correlation, _ in operations}
runtime_provider = [
    (correlation, outcome, intent)
    for correlation, outcome, intent in provider
    if correlation in operation_correlations
]
if any(outcome == "resolved" for _, outcome, _ in runtime_provider):
    raise SystemExit("denied remote Docker TLS use resolved secret material")
if case == "wrong-workspace":
    if operations or runtime_provider:
        raise SystemExit("wrong-workspace denial escaped operations")
elif case == "wrong-intent":
    if runtime_provider:
        raise SystemExit("wrong-intent denial reached provider IO")
elif case == "revoked-version":
    if not operations or not any(
        outcome == "revoked"
        and intent == "docker.remote-tls.ca-certificate"
        for _, outcome, intent in runtime_provider
    ):
        raise SystemExit("revoked-version denial lacked correlated provider evidence")
elif case == "provider-unavailable":
    if not operations or runtime_provider:
        raise SystemExit("unavailable-provider denial evidence was inconsistent")
else:
    raise SystemExit("unknown remote Docker TLS denial case")
'
}

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
  "$POSTGRES_IMAGE")"

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
  --hostname remote-docker \
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

if command -v gh >/dev/null 2>&1 && GHCR_TOKEN="$(gh auth token 2>/dev/null)"; then
  BOOTSTRAP_DIR="$BOOTSTRAP_DIR" GHCR_TOKEN="$GHCR_TOKEN" python3 -c '
import json
import os
from pathlib import Path

base = Path(os.environ["BOOTSTRAP_DIR"])
base.joinpath("ghcr-pull-credential.json").write_text(
    json.dumps(
        {"username": "OpenJ92", "password": os.environ["GHCR_TOKEN"]},
        separators=(",", ":"),
        sort_keys=True,
    ),
    encoding="utf-8",
)
base.joinpath("ghcr-token-sentinel").write_text(
    os.environ["GHCR_TOKEN"],
    encoding="utf-8",
)
'
  unset GHCR_TOKEN
fi
if [ ! -s "$BOOTSTRAP_DIR/ghcr-pull-credential.json" ]; then
  echo "GHCR pull authority is unavailable" >&2
  exit 1
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
    "oci.pull-credential",
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
denial_workspaces = [
    f"{workspace_id}-denial-{case}"
    for case in (
        "wrong-workspace",
        "wrong-intent",
        "revoked-version",
        "provider-unavailable",
    )
]
all_workspaces = [
    workspace_id,
    *denial_workspaces,
    f"{workspace_id}-denial-wrong-workspace-source",
]
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
    "runtime-authority:use",
    "secret-provider:register",
    "secret-provider:read",
    "secret-provider:use",
]
worker_scopes = ["execution:operate", "secret-provider:use"]
print(json.dumps([{
    "credential": "present",
    "subject_id": "hosted-operator",
    "kind": "operator",
    "workspace_grants": {
        candidate_workspace: operator_scopes
        for candidate_workspace in all_workspaces
    },
}, {
    "credential": "worker-present",
    "subject_id": "hosted-worker",
    "kind": "worker",
    "workspace_grants": {
        candidate_workspace: worker_scopes
        for candidate_workspace in all_workspaces
    },
}], separators=(",", ":"), sort_keys=True))
'
)"

PROVIDER_ROUTES_JSON='{"source-live-secrets":"http://cpk-secrets:8081"}'
PROVIDER_BOOTSTRAP_FILES_JSON='{"secret://bootstrap/provider/client-token":"/run/secrets/cpk-provider/client-token"}'
start_secrets
start_server

host_inventory "$HOST_INVENTORY_BEFORE"
run_controller deploy
assert_host_inventory_unchanged
assert_no_tls_temp_directory
remote_inventory
if ! grep -q '^container:' "$REMOTE_INVENTORY" \
  || ! grep -q '^network:' "$REMOTE_INVENTORY"; then
  echo "remote Docker TLS deployment did not create labelled workload resources" >&2
  cat "$REMOTE_INVENTORY" >&2
  exit 1
fi
REMOTE_CONTAINER="$(docker exec "$DIND_CONTAINER" docker ps -q \
  --filter "label=$WORKSPACE_LABEL" | head -n 1)"
EXPECTED_HELLO_IMAGE="$(SERVERS_REPO="$SERVERS_REPO" python3 -c '
import json
import os
from pathlib import Path
document = json.loads(
    Path(os.environ["SERVERS_REPO"], "products/hello_server/product.cpk.json").read_text()
)
image = document["product"]["image"]
print("{}/{}@{}".format(image["registry"], image["repository"], image["digest"]))
')"
ACTUAL_REMOTE_IMAGE="$(docker exec "$DIND_CONTAINER" docker inspect \
  --format '{{.Config.Image}}' "$REMOTE_CONTAINER")"
if [ "$ACTUAL_REMOTE_IMAGE" != "$EXPECTED_HELLO_IMAGE" ]; then
  echo "remote daemon realized an unexpected workload image" >&2
  exit 1
fi
docker logs "$SERVER_CONTAINER" >>"$SERVER_LOG" 2>&1 || true
docker logs "$SECRETS_CONTAINER" >>"$PROVIDER_LOG" 2>&1 || true

docker rm -f "$SERVER_CONTAINER" "$SECRETS_CONTAINER" >/dev/null
SERVER_CONTAINER=""
SECRETS_CONTAINER=""
start_secrets
start_server

host_inventory "$HOST_INVENTORY_BEFORE"
run_controller resume
assert_host_inventory_unchanged
assert_no_tls_temp_directory
remote_inventory
if [ -s "$REMOTE_INVENTORY" ]; then
  echo "remote Docker TLS teardown left labelled nested-daemon resources" >&2
  cat "$REMOTE_INVENTORY" >&2
  exit 1
fi
docker logs "$SERVER_CONTAINER" >>"$SERVER_LOG" 2>&1 || true
docker logs "$SECRETS_CONTAINER" >>"$PROVIDER_LOG" 2>&1 || true

for denial_case in \
  wrong-workspace \
  wrong-intent \
  revoked-version \
  provider-unavailable
do
  denial_workspace="$WORKSPACE_ID-denial-$denial_case"
  host_before="$DENIAL_EVIDENCE_DIR/$denial_case-host-before.txt"
  host_after="$DENIAL_EVIDENCE_DIR/$denial_case-host-after.txt"
  remote_before="$DENIAL_EVIDENCE_DIR/$denial_case-remote-before.txt"
  remote_after="$DENIAL_EVIDENCE_DIR/$denial_case-remote-after.txt"

  run_controller deny "$denial_case" prepare
  host_denial_inventory "$host_before"
  remote_denial_inventory "$remote_before"
  if [ "$denial_case" = "provider-unavailable" ]; then
    docker stop "$SECRETS_CONTAINER" >/dev/null
  fi
  run_controller deny "$denial_case" execute
  if [ "$denial_case" = "provider-unavailable" ]; then
    docker start "$SECRETS_CONTAINER" >/dev/null
    wait_for_secrets
  fi
  host_denial_inventory "$host_after"
  remote_denial_inventory "$remote_after"
  assert_denial_inventory_unchanged "$host_before" "$host_after" "host Docker"
  assert_denial_inventory_unchanged "$remote_before" "$remote_after" "remote DIND"
  assert_no_tls_temp_directory
  assert_denial_audit "$denial_case" "$denial_workspace"
done

docker logs "$SERVER_CONTAINER" >>"$SERVER_LOG" 2>&1 || true
docker logs "$SECRETS_CONTAINER" >>"$PROVIDER_LOG" 2>&1 || true

docker exec "$POSTGRES_CONTAINER" psql -U cpk -d cpk -At -F '|' -c "
SELECT correlation_id, use_intent, run_id, activity_id, effect_id
FROM cpk_secret_use_authorizations
WHERE workspace_id = '$WORKSPACE_ID'
ORDER BY correlation_id;
" >"$OPERATIONS_AUTHORIZATIONS"
docker exec -e WORKSPACE_ID="$WORKSPACE_ID" "$SECRETS_CONTAINER" python -c '
import os
import sqlite3

connection = sqlite3.connect("/var/lib/cpk-secrets/secrets.sqlite3")
rows = connection.execute(
    """
    SELECT correlation_id, intent, version_id
    FROM secret_resolution_selections
    WHERE workspace_id = ?
    ORDER BY correlation_id
    """,
    (os.environ["WORKSPACE_ID"],),
).fetchall()
for row in rows:
    print("|".join(str(value) for value in row))
' >"$PROVIDER_SELECTIONS"

OPERATIONS_AUTHORIZATIONS="$OPERATIONS_AUTHORIZATIONS" \
PROVIDER_SELECTIONS="$PROVIDER_SELECTIONS" python3 -c '
import os
from collections import defaultdict
from pathlib import Path

tls_intents = {
    "docker.remote-tls.ca-certificate",
    "docker.remote-tls.client-certificate",
    "docker.remote-tls.client-key",
}
oci_pull_intent = "oci.pull-credential"
operations = [
    tuple(line.split("|"))
    for line in Path(os.environ["OPERATIONS_AUTHORIZATIONS"]).read_text().splitlines()
    if line
]
provider = [
    tuple(line.split("|"))
    for line in Path(os.environ["PROVIDER_SELECTIONS"]).read_text().splitlines()
    if line
]
if not operations or not provider:
    raise SystemExit("operations/provider secret-use evidence was empty")
by_effect = defaultdict(set)
run_ids = set()
for correlation, intent, run_id, activity_id, effect_id in operations:
    if not all((correlation, intent, run_id, activity_id, effect_id)):
        raise SystemExit("operations TLS authorization evidence was incomplete")
    by_effect[effect_id].add(intent)
    run_ids.add(run_id)
allowed_intent_sets = (tls_intents, tls_intents | {oci_pull_intent})
if any(intents not in allowed_intent_sets for intents in by_effect.values()):
    raise SystemExit("an effect did not preserve exact Docker TLS and OCI pull intents")
if not any(oci_pull_intent in intents for intents in by_effect.values()):
    raise SystemExit("no product effect authorized the private OCI pull credential")
if len(run_ids) < 3:
    raise SystemExit("restart acceptance did not correlate deploy/update/teardown runs")
provider_pairs = {(correlation, intent) for correlation, intent, version in provider if version}
operation_pairs = {(correlation, intent) for correlation, intent, *_ in operations}
if len(provider_pairs) != len(provider):
    raise SystemExit("provider selected-version evidence was incomplete or duplicated")
if not provider_pairs.issubset(operation_pairs):
    raise SystemExit("provider resolved a secret without operations authorization")
provider_intents = {intent for _, intent in provider_pairs}
if not tls_intents.issubset(provider_intents) or oci_pull_intent not in provider_intents:
    raise SystemExit("provider did not resolve the required TLS and OCI pull material")
resolved_run_ids = {
    run_id
    for correlation, intent, run_id, *_ in operations
    if (correlation, intent) in provider_pairs
}
if len(resolved_run_ids) < 3:
    raise SystemExit("provider evidence did not span deploy/update/teardown runs")
'

docker exec "$POSTGRES_CONTAINER" pg_dump -U cpk -d cpk >"$OPERATIONS_DUMP"
for secret_file in ca.pem cert.pem key.pem client-token ghcr-pull-credential.json ghcr-token-sentinel; do
  if grep -F -f "$BOOTSTRAP_DIR/$secret_file" "$SERVER_LOG" >/dev/null 2>&1; then
    echo "cpk-server logs contain forbidden remote-TLS material" >&2
    exit 1
  fi
  if grep -F -f "$BOOTSTRAP_DIR/$secret_file" "$PROVIDER_LOG" >/dev/null 2>&1; then
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
echo "cpk-server remote Docker TLS durable-custody graph/restart smoke passed"
