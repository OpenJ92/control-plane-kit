from __future__ import annotations

import subprocess
import sys


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def main() -> int:
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    run([
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "products/cpk_server/tests",
        "-v",
    ])
    run([
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "products/hello_server/tests",
        "-v",
    ])
    run([
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "products/http_active_router/tests",
        "-v",
    ])
    run([
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "products/http_multiplexer/tests",
        "-v",
    ])
    run([
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "products/cpk_local_gateway/tests",
        "-v",
    ])
    run([
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "products/cloudflared_connector/tests",
        "-v",
    ])
    run([
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "products/postgres_server/tests",
        "-v",
    ])
    run(
        [
            sys.executable,
            "scripts/product_image_lane.py",
            "--inventory",
            "coordination/product-inventory.json",
        ]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
