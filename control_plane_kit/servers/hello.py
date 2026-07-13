"""Tiny parameterized FastAPI app used for router switch demos."""

from __future__ import annotations

import os

from control_plane_kit.servers._fastapi import require_fastapi


def create_hello_app(world: str | None = None):
    """Return an app whose response is parameterized by ``world``.

    The app deliberately has one interesting data route. Running two copies with
    different ``HELLO_WORLD`` values lets a router switch prove that the client
    can send the same request and receive a different downstream response.
    """

    _Depends, FastAPI, _Header, _HTTPException, _Request = require_fastapi()
    resolved_world = world if world is not None else os.environ.get("HELLO_WORLD", "world")
    app = FastAPI(title=f"Control Plane Kit Hello: {resolved_world}", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "world": resolved_world}

    @app.get("/hello")
    def hello() -> dict[str, str]:
        return {"message": f"Hello World {resolved_world}"}

    return app
