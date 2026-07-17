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


class WorkspaceLifecycle(StrEnum):
    """Lifecycle states shared by workspace and instance records."""

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ARCHIVED = "archived"
    DECONSTRUCTED = "deconstructed"
    DELETED = "deleted"
    FAILED = "failed"


class BlockFamily(StrEnum):
    """Closed authoring roles retained by compiled graph nodes."""

    APPLICATION = "application"
    DATA = "data"
    PROXY = "proxy"
