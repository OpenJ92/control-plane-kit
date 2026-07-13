"""Lazy FastAPI imports for optional server adapters."""

from __future__ import annotations


def require_fastapi():
    """Return FastAPI symbols or raise an install hint.

    The core algebra package does not require FastAPI. Server adapters are an
    optional interpretation layer and should fail only when that layer is used.
    """

    try:
        from fastapi import Depends, FastAPI, Header, HTTPException, Request
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "FastAPI server adapters require the optional 'server' extra: "
            "pip install control-plane-kit[server]"
        ) from exc
    return Depends, FastAPI, Header, HTTPException, Request
