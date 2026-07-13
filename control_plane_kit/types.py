"""Closed-ish primitive types for topology values."""

from __future__ import annotations

from enum import StrEnum


class Protocol(StrEnum):
    """Network protocols understood by socket compatibility checks."""

    HTTP = "http"
    POSTGRES = "postgres"
    TCP = "tcp"


class EndpointScope(StrEnum):
    """Descriptive endpoint visibility."""

    LOCAL = "local"
    PRIVATE = "private"
    PUBLIC = "public"


class RuntimeKind(StrEnum):
    """Runtime contexts supplied by the recipe tree."""

    DOCKER = "docker"
    EXTERNAL = "external"
    DRY_RUN = "dry-run"
    AWS = "aws"
    KUBERNETES = "kubernetes"
