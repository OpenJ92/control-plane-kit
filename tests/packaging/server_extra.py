"""Acceptance proof for the complete runnable-server dependency bundle."""

from __future__ import annotations

import psycopg
import uvicorn

from control_plane_kit.discovery_server.main import main as run_discovery_server
from control_plane_kit.idempotency_gateway.main import main as run_idempotency_server
from control_plane_kit.webhook_server.main import create_app_from_environment


if not psycopg.__version__ or not uvicorn.__version__:
    raise AssertionError("server extra dependencies are not importable")
if any(
    value is None
    for value in (
        run_discovery_server,
        run_idempotency_server,
        create_app_from_environment,
    )
):
    raise AssertionError("runnable server composition roots are not importable")

print("server extra acceptance passed")
