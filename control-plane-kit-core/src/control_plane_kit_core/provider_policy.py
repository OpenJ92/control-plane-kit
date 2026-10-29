"""Pure provider-policy administration commands and provider port values."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Protocol


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,511}\Z")
_LOGICAL_PROVIDER_ID = re.compile(r"[a-z][a-z0-9._-]{0,127}\Z")
_MAX_REVISION = (1 << 63) - 1


class ProviderPolicyContractError(ValueError):
    """Raised when provider-policy protocol material is malformed."""


class ProviderPolicyOperation(StrEnum):
    GRANT = "grant"
    REVOKE = "revoke"
    READ = "read"
    LIST = "list"
    COMMAND_RESULT = "command-result"


class ProviderPolicyIntentMode(StrEnum):
    ANY = "any"
    NONE = "none"
    EXACT = "exact"


class ProviderPolicyOutcome(StrEnum):
    GRANTED = "granted"
    REVOKED = "revoked"
    CONFLICT = "conflict"


class ProviderPolicyErrorCode(StrEnum):
    INVALID = "invalid"
    DENIED = "denied"
    MISSING = "missing"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    TRANSPORT = "transport"
    MALFORMED_RESULT = "malformed-result"


class ProviderPolicyFailure(RuntimeError):
    """Bounded noncanonical provider failure, with explicit effect ambiguity."""

    def __init__(
        self,
        code: ProviderPolicyErrorCode,
        message: str,
        *,
        effect_may_have_occurred: bool = False,
    ) -> None:
        if not isinstance(code, ProviderPolicyErrorCode):
            raise ProviderPolicyContractError(
                "provider-policy failure code must be ProviderPolicyErrorCode"
            )
        _bounded_text(message, "provider-policy failure message")
        if type(effect_may_have_occurred) is not bool:
            raise ProviderPolicyContractError(
                "provider-policy failure ambiguity must be boolean"
            )
        if effect_may_have_occurred and code not in (
            ProviderPolicyErrorCode.TRANSPORT,
            ProviderPolicyErrorCode.UNAVAILABLE,
            ProviderPolicyErrorCode.MALFORMED_RESULT,
        ):
            raise ProviderPolicyContractError(
                "provider-policy definite failure cannot claim effect ambiguity"
            )
        self.code = code
        self.effect_may_have_occurred = effect_may_have_occurred
        super().__init__(message)

    def descriptor(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "message": str(self),
            "effect_may_have_occurred": self.effect_may_have_occurred,
        }


@dataclass(frozen=True, order=True)
class ProviderPolicyCoordinate:
    subject: str
    workspace_id: str
    action: str
    intent: str | None = None

    def __post_init__(self) -> None:
        _bounded_text(self.subject, "provider-policy subject")
        _bounded_text(self.workspace_id, "provider-policy workspace_id")
        _bounded_text(self.action, "provider-policy action")
        if self.intent is not None:
            _bounded_text(self.intent, "provider-policy intent")

    def descriptor(self) -> dict[str, str | None]:
        return {
            "subject": self.subject,
            "workspace_id": self.workspace_id,
            "action": self.action,
            "intent": self.intent,
        }


@dataclass(frozen=True)
class ProviderPolicyGrantCommand:
    logical_provider_id: str
    provider_id: str
    coordinate: ProviderPolicyCoordinate
    expected_revision: int
    caller_idempotency_key: str
    provider_correlation: str
    operation: ProviderPolicyOperation = field(
        init=False, default=ProviderPolicyOperation.GRANT
    )

    def __post_init__(self) -> None:
        _mutation_fields(self)

    def descriptor(self) -> dict[str, object]:
        return _mutation_descriptor(self)


@dataclass(frozen=True)
class ProviderPolicyRevokeCommand:
    logical_provider_id: str
    provider_id: str
    coordinate: ProviderPolicyCoordinate
    expected_revision: int
    caller_idempotency_key: str
    provider_correlation: str
    grant_id: str
    operation: ProviderPolicyOperation = field(
        init=False, default=ProviderPolicyOperation.REVOKE
    )

    def __post_init__(self) -> None:
        _mutation_fields(self)
        _identifier(self.grant_id, "provider-policy grant_id")

    def descriptor(self) -> dict[str, object]:
        return {**_mutation_descriptor(self), "grant_id": self.grant_id}


ProviderPolicyMutationCommand = (
    ProviderPolicyGrantCommand | ProviderPolicyRevokeCommand
)


@dataclass(frozen=True)
class ProviderPolicyReadRequest:
    logical_provider_id: str
    grant_id: str
    operation: ProviderPolicyOperation = field(
        init=False, default=ProviderPolicyOperation.READ
    )

    def __post_init__(self) -> None:
        _logical_provider(self.logical_provider_id)
        _identifier(self.grant_id, "provider-policy grant_id")


@dataclass(frozen=True)
class ProviderPolicyListRequest:
    logical_provider_id: str
    subject: str | None = None
    workspace_id: str | None = None
    action: str | None = None
    intent_mode: ProviderPolicyIntentMode = ProviderPolicyIntentMode.ANY
    intent: str | None = None
    limit: int = 100
    after_grant_id: str | None = None
    operation: ProviderPolicyOperation = field(
        init=False, default=ProviderPolicyOperation.LIST
    )

    def __post_init__(self) -> None:
        _logical_provider(self.logical_provider_id)
        for value, name in (
            (self.subject, "subject"),
            (self.workspace_id, "workspace_id"),
            (self.action, "action"),
        ):
            if value is not None:
                _bounded_text(value, f"provider-policy list {name}")
        if not isinstance(self.intent_mode, ProviderPolicyIntentMode):
            raise ProviderPolicyContractError(
                "provider-policy intent_mode must be ProviderPolicyIntentMode"
            )
        if self.intent_mode is ProviderPolicyIntentMode.EXACT:
            if self.intent is None:
                raise ProviderPolicyContractError(
                    "provider-policy exact intent filter requires intent"
                )
            _bounded_text(self.intent, "provider-policy list intent")
        elif self.intent is not None:
            raise ProviderPolicyContractError(
                "provider-policy intent is allowed only for exact intent filter"
            )
        if type(self.limit) is not int or not 1 <= self.limit <= 100:
            raise ProviderPolicyContractError(
                "provider-policy list limit must be between 1 and 100"
            )
        if self.after_grant_id is not None:
            _identifier(
                self.after_grant_id, "provider-policy list after_grant_id"
            )


@dataclass(frozen=True)
class ProviderPolicyCommandResultRequest:
    logical_provider_id: str
    provider_correlation: str
    operation: ProviderPolicyOperation = field(
        init=False, default=ProviderPolicyOperation.COMMAND_RESULT
    )

    def __post_init__(self) -> None:
        _logical_provider(self.logical_provider_id)
        _identifier(
            self.provider_correlation, "provider-policy provider_correlation"
        )


@dataclass(frozen=True)
class ProviderPolicyGrantRecord:
    grant_id: str
    provider_id: str
    coordinate: ProviderPolicyCoordinate
    origin: str
    granted_by: str
    grant_correlation_id: str
    granted_at: str
    revoked_by: str | None = None
    revoke_correlation_id: str | None = None
    revoked_at: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.grant_id, "provider-policy grant_id")
        _provider_identity(self.provider_id)
        if type(self.coordinate) is not ProviderPolicyCoordinate:
            raise ProviderPolicyContractError(
                "provider-policy grant coordinate is invalid"
            )
        for value, name in (
            (self.origin, "origin"),
            (self.granted_by, "granted_by"),
            (self.grant_correlation_id, "grant_correlation_id"),
            (self.granted_at, "granted_at"),
        ):
            _bounded_text(value, f"provider-policy grant {name}")
        optional = (
            self.revoked_by,
            self.revoke_correlation_id,
            self.revoked_at,
        )
        if any(value is None for value in optional) and not all(
            value is None for value in optional
        ):
            raise ProviderPolicyContractError(
                "provider-policy revocation fields must be all present or absent"
            )
        for value in optional:
            if value is not None:
                _bounded_text(value, "provider-policy revocation field")


@dataclass(frozen=True)
class ProviderPolicyGrantRead:
    revision: int
    grant: ProviderPolicyGrantRecord

    def __post_init__(self) -> None:
        _revision(self.revision)
        if type(self.grant) is not ProviderPolicyGrantRecord:
            raise ProviderPolicyContractError(
                "provider-policy read grant is invalid"
            )


@dataclass(frozen=True)
class ProviderPolicyGrantPage:
    revision: int
    grants: tuple[ProviderPolicyGrantRecord, ...]
    next_after: str | None = None

    def __post_init__(self) -> None:
        _revision(self.revision)
        if not isinstance(self.grants, tuple) or not all(
            type(grant) is ProviderPolicyGrantRecord for grant in self.grants
        ):
            raise ProviderPolicyContractError(
                "provider-policy page grants must be a tuple of grant records"
            )
        if len(self.grants) > 100:
            raise ProviderPolicyContractError(
                "provider-policy page contains too many grants"
            )
        if self.next_after is not None:
            _identifier(self.next_after, "provider-policy page next_after")


@dataclass(frozen=True)
class ProviderPolicyCommandResult:
    provider_id: str
    correlation_id: str
    operation: ProviderPolicyOperation
    coordinate: ProviderPolicyCoordinate
    expected_revision: int
    outcome: ProviderPolicyOutcome
    code: str
    grant_id: str | None
    resulting_revision: int
    provider_actor: str
    occurred_at: str

    def __post_init__(self) -> None:
        _provider_identity(self.provider_id)
        _identifier(self.correlation_id, "provider-policy correlation_id")
        if not isinstance(self.operation, ProviderPolicyOperation) or self.operation not in (
            ProviderPolicyOperation.GRANT,
            ProviderPolicyOperation.REVOKE,
        ):
            raise ProviderPolicyContractError(
                "provider-policy result operation must be grant or revoke"
            )
        if type(self.coordinate) is not ProviderPolicyCoordinate:
            raise ProviderPolicyContractError(
                "provider-policy result coordinate is invalid"
            )
        _revision(self.expected_revision)
        _revision(self.resulting_revision)
        if not isinstance(self.outcome, ProviderPolicyOutcome):
            raise ProviderPolicyContractError(
                "provider-policy outcome must be ProviderPolicyOutcome"
            )
        _bounded_text(self.code, "provider-policy result code")
        if self.grant_id is not None:
            _identifier(self.grant_id, "provider-policy result grant_id")
        if self.outcome in (
            ProviderPolicyOutcome.GRANTED,
            ProviderPolicyOutcome.REVOKED,
        ) and self.grant_id is None:
            raise ProviderPolicyContractError(
                "successful provider-policy result requires grant_id"
            )
        expected_outcome = {
            ProviderPolicyOperation.GRANT: ProviderPolicyOutcome.GRANTED,
            ProviderPolicyOperation.REVOKE: ProviderPolicyOutcome.REVOKED,
        }[self.operation]
        expected_code = {
            ProviderPolicyOperation.GRANT: "provider-policy-granted",
            ProviderPolicyOperation.REVOKE: "provider-policy-revoked",
        }[self.operation]
        if self.outcome is ProviderPolicyOutcome.CONFLICT:
            if self.code == "policy-revision-conflict":
                valid_conflict = (
                    self.resulting_revision != self.expected_revision
                    and (
                        (
                            self.operation is ProviderPolicyOperation.GRANT
                            and self.grant_id is None
                        )
                        or (
                            self.operation is ProviderPolicyOperation.REVOKE
                            and self.grant_id is not None
                        )
                    )
                )
            elif self.code == "provider-grant-active":
                valid_conflict = (
                    self.operation is ProviderPolicyOperation.GRANT
                    and self.grant_id is not None
                    and self.resulting_revision == self.expected_revision
                )
            elif self.code == "provider-grant-revoke-conflict":
                valid_conflict = (
                    self.operation is ProviderPolicyOperation.REVOKE
                    and self.grant_id is not None
                    and self.resulting_revision == self.expected_revision
                )
            else:
                valid_conflict = False
            if not valid_conflict:
                raise ProviderPolicyContractError(
                    "provider-policy conflict result shape is invalid"
                )
        else:
            if self.outcome is not expected_outcome or self.code != expected_code:
                raise ProviderPolicyContractError(
                    "provider-policy result operation, outcome and code conflict"
                )
            if self.resulting_revision != self.expected_revision + 1:
                raise ProviderPolicyContractError(
                    "successful provider-policy result revision is inconsistent"
                )
        _bounded_text(self.provider_actor, "provider-policy result provider_actor")
        _bounded_text(self.occurred_at, "provider-policy result occurred_at")

    def matches(self, command: ProviderPolicyMutationCommand) -> bool:
        if not isinstance(
            command, (ProviderPolicyGrantCommand, ProviderPolicyRevokeCommand)
        ):
            return False
        if (
            self.provider_id != command.provider_id
            or self.correlation_id != command.provider_correlation
            or self.operation is not command.operation
            or self.coordinate != command.coordinate
            or self.expected_revision != command.expected_revision
        ):
            return False
        return not isinstance(command, ProviderPolicyRevokeCommand) or (
            self.grant_id == command.grant_id
        )


class ProviderPolicyPort(Protocol):
    """Typed provider effect/read boundary implemented outside Core.

    Implementations raise ``ProviderPolicyFailure`` with interpreter-authored,
    bounded safe text; raw response bodies and exception strings are not valid
    messages. A failed command-result read never resolves an earlier ambiguous
    mutation, and no failure authorizes redispatch of that mutation.
    """

    def grant(
        self, command: ProviderPolicyGrantCommand
    ) -> ProviderPolicyCommandResult: ...

    def revoke(
        self, command: ProviderPolicyRevokeCommand
    ) -> ProviderPolicyCommandResult: ...

    def read(self, request: ProviderPolicyReadRequest) -> ProviderPolicyGrantRead: ...

    def list(
        self, request: ProviderPolicyListRequest
    ) -> ProviderPolicyGrantPage: ...

    def command_result(
        self, request: ProviderPolicyCommandResultRequest
    ) -> ProviderPolicyCommandResult: ...


def provider_policy_command_fingerprint(
    command: ProviderPolicyMutationCommand,
) -> str:
    if not isinstance(
        command, (ProviderPolicyGrantCommand, ProviderPolicyRevokeCommand)
    ):
        raise ProviderPolicyContractError(
            "provider-policy fingerprint requires a mutation command"
        )
    descriptor = command.descriptor().copy()
    descriptor.pop("caller_idempotency_key")
    descriptor.pop("provider_correlation")
    encoded = json.dumps(
        descriptor, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return sha256(encoded).hexdigest()


def _mutation_fields(command: ProviderPolicyMutationCommand) -> None:
    _logical_provider(command.logical_provider_id)
    _provider_identity(command.provider_id)
    if type(command.coordinate) is not ProviderPolicyCoordinate:
        raise ProviderPolicyContractError(
            "provider-policy command coordinate is invalid"
        )
    _revision(command.expected_revision)
    _identifier(
        command.caller_idempotency_key,
        "provider-policy caller_idempotency_key",
    )
    _identifier(
        command.provider_correlation,
        "provider-policy provider_correlation",
    )


def _mutation_descriptor(
    command: ProviderPolicyMutationCommand,
) -> dict[str, object]:
    return {
        "logical_provider_id": command.logical_provider_id,
        "provider_id": command.provider_id,
        "operation": command.operation.value,
        **command.coordinate.descriptor(),
        "expected_revision": command.expected_revision,
        "caller_idempotency_key": command.caller_idempotency_key,
        "provider_correlation": command.provider_correlation,
    }


def _logical_provider(value: object) -> None:
    if not isinstance(value, str) or not _LOGICAL_PROVIDER_ID.fullmatch(value):
        raise ProviderPolicyContractError(
            "provider-policy logical provider identity is malformed"
        )


def _provider_identity(value: object) -> None:
    _bounded_text(value, "provider-policy provider identity")


def _identifier(value: object, name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ProviderPolicyContractError(f"{name} is malformed")


def _bounded_text(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 512
        or any(ord(character) < 32 for character in value)
    ):
        raise ProviderPolicyContractError(f"{name} is malformed")


def _revision(value: object) -> None:
    if type(value) is not int or not 0 <= value <= _MAX_REVISION:
        raise ProviderPolicyContractError(
            "provider-policy revision must be a signed 64-bit nonnegative integer"
        )


__all__ = [
    "ProviderPolicyCommandResult",
    "ProviderPolicyCommandResultRequest",
    "ProviderPolicyContractError",
    "ProviderPolicyCoordinate",
    "ProviderPolicyErrorCode",
    "ProviderPolicyFailure",
    "ProviderPolicyGrantCommand",
    "ProviderPolicyGrantPage",
    "ProviderPolicyGrantRead",
    "ProviderPolicyGrantRecord",
    "ProviderPolicyIntentMode",
    "ProviderPolicyListRequest",
    "ProviderPolicyMutationCommand",
    "ProviderPolicyOperation",
    "ProviderPolicyOutcome",
    "ProviderPolicyPort",
    "ProviderPolicyReadRequest",
    "ProviderPolicyRevokeCommand",
    "provider_policy_command_fingerprint",
]
