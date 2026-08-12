"""Closed paging language for Operations-owned read collections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import Callable, Generic, TypeVar
import unicodedata

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose


_GENERAL_IDENTIFIER_LIMIT = 512
_DELEGATION_IDENTIFIER = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_CANONICAL_INSTANT = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
_CURSOR_KEYS = frozenset({"format_version", "collection", "scope", "position"})

T = TypeVar("T")
U = TypeVar("U")


class ReadPageError(ValueError):
    """Raised when bounded read-page data is malformed or incongruent."""


class ReadCollection(StrEnum):
    """Finite Operations-owned collection vocabulary."""

    ACTIVITY_SESSIONS = "activity-sessions"
    OPEN_SESSIONS = "open-sessions"
    SESSION_ACTIONS = "session-actions"
    SESSION_PLANS = "session-plans"
    SESSION_APPROVALS = "session-approvals"
    PENDING_APPROVALS = "pending-approvals"
    PLAN_RUNS = "plan-runs"
    RUN_EVENTS = "run-events"
    LATEST_OBSERVATIONS = "latest-observations"
    RUNTIME_AUTHORITIES = "runtime-authorities"
    RUNTIME_AUTHORITY_DELIVERIES = "runtime-authority-deliveries"
    INGRESS_AUTHORITIES = "ingress-authorities"
    SECRET_PROVIDERS = "secret-providers"
    SECRET_REFERENCES = "secret-references"
    DELEGATION_SIGNING_KEYS = "delegation-signing-keys"
    GATEWAY_PROBES = "gateway-probes"


class ReadOrder(StrEnum):
    """Specification-only keyset direction."""

    ASCENDING = "ascending"
    DESCENDING = "descending"


@dataclass(frozen=True, slots=True)
class WorkspaceReadScope:
    workspace_id: str

    def __post_init__(self) -> None:
        _general_identifier(self.workspace_id)

    def descriptor(self) -> dict[str, str]:
        return {"workspace_id": self.workspace_id}


@dataclass(frozen=True, slots=True)
class SessionReadScope:
    workspace_id: str
    session_id: str

    def __post_init__(self) -> None:
        _general_identifier(self.workspace_id)
        _general_identifier(self.session_id)

    def descriptor(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
        }


@dataclass(frozen=True, slots=True)
class PlanReadScope:
    workspace_id: str
    plan_id: str

    def __post_init__(self) -> None:
        _general_identifier(self.workspace_id)
        _general_identifier(self.plan_id)

    def descriptor(self) -> dict[str, str]:
        return {"workspace_id": self.workspace_id, "plan_id": self.plan_id}


@dataclass(frozen=True, slots=True)
class RunReadScope:
    workspace_id: str
    run_id: str

    def __post_init__(self) -> None:
        _general_identifier(self.workspace_id)
        _general_identifier(self.run_id)

    def descriptor(self) -> dict[str, str]:
        return {"workspace_id": self.workspace_id, "run_id": self.run_id}


ReadScope = WorkspaceReadScope | SessionReadScope | PlanReadScope | RunReadScope


@dataclass(frozen=True, slots=True)
class OrdinalReadCursor:
    collection: ReadCollection
    scope: ReadScope
    ordinal: int
    item_id: str

    def __post_init__(self) -> None:
        _cursor_header(self.collection, self.scope, type(self))
        if type(self.ordinal) is not int or self.ordinal <= 0:
            raise ReadPageError("ordinal cursor position is malformed")
        _general_identifier(self.item_id)

    def descriptor(self) -> dict[str, object]:
        return _cursor_descriptor(
            self.collection,
            self.scope,
            {"ordinal": self.ordinal, "item_id": self.item_id},
        )


@dataclass(frozen=True, slots=True)
class TemporalReadCursor:
    collection: ReadCollection
    scope: ReadScope
    instant: str
    item_id: str

    def __post_init__(self) -> None:
        _cursor_header(self.collection, self.scope, type(self))
        _canonical_instant(self.instant)
        _general_identifier(self.item_id)

    def descriptor(self) -> dict[str, object]:
        return _cursor_descriptor(
            self.collection,
            self.scope,
            {"instant": self.instant, "item_id": self.item_id},
        )


@dataclass(frozen=True, slots=True)
class IdentityReadCursor:
    collection: ReadCollection
    scope: ReadScope
    item_id: str

    def __post_init__(self) -> None:
        _cursor_header(self.collection, self.scope, type(self))
        _general_identifier(self.item_id)

    def descriptor(self) -> dict[str, object]:
        return _cursor_descriptor(
            self.collection,
            self.scope,
            {"item_id": self.item_id},
        )


@dataclass(frozen=True, slots=True)
class DelegationKeyReadCursor:
    collection: ReadCollection
    scope: ReadScope
    purpose: DelegationKeyPurpose
    issuer: str
    key_id: str

    def __post_init__(self) -> None:
        _cursor_header(self.collection, self.scope, type(self))
        if not isinstance(self.purpose, DelegationKeyPurpose):
            raise ReadPageError("delegation cursor purpose is unsupported")
        _delegation_identifier(self.issuer)
        _delegation_identifier(self.key_id)

    def descriptor(self) -> dict[str, object]:
        return _cursor_descriptor(
            self.collection,
            self.scope,
            {
                "purpose": self.purpose.value,
                "issuer": self.issuer,
                "key_id": self.key_id,
            },
        )


ReadCursor = (
    OrdinalReadCursor
    | TemporalReadCursor
    | IdentityReadCursor
    | DelegationKeyReadCursor
)


@dataclass(frozen=True, slots=True)
class ReadCollectionSpec:
    collection: ReadCollection
    route_id: str
    scope_type: type[ReadScope]
    cursor_type: type[ReadCursor]
    order: ReadOrder
    position_fields: tuple[str, ...]


READ_COLLECTION_SPECS = (
    ReadCollectionSpec(ReadCollection.ACTIVITY_SESSIONS, "read.activity", WorkspaceReadScope, TemporalReadCursor, ReadOrder.ASCENDING, ("created_at", "session_id")),
    ReadCollectionSpec(ReadCollection.OPEN_SESSIONS, "read.sessions", WorkspaceReadScope, TemporalReadCursor, ReadOrder.ASCENDING, ("created_at", "session_id")),
    ReadCollectionSpec(ReadCollection.SESSION_ACTIONS, "read.session-actions", SessionReadScope, OrdinalReadCursor, ReadOrder.ASCENDING, ("ordinal", "action_id")),
    ReadCollectionSpec(ReadCollection.SESSION_PLANS, "read.session-plans", SessionReadScope, TemporalReadCursor, ReadOrder.ASCENDING, ("created_at", "plan_id")),
    ReadCollectionSpec(ReadCollection.SESSION_APPROVALS, "read.session-approvals", SessionReadScope, TemporalReadCursor, ReadOrder.ASCENDING, ("requested_at", "request_id")),
    ReadCollectionSpec(ReadCollection.PENDING_APPROVALS, "read.pending-approvals", WorkspaceReadScope, TemporalReadCursor, ReadOrder.ASCENDING, ("requested_at", "request_id")),
    ReadCollectionSpec(ReadCollection.PLAN_RUNS, "read.plan-runs", PlanReadScope, TemporalReadCursor, ReadOrder.ASCENDING, ("created_at", "run_id")),
    ReadCollectionSpec(ReadCollection.RUN_EVENTS, "read.run-events", RunReadScope, OrdinalReadCursor, ReadOrder.ASCENDING, ("ordinal", "event_id")),
    ReadCollectionSpec(ReadCollection.LATEST_OBSERVATIONS, "read.observed-state", WorkspaceReadScope, IdentityReadCursor, ReadOrder.ASCENDING, ("subject_id",)),
    ReadCollectionSpec(ReadCollection.RUNTIME_AUTHORITIES, "read.runtime-authorities", WorkspaceReadScope, IdentityReadCursor, ReadOrder.ASCENDING, ("authority_ref",)),
    ReadCollectionSpec(ReadCollection.RUNTIME_AUTHORITY_DELIVERIES, "read.runtime-authority-deliveries", WorkspaceReadScope, IdentityReadCursor, ReadOrder.ASCENDING, ("authority_ref",)),
    ReadCollectionSpec(ReadCollection.INGRESS_AUTHORITIES, "read.ingress-authorities", WorkspaceReadScope, IdentityReadCursor, ReadOrder.ASCENDING, ("authority_ref",)),
    ReadCollectionSpec(ReadCollection.SECRET_PROVIDERS, "read.secret-providers", WorkspaceReadScope, IdentityReadCursor, ReadOrder.ASCENDING, ("provider_id",)),
    ReadCollectionSpec(ReadCollection.SECRET_REFERENCES, "read.secret-references", WorkspaceReadScope, IdentityReadCursor, ReadOrder.ASCENDING, ("registration_id",)),
    ReadCollectionSpec(ReadCollection.DELEGATION_SIGNING_KEYS, "read.delegation-keys", WorkspaceReadScope, DelegationKeyReadCursor, ReadOrder.ASCENDING, ("purpose", "issuer", "key_id")),
    ReadCollectionSpec(ReadCollection.GATEWAY_PROBES, "read.gateway-probe-timeline", WorkspaceReadScope, TemporalReadCursor, ReadOrder.DESCENDING, ("issued_at", "probe_id")),
)
_SPEC_BY_COLLECTION = {spec.collection: spec for spec in READ_COLLECTION_SPECS}
_COLLECTION_BY_VALUE = {collection.value: collection for collection in ReadCollection}
_PURPOSE_BY_VALUE = {purpose.value: purpose for purpose in DelegationKeyPurpose}


def read_collection_spec(collection: ReadCollection) -> ReadCollectionSpec:
    if not isinstance(collection, ReadCollection):
        raise ReadPageError("read collection is unsupported")
    return _SPEC_BY_COLLECTION[collection]


def read_cursor_from_mapping(value: object) -> ReadCursor:
    if type(value) is not dict:
        raise ReadPageError("read cursor mapping is malformed")
    _exact_keys(value, _CURSOR_KEYS, "read cursor")
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        raise ReadPageError("read cursor format is unsupported")
    raw_collection = value["collection"]
    if type(raw_collection) is not str:
        raise ReadPageError("read cursor collection is malformed")
    collection = _COLLECTION_BY_VALUE.get(raw_collection)
    if collection is None:
        raise ReadPageError("read cursor collection is unsupported")
    spec = read_collection_spec(collection)
    scope = _scope_from_mapping(spec.scope_type, value["scope"])
    return _position_from_mapping(spec, scope, value["position"])


@dataclass(frozen=True, slots=True)
class ReadPageRequest:
    collection: ReadCollection
    scope: ReadScope
    limit: int
    cursor: ReadCursor | None = None

    def __post_init__(self) -> None:
        spec = read_collection_spec(self.collection)
        if type(self.scope) is not spec.scope_type:
            raise ReadPageError("read request scope is incongruent")
        if type(self.limit) is not int or not 1 <= self.limit <= 100:
            raise ReadPageError("read page limit is malformed")
        if self.cursor is not None and (
            type(self.cursor) is not spec.cursor_type
            or self.cursor.collection is not self.collection
            or self.cursor.scope != self.scope
        ):
            raise ReadPageError("read request cursor is incongruent")


@dataclass(frozen=True, slots=True)
class ReadPageCandidate(Generic[T]):
    item: T
    cursor_after_item: ReadCursor

    def __post_init__(self) -> None:
        if not isinstance(
            self.cursor_after_item,
            (
                OrdinalReadCursor,
                TemporalReadCursor,
                IdentityReadCursor,
                DelegationKeyReadCursor,
            ),
        ):
            raise ReadPageError("read candidate cursor is malformed")


@dataclass(frozen=True, slots=True)
class ReadPage(Generic[T]):
    request: ReadPageRequest
    items: tuple[T, ...]
    next_cursor: ReadCursor | None

    def __post_init__(self) -> None:
        if not isinstance(self.request, ReadPageRequest):
            raise ReadPageError("read page request is malformed")
        if type(self.items) is not tuple or len(self.items) > self.request.limit:
            raise ReadPageError("read page items are malformed")
        if self.next_cursor is not None:
            _matching_cursor(self.request, self.next_cursor)

    @classmethod
    def from_candidates(
        cls,
        request: ReadPageRequest,
        candidates: tuple[ReadPageCandidate[T], ...],
    ) -> ReadPage[T]:
        if not isinstance(request, ReadPageRequest):
            raise ReadPageError("read page request is malformed")
        if type(candidates) is not tuple:
            raise ReadPageError("read page candidates are malformed")
        if len(candidates) > request.limit + 1:
            raise ReadPageError("read page candidate count is malformed")
        for candidate in candidates:
            if not isinstance(candidate, ReadPageCandidate):
                raise ReadPageError("read page candidate is malformed")
            _matching_cursor(request, candidate.cursor_after_item)
        exposed = candidates[: request.limit]
        next_cursor = (
            exposed[-1].cursor_after_item
            if len(candidates) == request.limit + 1
            else None
        )
        return cls(request, tuple(candidate.item for candidate in exposed), next_cursor)

    def map(self, mapper: Callable[[T], U]) -> ReadPage[U]:
        return ReadPage(
            self.request,
            tuple(mapper(item) for item in self.items),
            self.next_cursor,
        )

    def descriptor(self) -> dict[str, object]:
        if any(type(item) is not dict for item in self.items):
            raise ReadPageError("read page item projection is malformed")
        return {
            "workspace_id": self.request.scope.workspace_id,
            "kind": self.request.collection.value,
            "limit": self.request.limit,
            "items": [dict(item) for item in self.items],
            "next_cursor": (
                None if self.next_cursor is None else self.next_cursor.descriptor()
            ),
        }


def _general_identifier(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > _GENERAL_IDENTIFIER_LIMIT
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise ReadPageError("read identifier is malformed")


def _delegation_identifier(value: object) -> None:
    if type(value) is not str or _DELEGATION_IDENTIFIER.fullmatch(value) is None:
        raise ReadPageError("delegation cursor identifier is malformed")


def _canonical_instant(value: object) -> None:
    valid = type(value) is str and _CANONICAL_INSTANT.fullmatch(value) is not None
    if valid:
        try:
            datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            valid = False
    if not valid:
        raise ReadPageError("temporal cursor instant is malformed")


def _cursor_header(
    collection: object,
    scope: object,
    cursor_type: type[object],
) -> None:
    spec = read_collection_spec(collection)
    if type(scope) is not spec.scope_type or cursor_type is not spec.cursor_type:
        raise ReadPageError("read cursor profile is incongruent")


def _cursor_descriptor(
    collection: ReadCollection,
    scope: ReadScope,
    position: dict[str, object],
) -> dict[str, object]:
    return {
        "format_version": 1,
        "collection": collection.value,
        "scope": scope.descriptor(),
        "position": position,
    }


def _exact_keys(value: dict[object, object], expected: frozenset[str], label: str) -> None:
    if len(value) != len(expected) or set(value) != expected:
        raise ReadPageError(f"{label} fields are malformed")


def _scope_from_mapping(scope_type: type[ReadScope], value: object) -> ReadScope:
    if type(value) is not dict:
        raise ReadPageError("read cursor scope is malformed")
    if scope_type is WorkspaceReadScope:
        _exact_keys(value, frozenset({"workspace_id"}), "workspace scope")
        return WorkspaceReadScope(value["workspace_id"])
    if scope_type is SessionReadScope:
        _exact_keys(value, frozenset({"workspace_id", "session_id"}), "session scope")
        return SessionReadScope(value["workspace_id"], value["session_id"])
    if scope_type is PlanReadScope:
        _exact_keys(value, frozenset({"workspace_id", "plan_id"}), "plan scope")
        return PlanReadScope(value["workspace_id"], value["plan_id"])
    if scope_type is RunReadScope:
        _exact_keys(value, frozenset({"workspace_id", "run_id"}), "run scope")
        return RunReadScope(value["workspace_id"], value["run_id"])
    raise ReadPageError("read cursor scope is unsupported")


def _position_from_mapping(
    spec: ReadCollectionSpec,
    scope: ReadScope,
    value: object,
) -> ReadCursor:
    if type(value) is not dict:
        raise ReadPageError("read cursor position is malformed")
    if spec.cursor_type is OrdinalReadCursor:
        _exact_keys(value, frozenset({"ordinal", "item_id"}), "ordinal position")
        return OrdinalReadCursor(
            spec.collection,
            scope,
            value["ordinal"],
            value["item_id"],
        )
    if spec.cursor_type is TemporalReadCursor:
        _exact_keys(value, frozenset({"instant", "item_id"}), "temporal position")
        return TemporalReadCursor(
            spec.collection,
            scope,
            value["instant"],
            value["item_id"],
        )
    if spec.cursor_type is IdentityReadCursor:
        _exact_keys(value, frozenset({"item_id"}), "identity position")
        return IdentityReadCursor(spec.collection, scope, value["item_id"])
    _exact_keys(
        value,
        frozenset({"purpose", "issuer", "key_id"}),
        "delegation-key position",
    )
    raw_purpose = value["purpose"]
    if type(raw_purpose) is not str:
        raise ReadPageError("delegation cursor purpose is malformed")
    purpose = _PURPOSE_BY_VALUE.get(raw_purpose)
    if purpose is None:
        raise ReadPageError("delegation cursor purpose is unsupported")
    return DelegationKeyReadCursor(
        spec.collection,
        scope,
        purpose,
        value["issuer"],
        value["key_id"],
    )


def _matching_cursor(request: ReadPageRequest, cursor: ReadCursor) -> None:
    spec = read_collection_spec(request.collection)
    if (
        type(cursor) is not spec.cursor_type
        or cursor.collection is not request.collection
        or cursor.scope != request.scope
    ):
        raise ReadPageError("read page cursor is incongruent")


__all__ = [
    "READ_COLLECTION_SPECS",
    "DelegationKeyReadCursor",
    "IdentityReadCursor",
    "OrdinalReadCursor",
    "PlanReadScope",
    "ReadCollection",
    "ReadOrder",
    "ReadPage",
    "ReadPageCandidate",
    "ReadPageError",
    "ReadPageRequest",
    "RunReadScope",
    "SessionReadScope",
    "TemporalReadCursor",
    "WorkspaceReadScope",
    "read_collection_spec",
    "read_cursor_from_mapping",
]
