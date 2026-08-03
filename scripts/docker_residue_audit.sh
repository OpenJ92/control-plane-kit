#!/bin/sh
set -eu

LABEL="org.openj92.project=control-plane-kit-servers"

# Pottery Factory resources are preserved because this audit only inspects the
# exact control-plane-kit-servers ownership label.
containers="$(docker ps -a --filter "label=$LABEL" --format '{{.ID}} {{.Names}} {{.Status}}')"
networks="$(docker network ls --filter "label=$LABEL" --format '{{.Name}}')"
volumes="$(docker volume ls --filter "label=$LABEL" --format '{{.Name}}')"

if [ -n "$containers" ]; then
  printf 'owned container residue detected:\n%s\n' "$containers" >&2
  exit 1
fi

if [ -n "$networks" ]; then
  printf 'owned network residue detected:\n%s\n' "$networks" >&2
  exit 1
fi

if [ -n "$volumes" ]; then
  printf 'owned volume residue detected:\n%s\n' "$volumes" >&2
  exit 1
fi

printf 'control-plane-kit-servers Docker residue audit passed\n'
