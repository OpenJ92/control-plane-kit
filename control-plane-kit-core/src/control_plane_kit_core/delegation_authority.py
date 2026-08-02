"""Pure authored delegation authority and verifier projection language."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
from typing import Mapping, TYPE_CHECKING

from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding

if TYPE_CHECKING:
    from control_plane_kit_core.topology.graph import DeploymentGraph


_REFERENCE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,255}$")
_VERIFIER_ENVIRONMENT_NAMES = frozenset(
    {
        "CPK_GATEWAY_PROBE_AUDIENCE",
        "CPK_GATEWAY_PROBE_ISSUER",
        "CPK_GATEWAY_PROBE_NODE_ID",
        "CPK_GATEWAY_PROBE_PROJECTION_ID",
        "CPK_GATEWAY_PROBE_VERIFICATION_KEYS_JSON",
        "CPK_GATEWAY_PROBE_VERIFIER",
    }
)


class DelegationAuthorityError(ValueError):
    """Raised when authored delegation authority or projection is incoherent."""


@dataclass(frozen=True, order=True)
class DelegationAuthorityBinding:
    """Stable authored intent binding one graph node to a delegation authority."""

    delegate_node_id: str
    purpose: DelegationKeyPurpose
    issuer: str

    def __post_init__(self) -> None:
        _reference(self.delegate_node_id, "delegate_node_id")
        if not isinstance(self.purpose, DelegationKeyPurpose):
            raise DelegationAuthorityError("delegation purpose must be closed")
        _reference(self.issuer, "issuer")

    @property
    def identity(self) -> tuple[str, DelegationKeyPurpose]:
        return self.delegate_node_id, self.purpose

    def descriptor(self) -> dict[str, str]:
        return {
            "delegate_node_id": self.delegate_node_id,
            "purpose": self.purpose.value,
            "issuer": self.issuer,
        }

    @classmethod
    def from_descriptor(
        cls,
        descriptor: Mapping[str, object],
    ) -> DelegationAuthorityBinding:
        if set(descriptor) != {"delegate_node_id", "purpose", "issuer"}:
            raise DelegationAuthorityError(
                "delegation authority descriptor has unexpected keys"
            )
        try:
            purpose = DelegationKeyPurpose(_text(descriptor, "purpose"))
        except ValueError as error:
            raise DelegationAuthorityError("delegation purpose is unknown") from error
        return cls(
            delegate_node_id=_text(descriptor, "delegate_node_id"),
            purpose=purpose,
            issuer=_text(descriptor, "issuer"),
        )


@dataclass(frozen=True)
class DelegationVerifierProjection:
    """Bounded public verifier material for one exact authored binding."""

    delegate_node_id: str
    purpose: DelegationKeyPurpose
    issuer: str
    audience: str
    projection_id: str
    public_keys: tuple[DelegationPublicKey, ...]

    def __post_init__(self) -> None:
        _reference(self.delegate_node_id, "delegate_node_id")
        if not isinstance(self.purpose, DelegationKeyPurpose):
            raise DelegationAuthorityError("delegation purpose must be closed")
        _reference(self.issuer, "issuer")
        _reference(self.audience, "audience")
        _reference(self.projection_id, "projection_id")
        if not isinstance(self.public_keys, tuple) or not all(
            isinstance(value, DelegationPublicKey) for value in self.public_keys
        ):
            raise DelegationAuthorityError(
                "delegation verifier projection public keys must be a typed tuple"
            )
        keys = tuple(sorted(self.public_keys, key=lambda value: value.key_id))
        if not keys or len(keys) > 16:
            raise DelegationAuthorityError(
                "delegation verifier projection requires one to sixteen public keys"
            )
        if len({value.key_id for value in keys}) != len(keys):
            raise DelegationAuthorityError(
                "delegation verifier projection key ids must be unique"
            )
        if any(value.algorithm is not DelegationKeyAlgorithm.ED25519 for value in keys):
            raise DelegationAuthorityError(
                "gateway delegation verifier projection requires Ed25519 keys"
            )
        object.__setattr__(self, "public_keys", keys)

    @property
    def binding_identity(self) -> tuple[str, DelegationKeyPurpose]:
        return self.delegate_node_id, self.purpose

    def descriptor(self) -> dict[str, object]:
        return {
            "delegate_node_id": self.delegate_node_id,
            "purpose": self.purpose.value,
            "issuer": self.issuer,
            "audience": self.audience,
            "projection_id": self.projection_id,
            "public_keys": [
                {
                    **value.descriptor(),
                    "public_key_pem": value.public_key_pem,
                }
                for value in self.public_keys
            ],
        }

    @classmethod
    def from_descriptor(
        cls,
        descriptor: Mapping[str, object],
    ) -> DelegationVerifierProjection:
        expected = {
            "delegate_node_id",
            "purpose",
            "issuer",
            "audience",
            "projection_id",
            "public_keys",
        }
        if set(descriptor) != expected:
            raise DelegationAuthorityError(
                "delegation verifier projection descriptor has unexpected keys"
            )
        try:
            purpose = DelegationKeyPurpose(_text(descriptor, "purpose"))
        except ValueError as error:
            raise DelegationAuthorityError("delegation purpose is unknown") from error
        raw_keys = descriptor.get("public_keys")
        if not isinstance(raw_keys, list):
            raise DelegationAuthorityError(
                "delegation verifier public_keys must be a list"
            )
        keys: list[DelegationPublicKey] = []
        for raw_key in raw_keys:
            if not isinstance(raw_key, Mapping) or set(raw_key) != {
                "key_id",
                "algorithm",
                "fingerprint_sha256",
                "public_key_pem",
            }:
                raise DelegationAuthorityError(
                    "delegation verifier public key descriptor is malformed"
                )
            try:
                key = DelegationPublicKey(
                    key_id=_text(raw_key, "key_id"),
                    algorithm=DelegationKeyAlgorithm(_text(raw_key, "algorithm")),
                    public_key_pem=_text(raw_key, "public_key_pem"),
                )
            except (TypeError, ValueError) as error:
                raise DelegationAuthorityError(
                    "delegation verifier public key is malformed"
                ) from error
            if raw_key.get("fingerprint_sha256") != key.fingerprint_sha256:
                raise DelegationAuthorityError(
                    "delegation verifier public key fingerprint is inconsistent"
                )
            keys.append(key)
        return cls(
            delegate_node_id=_text(descriptor, "delegate_node_id"),
            purpose=purpose,
            issuer=_text(descriptor, "issuer"),
            audience=_text(descriptor, "audience"),
            projection_id=_text(descriptor, "projection_id"),
            public_keys=tuple(keys),
        )

    def public_environment(self) -> tuple[PublicStaticEnvironmentBinding, ...]:
        key_map = {key.key_id: key.public_key_pem for key in self.public_keys}
        return tuple(
            sorted(
                (
                    PublicStaticEnvironmentBinding(
                        "CPK_GATEWAY_PROBE_AUDIENCE", self.audience
                    ),
                    PublicStaticEnvironmentBinding(
                        "CPK_GATEWAY_PROBE_ISSUER", self.issuer
                    ),
                    PublicStaticEnvironmentBinding(
                        "CPK_GATEWAY_PROBE_NODE_ID", self.delegate_node_id
                    ),
                    PublicStaticEnvironmentBinding(
                        "CPK_GATEWAY_PROBE_PROJECTION_ID", self.projection_id
                    ),
                    PublicStaticEnvironmentBinding(
                        "CPK_GATEWAY_PROBE_VERIFICATION_KEYS_JSON",
                        json.dumps(
                            key_map,
                            ensure_ascii=True,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    ),
                    PublicStaticEnvironmentBinding(
                        "CPK_GATEWAY_PROBE_VERIFIER", "ed25519"
                    ),
                )
            )
        )


def materialize_delegation_verifiers(
    authored_graph: DeploymentGraph,
    projections: tuple[DelegationVerifierProjection, ...],
) -> DeploymentGraph:
    """Return one realized graph without mutating authored graph truth."""

    from control_plane_kit_core.topology.graph import DeploymentGraph

    if not isinstance(authored_graph, DeploymentGraph):
        raise TypeError("delegation verifier materialization requires DeploymentGraph")
    if not isinstance(projections, tuple) or not all(
        isinstance(value, DelegationVerifierProjection) for value in projections
    ):
        raise TypeError("delegation verifier projections must be a typed tuple")
    if not authored_graph.delegation_authorities and not projections:
        return authored_graph

    binding_by_identity = {
        value.identity: value for value in authored_graph.delegation_authorities
    }
    projection_by_identity = {value.binding_identity: value for value in projections}
    if len(projection_by_identity) != len(projections):
        raise DelegationAuthorityError(
            "delegation verifier projections must have unique binding identities"
        )
    if set(binding_by_identity) != set(projection_by_identity):
        raise DelegationAuthorityError(
            "delegation verifier projections must cover exact authored bindings"
        )

    realized = authored_graph
    for identity in sorted(
        binding_by_identity,
        key=lambda value: (value[0], value[1].value),
    ):
        binding = binding_by_identity[identity]
        projection = projection_by_identity[identity]
        if binding.issuer != projection.issuer:
            raise DelegationAuthorityError(
                "delegation verifier projection issuer does not match authored binding"
            )
        try:
            node = realized.node(binding.delegate_node_id)
        except KeyError as error:
            raise DelegationAuthorityError(
                "delegation authority references a missing delegate node"
            ) from error
        existing_names = {value.name for value in node.public_environment}
        if existing_names & _VERIFIER_ENVIRONMENT_NAMES:
            raise DelegationAuthorityError(
                "authored node environment conflicts with generated verifier material"
            )
        if node.delegation_verifier_projection == projection:
            continue
        if node.delegation_verifier_projection is not None:
            raise DelegationAuthorityError(
                "realized graph already contains another verifier projection"
            )
        realized = realized.update_node(
            replace(node, delegation_verifier_projection=projection)
        )
    return realized


def _reference(value: object, name: str) -> None:
    if not isinstance(value, str) or not _REFERENCE.fullmatch(value):
        raise DelegationAuthorityError(
            f"delegation authority {name} must be a bounded reference"
        )


def _text(descriptor: Mapping[str, object], key: str) -> str:
    value = descriptor.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DelegationAuthorityError(f"delegation authority {key} must be text")
    return value


__all__ = [
    "DelegationAuthorityBinding",
    "DelegationAuthorityError",
    "DelegationVerifierProjection",
    "materialize_delegation_verifiers",
]
