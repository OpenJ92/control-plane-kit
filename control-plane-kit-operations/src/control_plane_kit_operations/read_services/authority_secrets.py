"""Public-safe authority and secret projections over durable truth."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from control_plane_kit_core.public_ingress import IngressAuthorityReference
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.secrets import SecretProviderId
from control_plane_kit_operations.ingress_authorities import (
    IngressAuthorityNotFound,
)
from control_plane_kit_operations.read_pages import ReadPage, ReadPageRequest
from control_plane_kit_operations.records import WorkspaceRecord
from control_plane_kit_operations.runtime_authorities import (
    RuntimeAuthorityNotFound,
)
from control_plane_kit_operations.secret_providers import (
    RegisteredSecretProvider,
    RegisteredSecretReference,
    SecretProviderNotFound,
)

from ._redaction import _redact_descriptor_value
from .errors import ReadModelError
from .models import FocusedDetailReadModel
from .protocols import (
    IngressAuthorityStore,
    RuntimeAuthorityDeliveryStore,
    RuntimeAuthorityStore,
    SecretProviderStore,
    SecretReferenceStore,
)


class _AuthoritySecretReadProjection:
    def __init__(
        self,
        require_workspace: Callable[[str], WorkspaceRecord],
        *,
        runtime_authority_store: RuntimeAuthorityStore | None,
        runtime_authority_delivery_store: RuntimeAuthorityDeliveryStore | None,
        ingress_authority_store: IngressAuthorityStore | None,
        secret_provider_store: SecretProviderStore | None,
        secret_reference_store: SecretReferenceStore | None,
    ) -> None:
        self._require_workspace = require_workspace
        self._runtime_authority_store = runtime_authority_store
        self._runtime_authority_delivery_store = runtime_authority_delivery_store
        self._ingress_authority_store = ingress_authority_store
        self._secret_provider_store = secret_provider_store
        self._secret_reference_store = secret_reference_store

    def runtime_authorities(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        workspace_id = request.scope.workspace_id
        self._require_workspace(workspace_id)
        if self._runtime_authority_store is None:
            raise ReadModelError("runtime authority store is not configured")
        return self._runtime_authority_store.active_page(request).map(
            lambda value: dict(_redacted_runtime_authority(value))
        )

    def runtime_authority_detail(
        self,
        workspace_id: str,
        authority_ref: RuntimeAuthorityReference,
    ) -> FocusedDetailReadModel:
        self._require_workspace(workspace_id)
        if self._runtime_authority_store is None:
            raise ReadModelError("runtime authority store is not configured")
        try:
            authority = self._runtime_authority_store.get(workspace_id, authority_ref)
        except (KeyError, RuntimeAuthorityNotFound) as exc:
            raise ReadModelError(
                f"missing runtime authority {authority_ref.reference_id!r}"
            ) from exc
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="runtime-authority-detail",
            payload={"runtime_authority": _redacted_runtime_authority(authority)},
        )

    def runtime_authority_deliveries(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        workspace_id = request.scope.workspace_id
        self._require_workspace(workspace_id)
        if self._runtime_authority_delivery_store is None:
            raise ReadModelError("runtime authority delivery store is not configured")
        return self._runtime_authority_delivery_store.active_page(request).map(
            lambda value: dict(_redacted_runtime_authority_delivery(value))
        )

    def runtime_authority_delivery_detail(
        self,
        workspace_id: str,
        authority_ref: RuntimeAuthorityReference,
    ) -> FocusedDetailReadModel:
        self._require_workspace(workspace_id)
        if self._runtime_authority_delivery_store is None:
            raise ReadModelError("runtime authority delivery store is not configured")
        try:
            delivery = self._runtime_authority_delivery_store.get(
                workspace_id,
                authority_ref,
            )
        except (KeyError, RuntimeAuthorityNotFound) as exc:
            raise ReadModelError(
                f"missing runtime authority delivery {authority_ref.reference_id!r}"
            ) from exc
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="runtime-authority-delivery-detail",
            payload={
                "runtime_authority_delivery": (
                    _redacted_runtime_authority_delivery(delivery)
                )
            },
        )

    def ingress_authorities(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        workspace_id = request.scope.workspace_id
        self._require_workspace(workspace_id)
        if self._ingress_authority_store is None:
            raise ReadModelError("ingress authority store is not configured")
        return self._ingress_authority_store.active_page(request).map(
            lambda value: dict(_redacted_ingress_authority(value))
        )

    def ingress_authority_detail(
        self,
        workspace_id: str,
        authority_ref: IngressAuthorityReference,
    ) -> FocusedDetailReadModel:
        self._require_workspace(workspace_id)
        if self._ingress_authority_store is None:
            raise ReadModelError("ingress authority store is not configured")
        try:
            authority = self._ingress_authority_store.get(workspace_id, authority_ref)
        except (KeyError, IngressAuthorityNotFound) as exc:
            raise ReadModelError(
                f"missing ingress authority {authority_ref.reference_id!r}"
            ) from exc
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="ingress-authority-detail",
            payload={"ingress_authority": _redacted_ingress_authority(authority)},
        )

    def secret_providers(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        workspace_id = request.scope.workspace_id
        self._require_workspace(workspace_id)
        if self._secret_provider_store is None:
            raise ReadModelError("secret provider store is not configured")
        return self._secret_provider_store.active_page(request).map(
            lambda value: dict(_public_secret_provider(value))
        )

    def secret_provider_detail(
        self,
        workspace_id: str,
        provider_id: SecretProviderId,
    ) -> FocusedDetailReadModel:
        self._require_workspace(workspace_id)
        if self._secret_provider_store is None:
            raise ReadModelError("secret provider store is not configured")
        try:
            provider = self._secret_provider_store.get_active(
                workspace_id,
                provider_id,
            )
        except (KeyError, SecretProviderNotFound) as error:
            raise ReadModelError("missing secret provider") from error
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="secret-provider-detail",
            payload={"secret_provider": _public_secret_provider(provider)},
        )

    def secret_references(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        workspace_id = request.scope.workspace_id
        self._require_workspace(workspace_id)
        if self._secret_reference_store is None:
            raise ReadModelError("secret reference store is not configured")
        return self._secret_reference_store.active_page(request).map(
            lambda value: dict(_public_secret_reference(value))
        )

    def secret_reference_detail(
        self,
        workspace_id: str,
        registration_id: str,
    ) -> FocusedDetailReadModel:
        self._require_workspace(workspace_id)
        if self._secret_reference_store is None:
            raise ReadModelError("secret reference store is not configured")
        try:
            reference = self._secret_reference_store.get_by_registration(
                workspace_id,
                registration_id,
            )
        except (KeyError, SecretProviderNotFound) as error:
            raise ReadModelError("missing secret reference") from error
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="secret-reference-detail",
            payload={"secret_reference": _public_secret_reference(reference)},
        )


def _redacted_runtime_authority(value: object) -> Mapping[str, object]:
    descriptor_method = getattr(value, "descriptor", None)
    if not callable(descriptor_method):
        raise ReadModelError("runtime authority record cannot be projected")
    descriptor = _mapping(descriptor_method())
    return _redact_descriptor_value("runtime_authority", descriptor)


def _redacted_ingress_authority(value: object) -> Mapping[str, object]:
    descriptor_method = getattr(value, "descriptor", None)
    if not callable(descriptor_method):
        raise ReadModelError("ingress authority record cannot be projected")
    descriptor = _mapping(descriptor_method())
    return _redact_descriptor_value("ingress_authority", descriptor)


def _redacted_runtime_authority_delivery(value: object) -> Mapping[str, object]:
    descriptor_method = getattr(value, "descriptor", None)
    if not callable(descriptor_method):
        raise ReadModelError("runtime authority delivery record cannot be projected")
    descriptor = _mapping(descriptor_method())
    return _redact_descriptor_value("runtime_authority_delivery", descriptor)


def _public_secret_provider(
    value: RegisteredSecretProvider,
) -> Mapping[str, object]:
    if not isinstance(value, RegisteredSecretProvider):
        raise ReadModelError("secret provider record cannot be projected")
    return value.descriptor()


def _public_secret_reference(
    value: RegisteredSecretReference,
) -> Mapping[str, object]:
    if not isinstance(value, RegisteredSecretReference):
        raise ReadModelError("secret reference record cannot be projected")
    return value.descriptor()


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ReadModelError("expected mapping in graph descriptor")
    return value
