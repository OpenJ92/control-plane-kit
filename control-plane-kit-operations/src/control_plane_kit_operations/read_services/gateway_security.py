"""Gateway security projections over durable operations truth."""

from __future__ import annotations

from typing import Callable

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyNotFound,
    RegisteredDelegationSigningKey,
    RegisteredDelegationSigningKeyStatus,
)
from control_plane_kit_operations.gateway_probes import (
    GatewayProbeError,
    GatewayProbeVerifierConfiguration,
)
from control_plane_kit_operations.read_pages import ReadPage, ReadPageRequest
from control_plane_kit_operations.records import WorkspaceRecord

from .errors import ReadModelError
from .models import FocusedDetailReadModel
from .protocols import DelegationSigningKeyStore, GatewayProbeStore


class _GatewaySecurityReadProjection:
    def __init__(
        self,
        require_workspace: Callable[[str], WorkspaceRecord],
        *,
        gateway_probe_store: GatewayProbeStore | None,
        delegation_signing_key_store: DelegationSigningKeyStore | None,
    ) -> None:
        self._require_workspace = require_workspace
        self._gateway_probe_store = gateway_probe_store
        self._delegation_signing_key_store = delegation_signing_key_store

    def gateway_probe_timeline(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        self._require_workspace(request.scope.workspace_id)
        if self._gateway_probe_store is None:
            raise ReadModelError("gateway probe store is not configured")
        return self._gateway_probe_store.page(request).map(
            lambda value: dict(value.descriptor())
        )

    def gateway_probe_detail(
        self,
        workspace_id: str,
        probe_id: str,
    ) -> FocusedDetailReadModel:
        self._require_workspace(workspace_id)
        if self._gateway_probe_store is None:
            raise ReadModelError("gateway probe store is not configured")
        missing = False
        try:
            attempt = self._gateway_probe_store.get(probe_id)
        except KeyError:
            missing = True
            attempt = None
        if missing or getattr(attempt, "workspace_id", None) != workspace_id:
            raise ReadModelError(f"missing gateway probe {probe_id!r}")
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="gateway-probe-detail",
            payload={"gateway_probe": attempt.descriptor()},
        )

    def delegation_signing_keys(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        self._require_workspace(request.scope.workspace_id)
        if self._delegation_signing_key_store is None:
            raise ReadModelError("delegation signing key store is not configured")
        return self._delegation_signing_key_store.workspace_page(request).map(
            _public_delegation_signing_key
        )

    def gateway_verifier_configuration(
        self,
        workspace_id: str,
        gateway_node_id: str,
    ) -> FocusedDetailReadModel:
        self._require_workspace(workspace_id)
        if self._delegation_signing_key_store is None:
            raise ReadModelError("delegation signing key store is not configured")
        try:
            active = self._delegation_signing_key_store.require_unambiguous_active(
                workspace_id,
                DelegationKeyPurpose.GATEWAY_PROBE,
            )
            verification_keys = (
                self._delegation_signing_key_store.list_for_verification(
                    workspace_id,
                    DelegationKeyPurpose.GATEWAY_PROBE,
                    active.issuer,
                )
            )
            if not any(
                value.status is RegisteredDelegationSigningKeyStatus.ACTIVE
                for value in verification_keys
            ):
                raise GatewayProbeError("gateway verifier set has no active key")
            configuration = GatewayProbeVerifierConfiguration(
                issuer=active.issuer,
                audience=f"gateway:{workspace_id}:{gateway_node_id}",
                gateway_node_id=gateway_node_id,
                public_keys=tuple(value.public_key for value in verification_keys),
            )
        except (DelegationSigningKeyNotFound, GatewayProbeError) as error:
            raise ReadModelError(
                "gateway verifier configuration is unavailable"
            ) from error
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="gateway-verifier-configuration",
            payload={
                "gateway_verifier_configuration": {
                    "issuer": configuration.issuer,
                    "audience": configuration.audience,
                    "gateway_node_id": configuration.gateway_node_id,
                    "public_keys": [
                        {
                            **key.descriptor(),
                            "public_key_pem": key.public_key_pem,
                        }
                        for key in configuration.public_keys
                    ],
                    "public_environment": [
                        binding.descriptor()
                        for binding in configuration.public_environment()
                    ],
                }
            },
        )


def _public_delegation_signing_key(
    value: RegisteredDelegationSigningKey,
) -> dict[str, object]:
    if not isinstance(value, RegisteredDelegationSigningKey):
        raise ReadModelError("delegation signing key record cannot be projected")
    return {
        "registration_id": value.registration_id,
        "workspace_id": value.workspace_id,
        "purpose": value.purpose.value,
        "issuer": value.issuer,
        "key_id": value.public_key.key_id,
        "algorithm": value.public_key.algorithm.value,
        "fingerprint_sha256": value.public_key.fingerprint_sha256,
        "admitted_by": value.admitted_by,
        "admitted_at": value.admitted_at,
        "status": value.status.value,
        "activated_by": value.activated_by,
        "activated_at": value.activated_at,
        "retired_by": value.retired_by,
        "retired_at": value.retired_at,
        "revoked_by": value.revoked_by,
        "revoked_at": value.revoked_at,
    }
