"""Explicit composition declarations, never inferred provider conformance."""
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from control_plane_kit_core.planning import StartNode, ReconcileNode

if TYPE_CHECKING:
    from control_plane_kit_operations.http_sample_execution import HttpSampleExecutionSupport


@dataclass(frozen=True, slots=True)
class RuntimeAdapterRegistration:
    adapter: object = field(repr=False)
    exact_version_execute: bool = False
    exact_version_execute_with_authority: bool = False
    exact_version_observe: bool = False
    invocation_profile: str | None = field(default=None, kw_only=True)
    ordinary_verification_invocations: bool = field(default=False, kw_only=True)
    original_runtime_observation_invocations: bool = field(default=False, kw_only=True)
    http_sample_support: "HttpSampleExecutionSupport | None" = field(default=None, kw_only=True, repr=False)

    def __post_init__(self):
        if self.adapter is None or any(type(value) is not bool for value in (
                self.exact_version_execute, self.exact_version_execute_with_authority, self.exact_version_observe,
                self.ordinary_verification_invocations, self.original_runtime_observation_invocations)):
            raise ValueError("runtime adapter registration is malformed")
        if self.invocation_profile is not None and (type(self.invocation_profile) is not str
                or self.invocation_profile != "cpk.semantic-invocations.v1"):
            raise ValueError("runtime adapter invocation profile is malformed")
        if self.http_sample_support is not None:
            require_http_sample_execution_support(self.http_sample_support, self)


def require_http_sample_execution_support(support, registration):
    """One descriptor must be consumed by the actual registered native arm."""
    from control_plane_kit_operations.http_sample_execution import HttpSampleExecutionSupport
    if (type(support) is not HttpSampleExecutionSupport
            or type(registration) is not RuntimeAdapterRegistration
            or registration.invocation_profile != "cpk.semantic-invocations.v1"
            or not registration.ordinary_verification_invocations
            or registration.http_sample_support is not support
            or getattr(registration.adapter, "http_sample_support", None) is not support
            or not callable(getattr(registration.adapter, "execute_with_authority", None))):
        raise ValueError("HTTP sample execution support is unavailable")
    support.__post_init__()


def adapter_accepts_invocations(registration, arm):
    if type(registration) is not RuntimeAdapterRegistration:
        return False
    registration.__post_init__()
    return (registration.invocation_profile == "cpk.semantic-invocations.v1"
        and callable(getattr(registration.adapter, arm, None)))


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
