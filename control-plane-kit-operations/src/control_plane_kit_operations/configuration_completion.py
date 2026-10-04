"""Immutable admission of one original invocation; never non-use permission."""
from dataclasses import dataclass
import re

from control_plane_kit_core.operations import EffectAttemptIdentity
from control_plane_kit_operations.configuration_preparation import _identity
from control_plane_kit_operations.records import OperationsRecordError


@dataclass(frozen=True)
class ConfigurationInvocationCompletionRecord:
    identity: EffectAttemptIdentity
    workspace_id: str
    request_fingerprint: str
    selection_fingerprint: str
    outcome_fingerprint: str
    original_event_id: str
    original_event_ordinal: int
    direct_event_id: str
    direct_event_ordinal: int

    def __post_init__(self):
        _identity(self.identity)
        valid = all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) for value in (
            self.request_fingerprint, self.selection_fingerprint, self.outcome_fingerprint))
        valid = valid and type(self.workspace_id) is str and 1 <= len(self.workspace_id.encode()) <= 128
        valid = valid and all(type(value) is str and 1 <= len(value) <= 512 for value in (
            self.original_event_id, self.direct_event_id))
        valid = valid and type(self.original_event_ordinal) is int and type(self.direct_event_ordinal) is int
        if not valid or not 0 < self.original_event_ordinal < self.direct_event_ordinal:
            raise OperationsRecordError("configuration completion is invalid")


__all__ = ["ConfigurationInvocationCompletionRecord"]
