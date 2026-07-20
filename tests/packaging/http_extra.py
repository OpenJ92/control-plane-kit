"""Acceptance proof for the focused HTTP extra and packaged templates."""

from __future__ import annotations

from importlib.resources import files

import fastapi
import httpx

from control_plane_kit.adapters import HttpVerificationInterpreter
from control_plane_kit.servers import hello_command
from control_plane_kit.interpreters.webhook_http import HttpWebhookDelivery


command = hello_command()
if command[:2] != ("python", "-c"):
    raise AssertionError("installed strict Hello template did not render a Python command")
template = files("control_plane_kit").joinpath("servers", "templates", "hello.py.j2")
if not template.is_file() or "StrictUndefined" in template.read_text():
    raise AssertionError("installed wheel is missing the expected Hello package template")
if not fastapi.__version__ or not httpx.__version__:
    raise AssertionError("HTTP extra dependencies are not importable")
if HttpVerificationInterpreter is None or HttpWebhookDelivery is None:
    raise AssertionError("HTTP operational entrances are not importable")

print("http extra acceptance passed")
