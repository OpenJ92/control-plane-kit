"""Retained cleanup evidence; construction confers no mutation authority."""
from dataclasses import dataclass
import re

from control_plane_kit_core.configuration_instances import ConfigurationCleanupOutcomeSet, ConfigurationCleanupOutcomeSetCodec
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptStatus
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.configuration_completion import ConfigurationInvocationCompletionRecord
from control_plane_kit_operations.configuration_preparation import ConfigurationRefEvidence, _identity
from control_plane_kit_operations.effect_outcome_evidence import EffectOutcomeProfile
from control_plane_kit_operations.records import OperationsRecordError


def _key(identity):
    return identity.run_id.value, identity.activity_id, identity.attempt


@dataclass(frozen=True)
class ConfigurationCleanupReservationRecord:
    identity: EffectAttemptIdentity
    workspace_id: str
    request_id: str
    request_fingerprint: str
    original_event_id: str
    plan_id: str
    approval_request_id: str
    approval_decision_id: str
    proposal_fingerprint: str
    runtime_id: str
    runtime_kind: RuntimeKind
    authority_ref: RuntimeAuthorityReference
    registration_id: str
    members: tuple[ConfigurationRefEvidence, ...]
    completions: tuple[ConfigurationInvocationCompletionRecord, ...]
    claims: tuple[ConfigurationRefEvidence, ...]
    status: EffectAttemptStatus
    outcome_fingerprint: str | None = None
    outcome_profile: EffectOutcomeProfile | None = None
    outcomes: ConfigurationCleanupOutcomeSet | None = None

    def __post_init__(self):
        _identity(self.identity)
        valid = all(type(value) is str and 1 <= len(value.encode()) <= 2048 for value in (
            self.request_id, self.original_event_id, self.plan_id, self.approval_request_id,
            self.approval_decision_id, self.registration_id))
        valid = valid and all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)
            for value in (self.request_fingerprint, self.proposal_fingerprint))
        valid = valid and all(type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value)
            for value in (self.workspace_id, self.runtime_id))
        valid = valid and type(self.runtime_kind) is RuntimeKind and type(self.authority_ref) is RuntimeAuthorityReference
        valid = valid and type(self.status) is EffectAttemptStatus
        valid = valid and all(type(values) is tuple and 1 <= len(values) <= maximum
            and all(type(value) is kind for value in values) for values, maximum, kind in (
                (self.members, 32, ConfigurationRefEvidence),
                (self.completions, 256, ConfigurationInvocationCompletionRecord),
                (self.claims, 256, ConfigurationRefEvidence)))
        if not valid:
            raise OperationsRecordError("configuration cleanup reservation is invalid")
        for value in self.members + self.claims:
            value.__post_init__()
        for value in self.completions:
            value.__post_init__()
        member_refs = {value.ref.allocation_id: value.ref for value in self.members}
        member_keys = tuple(value.ref.allocation_id for value in self.members)
        claim_keys = tuple((*_key(value.identity), value.ref.artifact_id) for value in self.claims)
        completion_keys = tuple(_key(value.identity) for value in self.completions)
        valid = (member_keys == tuple(sorted(set(member_keys)))
            and claim_keys == tuple(sorted(set(claim_keys)))
            and completion_keys == tuple(sorted(set(completion_keys)))
            and {value.identity for value in self.claims} == {value.identity for value in self.completions}
            and all((value.ref.workspace_id, value.ref.runtime_id) == (self.workspace_id, self.runtime_id)
                and value.identity == value.birth_identity and value.ref.artifact_id == value.birth_artifact_id
                for value in self.members)
            and all(member_refs.get(value.ref.allocation_id) == value.ref for value in self.claims)
            and {value.ref.allocation_id for value in self.claims} == set(member_refs)
            and all(value.workspace_id == self.workspace_id for value in self.completions))
        if self.status is EffectAttemptStatus.STARTED:
            valid = valid and self.outcome_fingerprint is None and self.outcome_profile is None and self.outcomes is None
        else:
            valid = valid and type(self.outcome_fingerprint) is str and re.fullmatch(r"[0-9a-f]{64}", self.outcome_fingerprint)
            valid = valid and type(self.outcome_profile) is EffectOutcomeProfile
            if self.outcome_profile is EffectOutcomeProfile.EXECUTION_RESULT:
                valid = valid and type(self.outcomes) is ConfigurationCleanupOutcomeSet
                if valid:
                    ConfigurationCleanupOutcomeSetCodec().encode(self.outcomes)
                    valid = tuple(value.ref for value in self.outcomes.outcomes) == tuple(value.ref for value in self.members)
            else:
                valid = valid and self.outcomes is None
        if not valid:
            raise OperationsRecordError("configuration cleanup reservation is invalid")


__all__ = ["ConfigurationCleanupReservationRecord"]
