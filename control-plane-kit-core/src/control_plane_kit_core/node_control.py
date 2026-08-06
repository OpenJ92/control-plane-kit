"""Pure authenticated workload node-control contract language."""

from enum import StrEnum


class NodeControlContractError(ValueError):
    """Raised when workload node-control contract material is malformed."""


class NodeControlOperation(StrEnum):
    READ_STATE = "read-state"
    APPLY_COMMAND = "apply-command"


class ControlPlaneVariableKind(StrEnum):
    SCALAR = "scalar"
    MAP = "map"
    WEIGHTED_ROUTING = "weighted-routing"


class ControlPlaneStateCodec(StrEnum):
    SCALAR_V1 = "control.scalar.v1"
    MAP_V1 = "control.map.v1"
    WEIGHTED_ROUTING_V1 = "control.weighted-routing.v1"


class ControlPlaneCommandCodec(StrEnum):
    REPLACE_SCALAR_V1 = "control.replace-scalar.v1"
    REPLACE_MAP_V1 = "control.replace-map.v1"
    REPLACE_WEIGHTED_ROUTING_V1 = "control.replace-weighted-routing.v1"


class ControlPlaneResultCodec(StrEnum):
    STATE_V1 = "control.state.v1"
    TRANSITION_V1 = "control.transition.v1"


class NodeControlResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"


class NodeControlEvidenceCode(StrEnum):
    APPLIED = "applied"
    NO_CHANGE = "no-change"
    PRECONDITION_FAILED = "precondition-failed"
    INVALID_COMMAND = "invalid-command"
    NOT_AUTHORIZED = "not-authorized"
    INTERNAL_FAILURE = "internal-failure"


class WorkloadNodeControlGrantVerificationCode(StrEnum):
    GRANT_TYPE_MISMATCH = "grant-type-mismatch"
    ISSUER_MISMATCH = "issuer-mismatch"
    AUDIENCE_MISMATCH = "audience-mismatch"
    WORKSPACE_MISMATCH = "workspace-mismatch"
    REVISION_MISMATCH = "revision-mismatch"
    NODE_MISMATCH = "node-mismatch"
    SOCKET_MISMATCH = "socket-mismatch"
    VARIABLE_MISMATCH = "variable-mismatch"
    COMMAND_MISMATCH = "command-mismatch"
    REQUEST_MISMATCH = "request-mismatch"
    TEMPORALLY_INVALID = "temporally-invalid"


class NodeControlTarget:
    pass


class ControlPlaneTransitionPrecondition:
    pass


class ScalarControlState:
    pass


class MapControlState:
    pass


class WeightedRoutingControlState:
    pass


class NodeControlPayload:
    pass


class NodeControlRequestDigest:
    pass


class NodeControlCommandRequest:
    pass


class NodeControlCommandRequestCodec:
    pass


class DelegatedWorkloadNodeControlGrant:
    pass


class DelegatedWorkloadNodeControlGrantCodec:
    pass


class WorkloadNodeControlGrantVerificationResult:
    pass


class NodeControlEvidence:
    pass


class NodeControlResult:
    pass


class NodeControlResultCodec:
    pass


class ControlPlaneVariableDescriptor:
    pass


class ControlPlaneVariableDescriptorCodec:
    pass


def verify_workload_node_control_grant(*args: object, **kwargs: object) -> object:
    raise NotImplementedError
