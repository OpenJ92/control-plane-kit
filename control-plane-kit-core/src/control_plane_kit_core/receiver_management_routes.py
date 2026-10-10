"""Pure receiver-management routes; graph eligibility and delivery are external."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import re
from urllib.parse import urlsplit

from control_plane_kit_core.receiver_identity import NodeControlReceiverTarget, NodeControlReceiverTargetCodec
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationCodec,
    WorkloadNodeControlSurfaceDeclarationProfile,
)

_PROFILE = "cpk-gateway-health-relay-configuration.v2"
_MAX_BYTES = 131_072
_ID = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_HOST = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\Z")
_FIELDS = frozenset({"profile", "gateway_target", "targets"})
_BINDING_FIELDS = frozenset({"target_id", "target", "declaration", "origin"})
_ERROR = "gateway health relay configuration is invalid"
_INPUT_ERRORS = (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError)


class ReceiverManagementRoutesError(ValueError):
    """Bounded configuration failure without candidate context."""


def _hostname(value: object) -> str:
    if type(value) is not str or len(value) > 253 or _HOST.fullmatch(value) is None:
        raise ValueError
    return value


def _target(raw: object) -> NodeControlReceiverTarget:
    return NodeControlReceiverTargetCodec().decode(raw)


@dataclass(frozen=True, slots=True, repr=False)
class ReceiverManagementRouteBinding:
    target_id: str
    target: NodeControlReceiverTarget
    declaration: WorkloadNodeControlSurfaceDeclaration
    origin: str

    def __post_init__(self) -> None:
        try:
            if type(self.target_id) is not str or _ID.fullmatch(self.target_id) is None:
                raise ValueError
            if type(self.target) is not NodeControlReceiverTarget or _target(self.target.descriptor()) != self.target:
                raise ValueError
            if type(self.declaration) is not WorkloadNodeControlSurfaceDeclaration:
                raise ValueError
            declaration = WorkloadNodeControlSurfaceDeclarationCodec().decode(self.declaration.descriptor())
            if (declaration != self.declaration or declaration.profile is not WorkloadNodeControlSurfaceDeclarationProfile.V2
                    or declaration.surface.provider_socket_name != self.target.provider_socket_name):
                raise ValueError
            if type(self.origin) is not str:
                raise ValueError
            parsed = urlsplit(self.origin)
            host = _hostname(parsed.hostname)
            if (parsed.scheme != "http" or parsed.username is not None or parsed.password is not None
                    or parsed.path or parsed.query or parsed.fragment or parsed.port is None
                    or not 1 <= parsed.port <= 65535 or self.origin != f"http://{host}:{parsed.port}"):
                raise ValueError
            return
        except _INPUT_ERRORS:
            failure = ReceiverManagementRoutesError(_ERROR)
        raise failure

    def descriptor(self) -> dict[str, object]:
        return dict(target_id=self.target_id, target=self.target.descriptor(),
                    declaration=self.declaration.descriptor(), origin=self.origin)


@dataclass(frozen=True, slots=True, repr=False)
class ReceiverManagementRoutes:
    gateway_target: NodeControlReceiverTarget
    targets: tuple[ReceiverManagementRouteBinding, ...]

    def __post_init__(self) -> None:
        try:
            if (type(self.gateway_target) is not NodeControlReceiverTarget
                    or _target(self.gateway_target.descriptor()) != self.gateway_target):
                raise ValueError
            if type(self.targets) is not tuple or len(self.targets) > 128:
                raise ValueError
            admitted = []
            for item in self.targets:
                if type(item) is not ReceiverManagementRouteBinding:
                    raise ValueError
                item = replace(item)
                if item.target.workspace_id != self.gateway_target.workspace_id or item.target.runtime_id != self.gateway_target.runtime_id:
                    raise ValueError
                admitted.append(item)
            if (len({item.target_id for item in admitted}) != len(admitted)
                    or len({item.target for item in admitted}) != len(admitted)):
                raise ValueError
            object.__setattr__(self, "targets", tuple(sorted(admitted, key=lambda item:item.target_id)))
            if len(_encode(self)) > _MAX_BYTES:
                raise ValueError
            return
        except _INPUT_ERRORS:
            failure = ReceiverManagementRoutesError(_ERROR)
        raise failure

    def descriptor(self) -> dict[str, object]:
        return dict(profile=_PROFILE, gateway_target=self.gateway_target.descriptor(), targets=[item.descriptor() for item in self.targets])


def _encode(value: ReceiverManagementRoutes) -> bytes:
    return json.dumps(value.descriptor(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _object(value: object, keys: frozenset[str]) -> dict:
    if type(value) is not dict or frozenset(value) != keys:
        raise ValueError
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError


def _structural_json(value: object, depth: int = 0) -> None:
    if depth > 8:
        raise ValueError
    if type(value) is dict:
        for item in value.values():
            _structural_json(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _structural_json(item, depth + 1)
    elif value is not None and type(value) not in (str, int, bool):
        raise ValueError


def _decode_json(raw: bytes, maximum: int) -> object:
    if type(raw) is not bytes or not 1 <= len(raw) <= maximum:
        raise ValueError
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
        parse_constant=_reject_constant)
    _structural_json(value)
    return value


class ReceiverManagementRoutesCodec:
    """Closed v2 syntax; graph authorization and delivery remain external."""

    def encode(self, routes: ReceiverManagementRoutes) -> dict[str, object]:
        try:
            if type(routes) is not ReceiverManagementRoutes:
                raise ValueError
            return replace(routes).descriptor()
        except _INPUT_ERRORS:
            failure = ReceiverManagementRoutesError(_ERROR)
        raise failure

    def encode_bytes(self, routes: ReceiverManagementRoutes) -> bytes:
        return json.dumps(self.encode(routes), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()

    def decode(self, document: object) -> ReceiverManagementRoutes:
        try:
            _structural_json(document)
            value = _object(document, _FIELDS)
            if value["profile"] != _PROFILE or type(value["targets"]) is not list or len(value["targets"]) > 128:
                raise ValueError
            bindings = []
            for entry in value["targets"]:
                entry = _object(entry, _BINDING_FIELDS)
                bindings.append(ReceiverManagementRouteBinding(
                    entry["target_id"], _target(entry["target"]),
                    WorkloadNodeControlSurfaceDeclarationCodec().decode(entry["declaration"]), entry["origin"]))
            return ReceiverManagementRoutes(_target(value["gateway_target"]), tuple(bindings))
        except _INPUT_ERRORS:
            failure = ReceiverManagementRoutesError(_ERROR)
        raise failure

    def decode_bytes(self, raw: bytes) -> ReceiverManagementRoutes:
        try:
            return self.decode(_decode_json(raw, _MAX_BYTES))
        except _INPUT_ERRORS:
            failure = ReceiverManagementRoutesError(_ERROR)
        raise failure


__all__ = [
    "ReceiverManagementRouteBinding", "ReceiverManagementRoutes",
    "ReceiverManagementRoutesCodec", "ReceiverManagementRoutesError",
]
