"""Gateway security projections over durable operations truth."""

from __future__ import annotations

import json
from typing import Callable

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.wrapper_configuration import (
    NodeControlVerificationConfiguration,
    WrapperConfigurationError,
)
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


_WORKLOAD_PURPOSES = (
    DelegationKeyPurpose.WORKLOAD_NODE_CONTROL,
    DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ,
    DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ,
)
_WORKLOAD_UNAVAILABLE = "workload verifier configuration is unavailable"


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
        if missing or attempt.workspace_id != workspace_id:
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

    def workload_verifier_configuration(
        self,
        workspace_id: str,
        purposes: tuple[DelegationKeyPurpose, ...],
    ) -> FocusedDetailReadModel:
        if (
            type(purposes) is not tuple
            or not 1 <= len(purposes) <= 3
            or any(
                type(value) is not DelegationKeyPurpose or value not in _WORKLOAD_PURPOSES
                for value in purposes
            )
            or len(set(purposes)) != len(purposes)
            or DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ not in purposes
        ):
            raise ReadModelError("workload verifier purposes are malformed")
        self._require_workspace(workspace_id)
        store = self._delegation_signing_key_store
        if store is None:
            raise ReadModelError(_WORKLOAD_UNAVAILABLE)
        try:
            # Acquire every purpose lock in a fixed order before reading any set.
            # The caller's read UoW holds them through the complete observation.
            selected = tuple(
                (purpose, store.require_unambiguous_active(workspace_id, purpose))
                for purpose in sorted(purposes, key=lambda value: value.value)
            )
            families = []
            for purpose, active in selected:
                if (
                    not isinstance(active, RegisteredDelegationSigningKey)
                    or active.workspace_id != workspace_id
                    or active.purpose is not purpose
                    or active.status is not RegisteredDelegationSigningKeyStatus.ACTIVE
                ):
                    raise ReadModelError(_WORKLOAD_UNAVAILABLE)
                keys = store.list_for_verification(
                    workspace_id, purpose, active.issuer, limit=17,
                )
                if (
                    not 1 <= len(keys) <= 16
                    or any(
                        not isinstance(value, RegisteredDelegationSigningKey)
                        or value.workspace_id != workspace_id
                        or value.purpose is not purpose
                        or value.issuer != active.issuer
                        or value.status not in (
                            RegisteredDelegationSigningKeyStatus.ACTIVE,
                            RegisteredDelegationSigningKeyStatus.VERIFY_ONLY,
                        )
                        for value in keys
                    )
                    or active not in keys
                ):
                    raise ReadModelError(_WORKLOAD_UNAVAILABLE)
                families.append(NodeControlVerificationConfiguration(
                    purpose, active.issuer, tuple(value.public_key for value in keys),
                ))
        except (DelegationSigningKeyNotFound, WrapperConfigurationError):
            unavailable = True
        else:
            unavailable = False
        if unavailable:
            raise ReadModelError(_WORKLOAD_UNAVAILABLE)
        model = FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="workload-verifier-configuration",
            payload={"workload_verifier_configuration": {"verifiers": [
                {
                    "purpose": family.purpose.value,
                    "issuer": family.issuer,
                    "public_keys": [
                        {"key_id": key.key_id, "algorithm": key.algorithm.value,
                         "public_key_pem": key.public_key_pem}
                        for key in family.public_keys
                    ],
                }
                for family in families
            ]}},
        )
        if len(json.dumps(model.descriptor()).encode("utf-8")) > 65_536:
            raise ReadModelError(_WORKLOAD_UNAVAILABLE)
        return model


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
