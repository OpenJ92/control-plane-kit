"""Closed provider-neutral lifecycle values for runtime resources."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ResourceOwnership(StrEnum):
    """Whether control-plane-kit may mutate one resource."""

    OWNED = "owned"
    ATTACHED = "attached"
    EXTERNAL = "external"


class ResourcePersistence(StrEnum):
    """Whether ordinary topology removal may remove one resource."""

    EPHEMERAL = "ephemeral"
    RETAINED = "retained"


@dataclass(frozen=True, order=True)
class DataResourceSpec:
    """One named data resource whose lifecycle is independent from compute."""

    resource_id: str
    persistence: ResourcePersistence = ResourcePersistence.RETAINED

    def __post_init__(self) -> None:
        if not self.resource_id.strip():
            raise ValueError("data resource identity must not be empty")
        if not isinstance(self.persistence, ResourcePersistence):
            raise TypeError("data resource persistence must be ResourcePersistence")

    def descriptor(self) -> dict[str, str]:
        return {
            "resource_id": self.resource_id,
            "persistence": self.persistence.value,
        }


@dataclass(frozen=True)
class ResourceLifecycle:
    """Ownership x compute persistence x independently retained data."""

    ownership: ResourceOwnership
    compute: ResourcePersistence
    data: tuple[DataResourceSpec, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.ownership, ResourceOwnership):
            raise TypeError("resource ownership must be ResourceOwnership")
        if not isinstance(self.compute, ResourcePersistence):
            raise TypeError("compute persistence must be ResourcePersistence")
        if not all(isinstance(value, DataResourceSpec) for value in self.data):
            raise TypeError("lifecycle data must contain DataResourceSpec values")
        identities = tuple(value.resource_id for value in self.data)
        if len(identities) != len(set(identities)):
            raise ValueError("data resource identities must be unique")
        if self.ownership is not ResourceOwnership.OWNED:
            if self.compute is not ResourcePersistence.RETAINED or self.data:
                raise ValueError(
                    "attached and external resources must be retained and cannot declare owned data"
                )
        object.__setattr__(self, "data", tuple(sorted(self.data)))

    @classmethod
    def owned_ephemeral(cls) -> "ResourceLifecycle":
        return cls(ResourceOwnership.OWNED, ResourcePersistence.EPHEMERAL)

    @classmethod
    def owned_with_retained_data(cls, *resource_ids: str) -> "ResourceLifecycle":
        return cls(
            ResourceOwnership.OWNED,
            ResourcePersistence.EPHEMERAL,
            tuple(DataResourceSpec(value) for value in resource_ids),
        )

    @classmethod
    def attached(cls) -> "ResourceLifecycle":
        return cls(ResourceOwnership.ATTACHED, ResourcePersistence.RETAINED)

    @classmethod
    def external(cls) -> "ResourceLifecycle":
        return cls(ResourceOwnership.EXTERNAL, ResourcePersistence.RETAINED)

    def data_resource(self, resource_id: str) -> DataResourceSpec:
        for resource in self.data:
            if resource.resource_id == resource_id:
                return resource
        raise KeyError(f"lifecycle has no data resource {resource_id!r}")

    def descriptor(self) -> dict[str, object]:
        return {
            "ownership": self.ownership.value,
            "compute": self.compute.value,
            "data": [value.descriptor() for value in self.data],
        }


OWNED_EPHEMERAL = ResourceLifecycle.owned_ephemeral()
EXTERNAL_RETAINED = ResourceLifecycle.external()
