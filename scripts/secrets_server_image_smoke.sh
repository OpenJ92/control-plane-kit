#!/bin/sh
set -eu

IMAGE="${CPK_SECRETS_IMAGE:-control-plane-kit-secrets-server:local}"
BUILD_IMAGE="${CPK_SECRETS_BUILD_IMAGE:-1}"
RUN_ID="cpk-secrets-image-smoke-$$"
CONTAINER="${RUN_ID}-provider"
ROOT="$(mktemp -d)"
BOOTSTRAP_VOLUME="${RUN_ID}-bootstrap"
DATA_VOLUME="${RUN_ID}-data"
TOKEN="ephemeral-provider-token-$RUN_ID"
SECRET_VALUE="ephemeral-postgres-value-$RUN_ID"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker volume rm -f "$BOOTSTRAP_VOLUME" "$DATA_VOLUME" >/dev/null 2>&1 || true
  rm -rf "$ROOT"
}
trap cleanup EXIT INT TERM

chmod 700 "$ROOT"

if [ "$BUILD_IMAGE" = "1" ]; then
  docker build -f products/secrets_server/Dockerfile -t "$IMAGE" .
fi

docker volume create --label "cpk.test-run=$RUN_ID" "$BOOTSTRAP_VOLUME" >/dev/null
docker volume create --label "cpk.test-run=$RUN_ID" "$DATA_VOLUME" >/dev/null
docker run --rm -i \
  --user 0:0 \
  --mount "type=volume,src=$BOOTSTRAP_VOLUME,dst=/bootstrap" \
  --mount "type=volume,src=$DATA_VOLUME,dst=/data" \
  --entrypoint python \
  "$IMAGE" \
  - "$TOKEN" <<'PY'
import base64
import json
import os
from pathlib import Path
import sys

bootstrap = Path("/bootstrap")
token = sys.argv[1]
(bootstrap / "master.key").write_text(
    base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"),
    encoding="utf-8",
)
(bootstrap / "credentials.json").write_text(
    json.dumps(
        [
            {
                "subject": "image-smoke",
                "token": token,
                "grants": [
                    {"action": "secret.write", "workspace_id": "workspace-smoke"},
                    {
                        "action": "secret.resolve",
                        "workspace_id": "workspace-smoke",
                        "intents": ["postgres.password"],
                    },
                ],
            }
        ],
        separators=(",", ":"),
        sort_keys=True,
    ),
    encoding="utf-8",
)
for path in bootstrap.iterdir():
    path.chmod(0o600)
    os.chown(path, 10006, 10006)
os.chown("/bootstrap", 10006, 10006)
os.chown("/data", 10006, 10006)
PY

start_provider() {
  docker run -d \
    --name "$CONTAINER" \
    --label "cpk.test-run=$RUN_ID" \
    -p 127.0.0.1::8081 \
    --mount "type=volume,src=$BOOTSTRAP_VOLUME,dst=/run/secrets/cpk-secrets,readonly" \
    --mount "type=volume,src=$DATA_VOLUME,dst=/var/lib/cpk-secrets" \
    "$IMAGE" >/dev/null
}

wait_ready() {
  PORT="$(docker port "$CONTAINER" 8081/tcp | sed 's/.*://')"
  export PORT
  attempts=0
  until curl --fail --silent --show-error \
    "http://127.0.0.1:$PORT/health/ready" >/dev/null
  do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 30 ]; then
      docker logs "$CONTAINER" >&2
      echo "secrets-server did not become ready" >&2
      exit 1
    fi
    sleep 1
  done
}

start_provider
wait_ready

VALUE_BASE64="$(printf '%s' "$SECRET_VALUE" | base64 | tr -d '\n')"
curl --fail --silent --show-error \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"value_base64\":\"$VALUE_BASE64\",\"correlation_id\":\"write-1\"}" \
  "http://127.0.0.1:$PORT/v1/workspaces/workspace-smoke/secrets/postgres-password" \
  >"$ROOT/write.json"

if grep -F "$SECRET_VALUE" "$ROOT/write.json" >/dev/null; then
  echo "write response leaked secret material" >&2
  exit 1
fi

docker rm -f "$CONTAINER" >/dev/null
start_provider
wait_ready

curl --fail --silent --show-error \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"intent":"postgres.password","caller_subject":"image-smoke","correlation_id":"resolve-after-restart"}' \
  "http://127.0.0.1:$PORT/v1/workspaces/workspace-smoke/secrets/postgres-password/resolve" \
  >"$ROOT/resolve.json"

python3 - "$ROOT/resolve.json" "$SECRET_VALUE" <<'PY'
import base64
import json
from pathlib import Path
import sys

response = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert response["outcome"] == "resolved"
assert base64.b64decode(response["value_base64"]).decode("utf-8") == sys.argv[2]
PY

docker rm -f "$CONTAINER" >/dev/null
if docker ps -a --filter "label=cpk.test-run=$RUN_ID" --format '{{.ID}}' | grep .; then
  echo "secrets-server smoke left owned containers" >&2
  exit 1
fi
docker volume rm "$BOOTSTRAP_VOLUME" "$DATA_VOLUME" >/dev/null
if docker volume ls --filter "label=cpk.test-run=$RUN_ID" --format '{{.Name}}' | grep .; then
  echo "secrets-server smoke left owned volumes" >&2
  exit 1
fi

echo "secrets-server image smoke passed"
