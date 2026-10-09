"""Declared plan semantics and their Operations-owned persistence format."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

import rfc8785

from control_plane_kit_core.planning import (
    ActivityPlan,
    DEFAULT_ACTIVITY_PLAN_CODEC,
    ManagementObservationError,
    compile_activity_plan,
    compile_graph_activity_plan,
    compile_managed_update_activity_plan,
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
from control_plane_kit_operations.configuration_cleanup import (
    ConfigurationCleanupProposal, ConfigurationCleanupProposalCodec,
    ConfigurationCleanupProposalV2, ConfigurationCleanupProposalV2Codec,
    MAX_CLEANUP_DOCUMENT_BYTES, configuration_cleanup_proposal_fingerprint,
)


class PlanDerivationProfile(StrEnum):
    STRUCTURAL_V1 = "structural-v1"
    MANAGEMENT_GRAPH_PAIR_V1 = "management-graph-pair-v1"
    MANAGED_UPDATE_V1 = "managed-update-v1"
    CONFIGURATION_CLEANUP_V1 = "configuration-cleanup-v1"
    CONFIGURATION_CLEANUP_V2 = "configuration-cleanup-v2"


class PlanDerivationError(ValueError):
    """Fixed, candidate-free failure of the stored derivation contract."""


_STORED_PLAN_SCHEMA = "control-plane-kit.operations.activity-plan-record"
_STORED_PLAN_KEYS = {"schema", "version", "derivation_profile", "plan"}
_CLEANUP_CODECS = {
    PlanDerivationProfile.CONFIGURATION_CLEANUP_V1: ConfigurationCleanupProposalCodec,
    PlanDerivationProfile.CONFIGURATION_CLEANUP_V2: ConfigurationCleanupProposalV2Codec,
}


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
    if profile in _CLEANUP_CODECS:
        raise PlanDerivationError("cleanup planning requires original allocation evidence")
    if type(transition) not in (
        InitialDeployment, UpdateDeployment, TeardownDeployment, NoOpDeployment,
    ):
        raise PlanDerivationError("activity plan derivation requires a deployment transition")
    if profile is PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1:
        return compile_graph_activity_plan(transition.current, transition.desired)
    if profile is PlanDerivationProfile.MANAGED_UPDATE_V1:
        return compile_managed_update_activity_plan(
            transition.current, transition.desired,
        )
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
    cleanup_proposal: ConfigurationCleanupProposal | ConfigurationCleanupProposalV2 | None = None,
) -> dict[str, object]:
    _require_profile(profile)
    if profile in _CLEANUP_CODECS:
        proposal_document = _CLEANUP_CODECS[profile]().encode(cleanup_proposal)
        validate_cleanup_activity_plan(plan, cleanup_proposal)
        descriptor = {"schema": _STORED_PLAN_SCHEMA, "version": 2,
            "derivation_profile": profile.value, "plan": DEFAULT_ACTIVITY_PLAN_CODEC.encode(plan),
            "cleanup_proposal": proposal_document,
            "cleanup_proposal_fingerprint": configuration_cleanup_proposal_fingerprint(cleanup_proposal)}
        if len(rfc8785.dumps(descriptor)) > MAX_CLEANUP_DOCUMENT_BYTES:
            raise PlanDerivationError("stored cleanup plan exceeds its capacity")
        return descriptor
    if cleanup_proposal is not None:
        raise PlanDerivationError("stored activity plan profile is inconsistent")
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
    if profile is None or profile in _CLEANUP_CODECS:
        raise PlanDerivationError("stored activity plan is malformed")
    return DEFAULT_ACTIVITY_PLAN_CODEC.decode(descriptor["plan"]), profile


@dataclass(frozen=True)
class StoredActivityPlan:
    plan: ActivityPlan
    profile: PlanDerivationProfile | None
    cleanup_proposal: ConfigurationCleanupProposal | ConfigurationCleanupProposalV2 | None = None

    def __post_init__(self):
        _require_profile(self.profile)
        if self.profile in _CLEANUP_CODECS:
            _CLEANUP_CODECS[self.profile]().encode(self.cleanup_proposal)
            validate_cleanup_activity_plan(self.plan, self.cleanup_proposal)
        elif self.cleanup_proposal is not None:
            raise PlanDerivationError("stored activity plan profile is inconsistent")


def validate_cleanup_activity_plan(plan, proposal):
    """The new profile generates one exact destructive resource-removal activity."""
    from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
    from control_plane_kit_core.planning import (
        ActivityImpact, CleanupConfigurationInstances, NonCompensatable, NonCompensatableReason, RiskLevel,
    )
    valid = False
    try:
        codec = (ConfigurationCleanupProposalCodec if type(proposal) is ConfigurationCleanupProposal
                 else ConfigurationCleanupProposalV2Codec)
        document = codec().encode(proposal)
        DEFAULT_ACTIVITY_PLAN_CODEC.encode(plan)
        if type(plan) is ActivityPlan and len(plan.activities) == 1:
            activity = plan.activities[0]
            expected = tuple(ConfigurationInstanceRefCodec().decode(row["ref"]) for row in document["candidates"])
            valid = (type(activity.operation) is CleanupConfigurationInstances
                and activity.operation.instances == expected and not activity.dependencies
                and activity.risk is RiskLevel.CRITICAL and activity.impact is ActivityImpact.DESTRUCTIVE
                and activity.compensation == NonCompensatable(NonCompensatableReason.RESOURCE_REMOVAL))
    except (TypeError, ValueError, KeyError, AttributeError):
        pass
    if not valid:
        raise PlanDerivationError("stored cleanup plan is malformed")


def decode_stored_activity_plan_record(descriptor: object) -> StoredActivityPlan:
    """Full stored meaning; unlike the old pair decoder, retains cleanup proof."""
    if not (isinstance(descriptor, Mapping) and descriptor.get("schema") == _STORED_PLAN_SCHEMA
            and descriptor.get("version") == 2):
        plan, profile = decode_stored_activity_plan(descriptor)
        return StoredActivityPlan(plan, profile)
    result = None
    try:
        profile = next((value for value in _CLEANUP_CODECS
                        if value.value == descriptor.get("derivation_profile")), None)
        if (type(descriptor) is dict and type(descriptor["version"]) is int
                and set(descriptor) == _STORED_PLAN_KEYS | {"cleanup_proposal", "cleanup_proposal_fingerprint"}
                and type(descriptor["derivation_profile"]) is str
                and profile is not None
                and len(rfc8785.dumps(descriptor)) <= MAX_CLEANUP_DOCUMENT_BYTES):
            proposal = _CLEANUP_CODECS[profile]().decode(descriptor["cleanup_proposal"])
            plan = DEFAULT_ACTIVITY_PLAN_CODEC.decode(descriptor["plan"])
            validate_cleanup_activity_plan(plan, proposal)
            if descriptor["cleanup_proposal_fingerprint"] == configuration_cleanup_proposal_fingerprint(proposal):
                result = StoredActivityPlan(plan, profile, proposal)
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError, OverflowError):
        pass
    if result is None:
        raise PlanDerivationError("stored activity plan is malformed")
    return result
