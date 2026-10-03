"""Declared plan semantics and their Operations-owned persistence format."""

from collections.abc import Mapping
from enum import StrEnum

from control_plane_kit_core.planning import (
    ActivityPlan,
    DEFAULT_ACTIVITY_PLAN_CODEC,
    ManagementObservationError,
    compile_activity_plan,
    compile_graph_activity_plan,
)
from control_plane_kit_core.planning.codec import (
    ACTIVITY_PLAN_SCHEMA,
    ActivityPlanDescriptorError,
)
from control_plane_kit_operations.deployment_transitions import (
    DeploymentTransition,
    InitialDeployment,
    NoOpDeployment,
    TeardownDeployment,
    UpdateDeployment,
)


class PlanDerivationProfile(StrEnum):
    STRUCTURAL_V1 = "structural-v1"
    MANAGEMENT_GRAPH_PAIR_V1 = "management-graph-pair-v1"


class PlanDerivationError(ValueError):
    """Fixed, candidate-free failure of the stored derivation contract."""


_STORED_PLAN_SCHEMA = "control-plane-kit.operations.activity-plan-record"
_STORED_PLAN_KEYS = {"schema", "version", "derivation_profile", "plan"}


def _require_profile(profile: PlanDerivationProfile | None) -> None:
    if profile is not None and type(profile) is not PlanDerivationProfile:
        raise PlanDerivationError("activity plan derivation profile is invalid")


def derive_activity_plan(
    transition: DeploymentTransition,
    *,
    profile: PlanDerivationProfile | None,
) -> ActivityPlan:
    """Select exactly one declared interpretation, before any comparison."""
    _require_profile(profile)
    if type(transition) not in (
        InitialDeployment, UpdateDeployment, TeardownDeployment, NoOpDeployment,
    ):
        raise PlanDerivationError("activity plan derivation requires a deployment transition")
    if profile is PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1:
        return compile_graph_activity_plan(transition.current, transition.desired)
    return compile_activity_plan(transition.diff)


def planning_derivation_matches_action(
    profile: PlanDerivationProfile | None,
    evidence: Mapping[str, object],
) -> bool:
    """Absence is legacy; explicit null or a one-sided marker is a conflict."""
    if profile is None:
        return "derivation_profile" not in evidence
    if type(profile) is not PlanDerivationProfile:
        return False
    value = evidence.get("derivation_profile")
    return type(value) is str and value == profile.value


def encode_stored_activity_plan(
    plan: ActivityPlan,
    *,
    profile: PlanDerivationProfile | None,
) -> dict[str, object]:
    _require_profile(profile)
    encoded = None
    try:
        encoded = DEFAULT_ACTIVITY_PLAN_CODEC.encode(plan)
    except (ActivityPlanDescriptorError, ManagementObservationError):
        pass
    if encoded is None:
        raise PlanDerivationError("stored activity plan is malformed")
    if profile is None:
        return encoded
    return {
        "schema": _STORED_PLAN_SCHEMA,
        "version": 1,
        "derivation_profile": profile.value,
        "plan": encoded,
    }


def decode_stored_activity_plan(
    descriptor: object,
) -> tuple[ActivityPlan, PlanDerivationProfile | None]:
    """Recognize legacy or profiled wire once; never retry a malformed envelope."""
    decoded = None
    try:
        decoded = _decode_stored_activity_plan(descriptor)
    except (
        PlanDerivationError, ActivityPlanDescriptorError,
        ManagementObservationError, RecursionError, OverflowError,
    ):
        pass
    # Raise outside the parser exception context. Unrelated failures escape.
    if decoded is None:
        raise PlanDerivationError("stored activity plan is malformed")
    return decoded


def _decode_stored_activity_plan(
    descriptor: object,
) -> tuple[ActivityPlan, PlanDerivationProfile | None]:
    if not isinstance(descriptor, Mapping) or type(descriptor.get("schema")) is not str:
        raise PlanDerivationError("stored activity plan is malformed")
    if descriptor["schema"] == ACTIVITY_PLAN_SCHEMA:
        return DEFAULT_ACTIVITY_PLAN_CODEC.decode(descriptor), None
    if (
        descriptor["schema"] != _STORED_PLAN_SCHEMA
        or set(descriptor) != _STORED_PLAN_KEYS
        or type(descriptor["version"]) is not int
        or descriptor["version"] != 1
        or type(descriptor["derivation_profile"]) is not str
    ):
        raise PlanDerivationError("stored activity plan is malformed")
    profile = next(
        (value for value in PlanDerivationProfile if value.value == descriptor["derivation_profile"]),
        None,
    )
    if profile is None:
        raise PlanDerivationError("stored activity plan is malformed")
    return DEFAULT_ACTIVITY_PLAN_CODEC.decode(descriptor["plan"]), profile
