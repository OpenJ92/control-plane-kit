"""Explicit composition declarations, never inferred provider conformance."""
from dataclasses import dataclass, field

from control_plane_kit_core.planning import StartNode, ReconcileNode


@dataclass(frozen=True, slots=True)
class RuntimeAdapterRegistration:
    adapter: object = field(repr=False)
    exact_version_execute: bool = False
    exact_version_execute_with_authority: bool = False
    exact_version_observe: bool = False

    def __post_init__(self):
        if self.adapter is None or any(type(value) is not bool for value in (
                self.exact_version_execute, self.exact_version_execute_with_authority, self.exact_version_observe)):
            raise ValueError("runtime adapter registration is malformed")


def versioned_consumer_required(request):
    return type(request.operation) in (StartNode, ReconcileNode) and any(
        material.secret_selections for material in request.products)


def adapter_is_qualified(registration, request, arm):
    if arm not in ("execute", "execute_with_authority", "observe"):
        raise ValueError("runtime adapter arm is unknown")
    if not versioned_consumer_required(request):
        return True
    if type(registration) is not RuntimeAdapterRegistration:
        return False
    RuntimeAdapterRegistration.__post_init__(registration)
    return getattr(registration, "exact_version_" + arm)


def registered_adapter(registration):
    if type(registration) is RuntimeAdapterRegistration:
        RuntimeAdapterRegistration.__post_init__(registration)
        return registration.adapter
    if isinstance(registration, RuntimeAdapterRegistration):
        raise ValueError("runtime adapter registration must be exact")
    return registration
