#!/usr/bin/env bash
set -euo pipefail

IMAGE="${CPK_TCP_SWITCH_IMAGE:-control-plane-kit-test:local}"
NETWORK="cpk-tcp-switch-live"
BLUE="cpk-tcp-blue"
GREEN="cpk-tcp-green"
SWITCH="cpk-tcp-switch"

cleanup() {
  docker rm -f "$SWITCH" "$GREEN" "$BLUE" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}

trap cleanup EXIT
cleanup
docker build --target test -t "$IMAGE" . >/dev/null
docker network create "$NETWORK" >/dev/null

docker run -d --name "$BLUE" --network "$NETWORK" \
  -e CPK_TCP_REPLY="blue:" \
  "$IMAGE" python tests/fixtures/tcp_reply_server.py >/dev/null
docker run -d --name "$GREEN" --network "$NETWORK" \
  -e CPK_TCP_REPLY="green:" \
  "$IMAGE" python tests/fixtures/tcp_reply_server.py >/dev/null
docker run -d --name "$SWITCH" --network "$NETWORK" \
  -e CPK_TCP_SWITCH_BLOCK_ID="tcp-switch" \
  -e CPK_TCP_SWITCH_TARGET_A="tcp://$BLUE:7000" \
  -e CPK_TCP_SWITCH_TARGET_B="tcp://$GREEN:7000" \
  -e CPK_TCP_SWITCH_ACTIVE_TARGET="target-a" \
  -e CPK_TCP_SWITCH_MODE="active-target" \
  -e CPK_CONTROL_TOKEN="tcp-switch-live-token" \
  "$IMAGE" uvicorn \
    control_plane_kit.servers.tcp_switch:create_tcp_switch_app_from_environment \
    --factory --host 0.0.0.0 --port 8080 >/dev/null

attempt=0
until docker run --rm --network "$NETWORK" "$IMAGE" python -c \
  "from urllib.request import Request, urlopen; urlopen(Request('http://$SWITCH:8080/__deploy/health', headers={'Authorization': 'Bearer tcp-switch-live-token'}), timeout=2)" \
  >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    docker logs "$SWITCH" >&2
    exit 1
  fi
  sleep 1
done

docker run --rm --network "$NETWORK" "$IMAGE" python tests/live_tcp_switch.py
cleanup

if docker ps -a --format '{{.Names}}' | grep -Eq "^($BLUE|$GREEN|$SWITCH)$"; then
  echo "TCP switch live containers remain after cleanup" >&2
  exit 1
fi
if docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "TCP switch live network remains after cleanup" >&2
  exit 1
fi
