"""Bounded noncanonical product display; never material or admission authority."""

from __future__ import annotations

import json
from typing import Callable

from control_plane_kit_core.products import ContainerServerProduct, ProductReferenceCodec, product_configuration_slots
from control_plane_kit_operations.products import (
    ProductRegistrationNotFound,
    RegisteredProductStatus,
    registration_id_for,
)
from control_plane_kit_operations.read_pages import (
    ReadCollection,
    ReadPageRequest,
    WorkspaceReadScope,
)
from ._redaction import _redact_descriptor_value
from .errors import ReadModelError
from .protocols import RegisteredProductStore


_MAX_RESPONSE_BYTES = 1048576


class _DirectoryRead:
    def __init__(self, value: dict[str, object]) -> None:
        try:
            encoded = json.dumps(
                value, ensure_ascii=True, allow_nan=False, separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > _MAX_RESPONSE_BYTES:
                raise ValueError
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise ReadModelError("product directory response is malformed or oversized") from None
        self._encoded = encoded

    def descriptor(self) -> dict[str, object]:
        return json.loads(self._encoded)


def _summary(value: object, workspace_id: str) -> dict[str, object]:
    try:
        if type(value) is not dict:
            raise ValueError
        reference = ProductReferenceCodec().decode(value["reference"])
        if value["registration_id"] != registration_id_for(workspace_id, reference):
            raise ValueError
        for key, limit in (("display_name", 128), ("description", 1024)):
            text = value[key]
            if text is not None and (
                type(text) is not str or len(text) > limit or not text.strip() or "\x00" in text
            ):
                raise ValueError
        if value["product_family"] not in {"server", "data-service"}:
            raise ValueError
        status = RegisteredProductStatus(value["status"]).value
        return {
            "registration_id": value["registration_id"],
            "reference": reference.descriptor(),
            "status": status,
            "display_name": value["display_name"],
            "description": value["description"],
            "product_family": value["product_family"],
        }
    except (KeyError, ValueError, TypeError):
        raise ReadModelError("product directory record is malformed or oversized") from None


def _material_shape(delivery) -> dict[str, object]:
    raw = delivery.descriptor()
    # Never include reference_id, credential, actual value, or provider handle.
    result = {
        key: raw[key] for key in (
            "kind", "environment_name", "target_path", "file_mode", "intent",
        ) if key in raw
    }
    if raw.get("path_binding") is not None:
        result["path_binding"] = {"environment_name": raw["path_binding"]["environment_name"]}
    return result


def _contract(product: ContainerServerProduct) -> dict[str, object]:
    contract = product.runtime_contract
    return {
        "providers": [
            {"name": socket.name, "protocol": socket.protocol.descriptor()}
            for socket in contract.sockets.providers
        ],
        "requirements": [
            {
                "name": socket.name, "protocol": socket.protocol.descriptor(),
                "env_names": list(socket.env_bindings), "required": socket.required,
                "binding": socket.binding.value,
                "material_requirements": [_material_shape(d) for d in socket.secret_deliveries],
            } for socket in contract.sockets.requirements
        ],
        "provider_ports": [port.descriptor() for port in contract.provider_ports],
        "public_environment_names": [binding.name for binding in contract.public_environment],
        "configuration": [
            {
                "artifact_id": artifact.artifact_id, "target_path": artifact.target_path,
                "media_type": artifact.media_type.value, "file_mode": artifact.file_mode.value,
            } for artifact in product_configuration_slots(contract)
        ],
        "material_requirements": [_material_shape(d) for d in contract.secret_deliveries],
        "retained_data_mounts": [
            {"resource_id": mount.resource_id, "target_path": mount.target_path}
            for mount in contract.retained_data_mounts
        ],
        "capabilities": [capability.value for capability in contract.capabilities],
        "lifecycle": contract.lifecycle.descriptor(),
        "verification": [
            {"kind": check.descriptor()["kind"], "check_id": check.check_id,
             "provider_socket": check.provider_socket}
            for check in contract.verification.checks
        ],
        "control_surfaces": [
            {"provider_socket_name": surface.provider_socket_name.value,
             "variable_names": [variable.variable_name.value for variable in surface.variables],
             "health_reads": [read.value for read in surface.health_reads]}
            for surface in contract.control_surfaces
        ],
    }


class _ProductDirectoryReadProjection:
    def __init__(self, require_workspace: Callable, store: RegisteredProductStore | None) -> None:
        self._require_workspace = require_workspace
        self._store = store

    def _require(self, workspace_id: str) -> None:
        self._require_workspace(workspace_id)
        if self._store is None:
            raise ReadModelError("product directory store is not configured")

    def page(self, request: ReadPageRequest) -> _DirectoryRead:
        if request.collection is not ReadCollection.REGISTERED_PRODUCTS:
            raise ReadModelError("product directory page is malformed")
        workspace_id = request.scope.workspace_id
        self._require(workspace_id)
        page = self._store.directory_page(request).map(lambda value: _summary(value, workspace_id))
        return _DirectoryRead(page.descriptor())

    def detail(self, workspace_id: str, digest: str) -> _DirectoryRead:
        WorkspaceReadScope(workspace_id)
        self._require(workspace_id)
        try:
            record = self._store.directory_detail(workspace_id, digest)
        except ProductRegistrationNotFound:
            raise ReadModelError("missing registered product") from None
        product = record.descriptor_document.product
        summary = _summary({
            "registration_id": record.registration_id,
            "reference": record.reference.descriptor(), "status": record.status.value,
            "display_name": product.display_name, "description": product.description,
            "product_family": product.product_family.value,
        }, workspace_id)
        image = product.image
        # Arbitrary image provenance metadata is excluded along with registration metadata.
        projection = {
            "image": {"registry": image.registry, "repository": image.repository,
                      "digest": image.digest, "tag": image.tag,
                      "platforms": [platform.descriptor() for platform in image.platforms]},
            "source": record.source.descriptor(), "imported_at": record.imported_at,
            "contract": _contract(product),
        }
        safe = _redact_descriptor_value("product_projection", projection)
        return _DirectoryRead({
            "workspace_id": workspace_id, "kind": "registered-product-detail",
            "product": {**summary, "projection_kind": "noncanonical-product-display", **safe},
        })
