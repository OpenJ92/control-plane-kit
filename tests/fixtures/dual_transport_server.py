"""Tiny TCP/UDP fixture for the live transport acceptance proof."""

from __future__ import annotations

import os
import socket
import sys
from threading import Thread
import time


HOST = "0.0.0.0"
PORT = int(os.environ.get("TRANSPORT_FIXTURE_PORT", "5353"))
REQUEST = b"probe"


def serve() -> None:
    tcp = Thread(target=_serve_tcp, daemon=True)
    udp = Thread(target=_serve_udp, daemon=True)
    tcp.start()
    udp.start()
    tcp.join()
    udp.join()


def probe(transport: str, host: str) -> None:
    deadline = time.monotonic() + 10
    while True:
        try:
            response = _probe_once(transport, host)
            expected = f"{transport}-ok".encode()
            if response != expected:
                raise RuntimeError(
                    f"unexpected {transport} response: {response!r}"
                )
            print(response.decode())
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def _serve_tcp() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen()
        while True:
            connection, _ = server.accept()
            with connection:
                if connection.recv(len(REQUEST)) == REQUEST:
                    connection.sendall(b"tcp-ok")


def _serve_udp() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind((HOST, PORT))
        while True:
            payload, address = server.recvfrom(512)
            if payload == REQUEST:
                server.sendto(b"udp-ok", address)


def _probe_once(transport: str, host: str) -> bytes:
    socket_type = {
        "tcp": socket.SOCK_STREAM,
        "udp": socket.SOCK_DGRAM,
    }[transport]
    with socket.socket(socket.AF_INET, socket_type) as client:
        client.settimeout(1)
        client.connect((host, PORT))
        client.sendall(REQUEST)
        return client.recv(512)


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["serve"]:
            serve()
        case ["probe", transport, host]:
            probe(transport, host)
        case _:
            raise SystemExit("usage: dual_transport_server.py serve|probe TRANSPORT HOST")
