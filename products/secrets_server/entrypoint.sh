#!/bin/sh
set -eu

: "${CPK_SECRETS_MASTER_KEY_FILE:=/run/secrets/cpk-secrets/master.key}"
: "${CPK_SECRETS_CREDENTIALS_FILE:=/run/secrets/cpk-secrets/credentials.json}"
export CPK_SECRETS_MASTER_KEY_FILE CPK_SECRETS_CREDENTIALS_FILE

exec "$@"
