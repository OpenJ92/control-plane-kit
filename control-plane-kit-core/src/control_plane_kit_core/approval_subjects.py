"""Pure closed subjects for reviewable control-plane approvals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import TypeAlias

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ApprovalSubjectKind(StrEnum):
    """Closed operational truths that may receive approval."""

    ACTIVITY_PLAN = "activity-plan"
    GATEWAY_KEY_ROTATION = "gateway-key-rotation"


@dataclass(frozen=True)
class ActivityPlanApprovalSubject:
    """Approval authority over one immutable persisted activity plan."""

    plan_id: str

    def __post_init__(self) -> None:
        _identifier(self.plan_id, "plan_id")

    @property
    def kind(self) -> ApprovalSubjectKind:
        return ApprovalSubjectKind.ACTIVITY_PLAN

    @property
    def subject_id(self) -> str:
        return self.plan_id

    @property
    def review_digest(self) -> str:
        return sha256(f"activity-plan:{self.plan_id}".encode("utf-8")).hexdigest()

    def descriptor(self) -> dict[str, object]:
        return {"kind": self.kind.value, "plan_id": self.plan_id}


@dataclass(frozen=True)
class GatewayKeyRotationApprovalSubject:
    """Secret-free immutable review meaning for one gateway key rotation."""

    rotation_id: str
    workspace_id: str
    gateway_node_id: str
    purpose: DelegationKeyPurpose
    issuer: str
    old_key_id: str
    maximum_grant_lifetime_seconds: int
    clock_skew_seconds: int
    rotation_intent_digest: str

    def __post_init__(self) -> None:
        for field in (
            "rotation_id",
            "workspace_id",
            "gateway_node_id",
            "issuer",
            "old_key_id",
        ):
            _identifier(getattr(self, field), field)
        if not isinstance(self.purpose, DelegationKeyPurpose):
            raise TypeError("rotation purpose must be DelegationKeyPurpose")
        if not 1 <= self.maximum_grant_lifetime_seconds <= 300:
            raise ValueError("maximum grant lifetime is out of bounds")
        if not 0 <= self.clock_skew_seconds <= 60:
            raise ValueError("clock skew is out of bounds")
        if not isinstance(self.rotation_intent_digest, str) or not _DIGEST.fullmatch(
            self.rotation_intent_digest
        ):
            raise ValueError("rotation intent digest is malformed")

    @property
    def kind(self) -> ApprovalSubjectKind:
        return ApprovalSubjectKind.GATEWAY_KEY_ROTATION

    @property
    def subject_id(self) -> str:
        return self.rotation_id

    @property
    def review_digest(self) -> str:
        encoded = json.dumps(
            self.descriptor(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "rotation_id": self.rotation_id,
            "workspace_id": self.workspace_id,
            "gateway_node_id": self.gateway_node_id,
            "purpose": self.purpose.value,
            "issuer": self.issuer,
            "old_key_id": self.old_key_id,
            "overlap_verifier_roles": ["old", "new"],
            "retirement_verifier_roles": ["new"],
            "maximum_grant_lifetime_seconds": self.maximum_grant_lifetime_seconds,
            "clock_skew_seconds": self.clock_skew_seconds,
            "rotation_intent_digest": self.rotation_intent_digest,
        }


ApprovalSubject: TypeAlias = (
    ActivityPlanApprovalSubject | GatewayKeyRotationApprovalSubject
)


def approval_subject_from_descriptor(value: object) -> ApprovalSubject:
    """Decode one exact closed approval subject descriptor."""

    if not isinstance(value, dict):
        raise ValueError("approval subject descriptor must be an object")
    kind = value.get("kind")
    if kind == ApprovalSubjectKind.ACTIVITY_PLAN.value:
        if set(value) != {"kind", "plan_id"}:
            raise ValueError("activity-plan approval subject is malformed")
        return ActivityPlanApprovalSubject(_text(value, "plan_id"))
    if kind == ApprovalSubjectKind.GATEWAY_KEY_ROTATION.value:
        expected = {
            "kind",
            "rotation_id",
            "workspace_id",
            "gateway_node_id",
            "purpose",
            "issuer",
            "old_key_id",
            "overlap_verifier_roles",
            "retirement_verifier_roles",
            "maximum_grant_lifetime_seconds",
            "clock_skew_seconds",
            "rotation_intent_digest",
        }
        if set(value) != expected:
            raise ValueError("gateway-key-rotation approval subject is malformed")
        if value["overlap_verifier_roles"] != ["old", "new"]:
            raise ValueError("rotation overlap review intent is malformed")
        if value["retirement_verifier_roles"] != ["new"]:
            raise ValueError("rotation retirement review intent is malformed")
        try:
            purpose = DelegationKeyPurpose(_text(value, "purpose"))
        except ValueError as error:
            raise ValueError("rotation approval purpose is unsupported") from error
        return GatewayKeyRotationApprovalSubject(
            rotation_id=_text(value, "rotation_id"),
            workspace_id=_text(value, "workspace_id"),
            gateway_node_id=_text(value, "gateway_node_id"),
            purpose=purpose,
            issuer=_text(value, "issuer"),
            old_key_id=_text(value, "old_key_id"),
            maximum_grant_lifetime_seconds=_integer(
                value, "maximum_grant_lifetime_seconds"
            ),
            clock_skew_seconds=_integer(value, "clock_skew_seconds"),
            rotation_intent_digest=_text(value, "rotation_intent_digest"),
        )
    raise ValueError("approval subject kind is unsupported")


def _identifier(value: object, field: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} is malformed")


def _text(value: dict[object, object], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str):
        raise ValueError(f"{field} is malformed")
    return item


def _integer(value: dict[object, object], field: str) -> int:
    item = value.get(field)
    if type(item) is not int:
        raise ValueError(f"{field} is malformed")
    return item


__all__ = [
    "ActivityPlanApprovalSubject",
    "ApprovalSubject",
    "ApprovalSubjectKind",
    "GatewayKeyRotationApprovalSubject",
    "approval_subject_from_descriptor",
]
