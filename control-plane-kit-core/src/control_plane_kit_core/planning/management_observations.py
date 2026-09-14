"""Bounded management observation requests; no graph, authority or effects."""
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import re

from control_plane_kit_core._node_control_public_wire import canonical_json_bytes
from control_plane_kit_core.node_control import NodeHealthReadKind
from control_plane_kit_core.runtime_management import RuntimeManagementError, _reference


class ManagementObservationError(ValueError):
    """Fixed categorical failure that never retains candidate material."""


class PlanGraphSide(StrEnum):
    BASE_GRAPH = "base-graph"
    DESIRED_GRAPH = "desired-graph"


class ManagementBootstrapStage(StrEnum):
    GATEWAY_LOCAL_READY = "gateway-local-ready"
    CONNECTOR_CONNECTED = "connector-connected"
    AUTHENTICATED_MANAGEMENT_PATH = "authenticated-management-path"


class NodeHealthObservationTransport(StrEnum):
    RUNTIME_GATEWAY = "runtime-gateway"


def _bounded_reference(value, *, socket=False):
    invalid = type(value) is not str
    if not invalid:
        try:
            _reference(value, socket=socket)
        except (RuntimeManagementError, TypeError, ValueError):
            invalid = True
    if invalid:
        raise ManagementObservationError("management observation reference is malformed")


def _bounded_wire(value, maximum):
    encoded = None
    try:
        encoded = canonical_json_bytes(value)
    except (TypeError, ValueError, OverflowError, RecursionError):
        pass
    if encoded is None or len(encoded) > maximum:
        raise ManagementObservationError("management observation material is outside its bound")
    return encoded


@dataclass(frozen=True)
class ManagementObservationTarget:
    runtime_id: str
    graph_side: PlanGraphSide
    graph_digest: str
    relation_digest: str

    def __post_init__(self):
        _bounded_reference(self.runtime_id)
        if type(self.graph_side) is not PlanGraphSide:
            raise ManagementObservationError("management observation side is malformed")
        if any(type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None
               for value in (self.graph_digest, self.relation_digest)):
            raise ManagementObservationError("management observation digest is malformed")
        _bounded_wire(self.descriptor(), 512)

    def descriptor(self):
        return {"kind": "management", "runtime_id": self.runtime_id,
                "graph_side": self.graph_side.value, "graph_digest": self.graph_digest,
                "relation_digest": self.relation_digest}


@dataclass(frozen=True)
class ObserveManagementBootstrap:
    target: ManagementObservationTarget
    stage: ManagementBootstrapStage

    def __post_init__(self):
        if type(self.target) is not ManagementObservationTarget or type(self.stage) is not ManagementBootstrapStage:
            raise ManagementObservationError("management bootstrap request is malformed")
        self.target.__post_init__()
        _bounded_wire(self.descriptor(), 1024)

    def descriptor(self):
        return {"kind": "observe-management-bootstrap", "target": self.target.descriptor(), "stage": self.stage.value}


@dataclass(frozen=True)
class ObserveNodeHealth:
    target: ManagementObservationTarget
    node_id: str
    provider_socket_name: str
    health_kind: NodeHealthReadKind
    transport: NodeHealthObservationTransport = NodeHealthObservationTransport.RUNTIME_GATEWAY

    def __post_init__(self):
        if (type(self.target) is not ManagementObservationTarget
                or type(self.health_kind) is not NodeHealthReadKind
                or type(self.transport) is not NodeHealthObservationTransport):
            raise ManagementObservationError("node health observation request is malformed")
        self.target.__post_init__()
        _bounded_reference(self.node_id)
        _bounded_reference(self.provider_socket_name, socket=True)
        _bounded_wire(self.descriptor(), 1024)

    def descriptor(self):
        return {"kind": "observe-node-health", "target": self.target.descriptor(),
                "node_id": self.node_id, "provider_socket_name": self.provider_socket_name,
                "health_kind": self.health_kind.value, "transport": self.transport.value}


def _enum(enum_type, value):
    if type(value) is str:
        for member in enum_type:
            if member.value == value:
                return member
    raise ManagementObservationError("management observation variant is unknown")


def _keys(value, keys):
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise ManagementObservationError("management observation fields are malformed")


def _observation_from_descriptor(value):
    """Internal codec shared by the public operation and plan entrances."""
    _bounded_wire(value, 1024)
    if not isinstance(value, Mapping):
        raise ManagementObservationError("management observation must be an object")
    kind = value.get("kind")
    if kind == "observe-management-bootstrap":
        _keys(value, {"kind", "target", "stage"})
    elif kind == "observe-node-health":
        _keys(value, {"kind", "target", "node_id", "provider_socket_name", "health_kind", "transport"})
    else:
        raise ManagementObservationError("management observation variant is unknown")
    raw = value["target"]
    _keys(raw, {"kind", "runtime_id", "graph_side", "graph_digest", "relation_digest"})
    _bounded_wire(raw, 512)
    if raw["kind"] != "management":
        raise ManagementObservationError("management observation target is malformed")
    target = ManagementObservationTarget(raw["runtime_id"], _enum(PlanGraphSide, raw["graph_side"]),
                                         raw["graph_digest"], raw["relation_digest"])
    if kind == "observe-management-bootstrap":
        return ObserveManagementBootstrap(target, _enum(ManagementBootstrapStage, value["stage"]))
    return ObserveNodeHealth(target, value["node_id"], value["provider_socket_name"],
                             _enum(NodeHealthReadKind, value["health_kind"]),
                             _enum(NodeHealthObservationTransport, value["transport"]))
