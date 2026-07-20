"""Acceptance proof for Postgres-backed operational composition."""

from __future__ import annotations

import psycopg

from control_plane_kit.discovery_registry import PostgresDiscoveryUnitOfWork
from control_plane_kit.stores import PostgresUnitOfWork


if not psycopg.__version__:
    raise AssertionError("Postgres extra did not install psycopg")
if any(
    value is None
    for value in (
        PostgresDiscoveryUnitOfWork,
        PostgresUnitOfWork,
    )
):
    raise AssertionError("Postgres operational entrances are not importable")

print("postgres extra acceptance passed")
