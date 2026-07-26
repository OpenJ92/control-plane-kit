#!/bin/sh
set -eu

IMAGE="${HELLO_SERVER_IMAGE:-localhost/control-plane-kit-servers/hello-server:local}"
BUILD_IMAGE="${HELLO_SERVER_BUILD_IMAGE:-1}"
CONTAINER="cpk-servers-hello-smoke"
PROJECT_LABEL="org.openj92.project=control-plane-kit-servers"
PRODUCT_LABEL="org.openj92.product=hello-server"
PORT="${HELLO_SERVER_SMOKE_PORT:-18080}"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}

cleanup
trap cleanup EXIT INT TERM

if [ "$BUILD_IMAGE" = "1" ]; then
  docker build -f products/hello_server/Dockerfile -t "$IMAGE" .
fi

docker run -d \
  --name "$CONTAINER" \
  --label "$PROJECT_LABEL" \
  --label "$PRODUCT_LABEL" \
  -e HELLO_MESSAGE="Hello, seed!" \
  -e HELLO_DEPENDENCIES_JSON="[]" \
  -p "127.0.0.1:$PORT:8000" \
  "$IMAGE" >/dev/null

for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "http://127.0.0.1:$PORT/health/ready" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

MESSAGE="$(curl -fsS "http://127.0.0.1:$PORT/")"
if [ "$MESSAGE" != "Hello, seed!" ]; then
  echo "unexpected hello response: $MESSAGE" >&2
  exit 1
fi

DEPENDENCIES="$(curl -fsS "http://127.0.0.1:$PORT/dependencies")"
if [ "$DEPENDENCIES" != "[]" ]; then
  echo "unexpected dependencies response: $DEPENDENCIES" >&2
  exit 1
fi

OBSERVATIONS="$(curl -fsS "http://127.0.0.1:$PORT/observations/requests")"
printf '%s' "$OBSERVATIONS" | grep -q '"count":1'
printf '%s' "$OBSERVATIONS" | grep -q '"method":"GET"'
printf '%s' "$OBSERVATIONS" | grep -q '"path":"/"'
if printf '%s' "$OBSERVATIONS" | grep -Eiq 'headers|body|secret'; then
  echo "observer evidence leaked forbidden request material: $OBSERVATIONS" >&2
  exit 1
fi

echo "hello-server image smoke passed: $MESSAGE"
