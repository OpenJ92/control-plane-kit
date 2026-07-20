"""Client-side semantic proof for the Docker TCP switch fixture."""

from __future__ import annotations

import json
import socket
from urllib.error import HTTPError
from urllib.request import Request, urlopen


HOST = "cpk-tcp-switch"
TOKEN = "tcp-switch-live-token"


def exchange() -> bytes:
    with socket.create_connection((HOST, 7000), timeout=5) as client:
        client.sendall(b"payload")
        client.shutdown(socket.SHUT_WR)
        return client.recv(65_536)


def switch(*, authorized: bool) -> int:
    headers = {
        "Content-Type": "application/json",
        "X-Control-Plane-Request-Id": "tcp-switch-live-request",
        "Idempotency-Key": "tcp-switch-live-green",
    }
    if authorized:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = Request(
        f"http://{HOST}:8080/__deploy/active-target",
        data=json.dumps({"target_id": "target-b"}).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status
    except HTTPError as error:
        body = error.read().decode()
        if TOKEN in body:
            raise RuntimeError("control token leaked in unauthorized response") from error
        return error.code


if exchange() != b"blue:payload":
    raise RuntimeError("initial TCP exchange did not reach blue")
if switch(authorized=False) != 401:
    raise RuntimeError("unauthorized TCP switch mutation did not fail closed")
if exchange() != b"blue:payload":
    raise RuntimeError("unauthorized mutation changed the TCP data path")
if switch(authorized=True) != 200:
    raise RuntimeError("authenticated TCP switch mutation failed")
if exchange() != b"green:payload":
    raise RuntimeError("authenticated mutation did not select green")
print("TCP switch live proof passed: blue -> 401 -> blue -> green")
