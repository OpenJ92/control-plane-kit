"""Tiny byte-oriented target fixture for the live TCP switch proof."""

from __future__ import annotations

import os
import socketserver


class ReplyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        payload = self.request.recv(65_536)
        self.request.sendall(os.environ["CPK_TCP_REPLY"].encode() + payload)


if __name__ == "__main__":
    with socketserver.ThreadingTCPServer(("0.0.0.0", 7000), ReplyHandler) as server:
        server.serve_forever()
