from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

from control_plane_kit_servers.catalogue import publish_catalogue


ROOT = Path(__file__).resolve().parents[1]
COORDINATES = ROOT / "coordinates" / "server-products.json"
PYPROJECT = ROOT / "pyproject.toml"
CPK_SERVER_DOCKERFILE = ROOT / "products" / "cpk_server" / "Dockerfile"
CPK_LOCAL_GATEWAY_DOCKERFILE = (
    ROOT / "products" / "cpk_local_gateway" / "Dockerfile"
)
CATALOGUE = ROOT / "catalogue" / "products.json"
PACKAGED_CATALOGUE = ROOT / "src" / "control_plane_kit_servers" / "catalogue.json"


class CoordinateError(ValueError):
    """Raised when generated server product coordinates are invalid."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply or check generated server product coordinates.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if generated files are not already up to date",
    )
    args = parser.parse_args(argv)

    updates = generate_updates(load_coordinates(COORDINATES))
    if args.check:
        stale = [str(path.relative_to(ROOT)) for path, content in updates.items() if path.read_bytes() != content]
        if stale:
            raise CoordinateError(
                "generated coordinate files are stale: " + ", ".join(sorted(stale))
            )
        return 0

    for path, content in updates.items():
        path.write_bytes(content)
    report = publish_catalogue(CATALOGUE, PACKAGED_CATALOGUE)
    print(json.dumps(report, sort_keys=True))
    return 0


def load_coordinates(path: Path) -> Mapping[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise CoordinateError("coordinates root must be an object")
    if raw.get("schema") != "cpk-servers.coordinates":
        raise CoordinateError("coordinates schema must be cpk-servers.coordinates")
    upstreams = raw.get("upstreams")
    products = raw.get("products")
    if not isinstance(upstreams, Mapping):
        raise CoordinateError("coordinates upstreams must be an object")
    if not isinstance(products, list):
        raise CoordinateError("coordinates products must be a list")
    _commit(upstreams, "control_plane_kit_commit")
    _commit(upstreams, "control_plane_kit_interpreters_commit")
    seen: set[str] = set()
    for product in products:
        if not isinstance(product, Mapping):
            raise CoordinateError("coordinate product must be an object")
        product_id = _required_text(product, "product_id")
        if product_id in seen:
            raise CoordinateError(f"duplicate product coordinate: {product_id}")
        seen.add(product_id)
        _relative(product, "owner_directory")
        _relative(product, "descriptor_path")
        _commit(product, "source_commit")
        image = product.get("image")
        if not isinstance(image, Mapping):
            raise CoordinateError(f"{product_id} image must be an object")
        _required_text(image, "registry")
        _required_text(image, "repository")
        _required_text(image, "tag")
        digest = _required_text(image, "digest")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise CoordinateError(f"{product_id} image digest must be sha256")
    return raw


def generate_updates(coordinates: Mapping[str, Any]) -> dict[Path, bytes]:
    upstreams = coordinates["upstreams"]
    products = tuple(coordinates["products"])
    updates: dict[Path, bytes] = {}
    cpk_commit = str(upstreams["control_plane_kit_commit"])
    interpreters_commit = str(upstreams["control_plane_kit_interpreters_commit"])

    updates[PYPROJECT] = _replace_dependency_pins(
        PYPROJECT.read_text(encoding="utf-8"),
        cpk_commit=cpk_commit,
        interpreters_commit=interpreters_commit,
    ).encode("utf-8")
    updates[CPK_SERVER_DOCKERFILE] = _replace_dependency_pins(
        CPK_SERVER_DOCKERFILE.read_text(encoding="utf-8"),
        cpk_commit=cpk_commit,
        interpreters_commit=interpreters_commit,
    ).encode("utf-8")
    updates[CPK_LOCAL_GATEWAY_DOCKERFILE] = _replace_dependency_pins(
        CPK_LOCAL_GATEWAY_DOCKERFILE.read_text(encoding="utf-8"),
        cpk_commit=cpk_commit,
        interpreters_commit=interpreters_commit,
    ).encode("utf-8")

    catalogue_products: list[dict[str, str]] = []
    for product in products:
        descriptor_path = ROOT / str(product["descriptor_path"])
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        rewritten = _with_image_coordinates(descriptor, product)
        encoded = _compact_json(rewritten)
        updates[descriptor_path] = encoded
        catalogue_products.append(
            {
                "product_id": str(product["product_id"]),
                "owner_directory": str(product["owner_directory"]),
                "descriptor_path": str(product["descriptor_path"]),
                "descriptor_sha256": hashlib.sha256(encoded).hexdigest(),
                "source_commit": str(product["source_commit"]),
                "image_ref": _image_ref(product),
                "image_digest": str(product["image"]["digest"]),
                "status": "completed",
            }
        )

    updates[CATALOGUE] = (
        json.dumps(
            {
                "schema": "cpk-servers.descriptor-catalogue",
                "products": catalogue_products,
            },
            indent=2,
            sort_keys=False,
        ).encode("utf-8")
        + b"\n"
    )
    return updates


def _replace_dependency_pins(
    text: str,
    *,
    cpk_commit: str,
    interpreters_commit: str,
) -> str:
    text = re.sub(
        r"https://github\.com/OpenJ92/control-plane-kit/archive/[0-9a-f]{40}\.zip",
        f"https://github.com/OpenJ92/control-plane-kit/archive/{cpk_commit}.zip",
        text,
    )
    return re.sub(
        r"https://github\.com/OpenJ92/control-plane-kit-interpreters/archive/[0-9a-f]{40}\.zip",
        "https://github.com/OpenJ92/control-plane-kit-interpreters/archive/"
        f"{interpreters_commit}.zip",
        text,
    )


def _with_image_coordinates(
    descriptor: Mapping[str, Any],
    product: Mapping[str, Any],
) -> Mapping[str, Any]:
    rewritten = copy.deepcopy(descriptor)
    image = rewritten["product"]["image"]
    coordinates = product["image"]
    image["registry"] = coordinates["registry"]
    image["repository"] = coordinates["repository"]
    image["tag"] = coordinates["tag"]
    image["digest"] = coordinates["digest"]
    provenance = image.get("provenance")
    if isinstance(provenance, dict) and "source-commit" in provenance:
        provenance["source-commit"] = product["source_commit"]
    return rewritten


def _image_ref(product: Mapping[str, Any]) -> str:
    image = product["image"]
    return f"{image['registry']}/{image['repository']}:{image['tag']}"


def _compact_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=False).encode("utf-8")


def _required_text(value: Mapping[str, Any], key: str) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate:
        raise CoordinateError(f"{key} must be a non-empty string")
    return candidate


def _commit(value: Mapping[str, Any], key: str) -> str:
    candidate = _required_text(value, key)
    if not re.fullmatch(r"[0-9a-f]{40}", candidate):
        raise CoordinateError(f"{key} must be a 40-character lowercase git sha")
    return candidate


def _relative(value: Mapping[str, Any], key: str) -> str:
    candidate = _required_text(value, key)
    path = Path(candidate)
    if path.is_absolute() or ".." in path.parts:
        raise CoordinateError(f"{key} must be a safe relative path")
    return candidate


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except CoordinateError as error:
        print(f"coordinate error: {error}", file=sys.stderr)
        raise SystemExit(1) from error
