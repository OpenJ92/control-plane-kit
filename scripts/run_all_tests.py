from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def product_test_roots() -> tuple[Path, ...]:
    product_roots = tuple(
        sorted(path for path in (ROOT / "products").iterdir() if path.is_dir())
    )
    owned_products = tuple(
        path
        for path in product_roots
        if tuple(path.glob("product*.json"))
        or (path / "src").is_dir()
        or (path / "Dockerfile").is_file()
    )
    missing = tuple(
        path.relative_to(ROOT).as_posix()
        for path in owned_products
        if not (path / "tests").is_dir()
    )
    if missing:
        raise RuntimeError("product test package is missing: " + ", ".join(missing))
    return tuple(path / "tests" for path in owned_products)


def main() -> int:
    run([sys.executable, "scripts/apply_coordinates.py", "--check"])
    run([sys.executable, "-m", "compileall", "-q", "src", "products", "scripts", "tests"])
    for test_root in (ROOT / "tests", *product_test_roots()):
        run(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                test_root.relative_to(ROOT).as_posix(),
                "-v",
            ]
        )
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
