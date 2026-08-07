import itertools
import unittest

import control_plane_kit_core as core
import control_plane_kit_core.node_control as node_control
from control_plane_kit_core.node_control import (
    ControlPlaneCommandCodec,
    ControlPlaneResultCodec,
    ControlPlaneStateCodec,
    ControlPlaneVariableDescriptor,
    ControlPlaneVariableKind,
    ControlPlaneVariableOperationContract,
    MapControlState,
    NodeControlContractError,
    NodeControlEvidence,
    NodeControlEvidenceCode,
    NodeControlOperation,
    NodeControlResultCodec,
    NodeControlResultStatus,
    ScalarControlState,
    WeightedRoutingControlState,
)


class NodeControlResultVariantTests(unittest.TestCase):
    def result_types(self):
        names = (
            "NodeControlReadStateSucceeded",
            "NodeControlTransitionSucceeded",
            "NodeControlRejected",
            "NodeControlFailed",
        )
        result_types = tuple(getattr(node_control, name, None) for name in names)
        self.assertNotIn(None, result_types, "nominal result variants are missing")
        return result_types

    def variable(
        self,
        kind: ControlPlaneVariableKind,
        state_codec: ControlPlaneStateCodec,
        command_codec: ControlPlaneCommandCodec,
    ) -> ControlPlaneVariableDescriptor:
        return ControlPlaneVariableDescriptor(
            variable_name=f"{kind.value}-variable",
            kind=kind,
            state_codec=state_codec,
            operation_contracts=(
                ControlPlaneVariableOperationContract(
                    operation=NodeControlOperation.READ_STATE,
                    command_codec=None,
                    result_codec=ControlPlaneResultCodec.STATE_V1,
                ),
                ControlPlaneVariableOperationContract(
                    operation=NodeControlOperation.APPLY_COMMAND,
                    command_codec=command_codec,
                    result_codec=ControlPlaneResultCodec.TRANSITION_V1,
                ),
            ),
        )

    def variables(self):
        return (
            (
                self.variable(
                    ControlPlaneVariableKind.SCALAR,
                    ControlPlaneStateCodec.SCALAR_V1,
                    ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
                ),
                ScalarControlState("target-a"),
            ),
            (
                self.variable(
                    ControlPlaneVariableKind.MAP,
                    ControlPlaneStateCodec.MAP_V1,
                    ControlPlaneCommandCodec.REPLACE_MAP_V1,
                ),
                MapControlState((("target-a", True),)),
            ),
            (
                self.variable(
                    ControlPlaneVariableKind.WEIGHTED_ROUTING,
                    ControlPlaneStateCodec.WEIGHTED_ROUTING_V1,
                    ControlPlaneCommandCodec.REPLACE_WEIGHTED_ROUTING_V1,
                ),
                WeightedRoutingControlState(
                    targets=("target-a", "target-b"),
                    weights=(("target-a", 2.0), ("target-b", 1.0)),
                ),
            ),
        )

    def test_all_valid_nominal_variants_round_trip_exact_wire(self) -> None:
        read_type, transition_type, rejected_type, failed_type = self.result_types()

        for variable, state in self.variables():
            with self.subTest(kind=variable.kind):
                result = read_type(
                    request_id="request-read-1",
                    state_codec=variable.state_codec,
                    version=4,
                    state=state,
                )
                expected = {
                    "request_id": "request-read-1",
                    "operation": "read-state",
                    "status": "succeeded",
                    "codec": "control.state.v1",
                    "state_codec": variable.state_codec.value,
                    "version": 4,
                    "state": state.descriptor(),
                }
                codec = NodeControlResultCodec(variable)
                self.assertEqual(codec.encode(result), expected)
                self.assertEqual(codec.decode(expected), result)

        variable, _ = self.variables()[2]
        valid = []
        for code in (
            NodeControlEvidenceCode.APPLIED,
            NodeControlEvidenceCode.NO_CHANGE,
        ):
            valid.append(
                transition_type(
                    request_id=f"request-{code.value}",
                    version=5,
                    evidence=NodeControlEvidence(code),
                )
            )
        valid.append(
            rejected_type(
                request_id="request-read-rejected",
                operation=NodeControlOperation.READ_STATE,
                evidence=NodeControlEvidence(
                    NodeControlEvidenceCode.NOT_AUTHORIZED
                ),
            )
        )
        for code in (
            NodeControlEvidenceCode.PRECONDITION_FAILED,
            NodeControlEvidenceCode.INVALID_COMMAND,
            NodeControlEvidenceCode.NOT_AUTHORIZED,
        ):
            valid.append(
                rejected_type(
                    request_id=f"request-{code.value}",
                    operation=NodeControlOperation.APPLY_COMMAND,
                    evidence=NodeControlEvidence(code),
                )
            )
        for operation in NodeControlOperation:
            valid.append(
                failed_type(
                    request_id=f"request-{operation.value}-failed",
                    operation=operation,
                )
            )

        codec = NodeControlResultCodec(variable)
        for result in valid:
            with self.subTest(result=result):
                encoded = codec.encode(result)
                self.assertEqual(codec.decode(encoded), result)
                self.assertEqual(
                    encoded["codec"],
                    variable.contract_for(result.operation).result_codec.value,
                )
                self.assertEqual(encoded["status"], result.status.value)
                self.assertIsInstance(encoded["evidence"], dict)

    def test_nominal_constructors_reject_local_contradictions(self) -> None:
        read_type, transition_type, rejected_type, failed_type = self.result_types()
        invalid_reads = (
            (ControlPlaneStateCodec.SCALAR_V1, MapControlState((("a", 1),))),
            (ControlPlaneStateCodec.MAP_V1, ScalarControlState("a")),
            (
                ControlPlaneStateCodec.WEIGHTED_ROUTING_V1,
                ScalarControlState("a"),
            ),
            ("control.scalar.v1", ScalarControlState("a")),
        )
        for state_codec, state in invalid_reads:
            with self.subTest(state_codec=state_codec, state=state):
                with self.assertRaises(NodeControlContractError):
                    read_type("request-1", state_codec, 4, state)

        for code in NodeControlEvidenceCode:
            if code in (
                NodeControlEvidenceCode.APPLIED,
                NodeControlEvidenceCode.NO_CHANGE,
            ):
                continue
            with self.subTest(transition_evidence=code):
                with self.assertRaises(NodeControlContractError):
                    transition_type(
                        "request-1",
                        4,
                        NodeControlEvidence(code),
                    )

        rejection_matrix = {
            NodeControlOperation.READ_STATE: {
                NodeControlEvidenceCode.NOT_AUTHORIZED,
            },
            NodeControlOperation.APPLY_COMMAND: {
                NodeControlEvidenceCode.PRECONDITION_FAILED,
                NodeControlEvidenceCode.INVALID_COMMAND,
                NodeControlEvidenceCode.NOT_AUTHORIZED,
            },
        }
        for operation, code in itertools.product(
            NodeControlOperation,
            NodeControlEvidenceCode,
        ):
            if code in rejection_matrix[operation]:
                continue
            with self.subTest(operation=operation, rejection_evidence=code):
                with self.assertRaises(NodeControlContractError):
                    rejected_type(
                        "request-1",
                        operation,
                        NodeControlEvidence(code),
                    )

        for factory in (
            lambda: transition_type("request-1", 4, ()),
            lambda: rejected_type(
                "request-1",
                "read-state",
                NodeControlEvidence(NodeControlEvidenceCode.NOT_AUTHORIZED),
            ),
            lambda: failed_type("request-1", "read-state"),
        ):
            with self.subTest(factory=factory):
                with self.assertRaises(NodeControlContractError):
                    factory()

    def matrix_descriptor(
        self,
        operation: NodeControlOperation,
        status: NodeControlResultStatus,
        evidence: NodeControlEvidenceCode,
    ) -> dict[str, object]:
        codec = (
            ControlPlaneResultCodec.STATE_V1
            if operation is NodeControlOperation.READ_STATE
            else ControlPlaneResultCodec.TRANSITION_V1
        )
        descriptor: dict[str, object] = {
            "request_id": "request-matrix-1",
            "operation": operation.value,
            "status": status.value,
            "codec": codec.value,
            "evidence": {"code": evidence.value},
        }
        if status is NodeControlResultStatus.SUCCEEDED:
            descriptor["version"] = 4
            if operation is NodeControlOperation.READ_STATE:
                descriptor["state_codec"] = ControlPlaneStateCodec.SCALAR_V1.value
                descriptor["state"] = ScalarControlState("target-a").descriptor()
        return descriptor

    def test_codec_enforces_complete_status_evidence_matrix(self) -> None:
        self.result_types()
        variable, _ = self.variables()[0]
        codec = NodeControlResultCodec(variable)
        valid = {
            (
                NodeControlOperation.APPLY_COMMAND,
                NodeControlResultStatus.SUCCEEDED,
                NodeControlEvidenceCode.APPLIED,
            ),
            (
                NodeControlOperation.APPLY_COMMAND,
                NodeControlResultStatus.SUCCEEDED,
                NodeControlEvidenceCode.NO_CHANGE,
            ),
            (
                NodeControlOperation.READ_STATE,
                NodeControlResultStatus.REJECTED,
                NodeControlEvidenceCode.NOT_AUTHORIZED,
            ),
            *(
                (
                    NodeControlOperation.APPLY_COMMAND,
                    NodeControlResultStatus.REJECTED,
                    code,
                )
                for code in (
                    NodeControlEvidenceCode.PRECONDITION_FAILED,
                    NodeControlEvidenceCode.INVALID_COMMAND,
                    NodeControlEvidenceCode.NOT_AUTHORIZED,
                )
            ),
            *(
                (
                    operation,
                    NodeControlResultStatus.FAILED,
                    NodeControlEvidenceCode.INTERNAL_FAILURE,
                )
                for operation in NodeControlOperation
            ),
        }
        for row in itertools.product(
            NodeControlOperation,
            NodeControlResultStatus,
            NodeControlEvidenceCode,
        ):
            descriptor = self.matrix_descriptor(*row)
            with self.subTest(row=row):
                if row in valid:
                    self.assertEqual(
                        codec.encode(codec.decode(descriptor)),
                        descriptor,
                    )
                else:
                    with self.assertRaises(NodeControlContractError):
                        codec.decode(descriptor)

    def test_codec_rejects_stale_and_cross_variant_material(self) -> None:
        read_type, transition_type, rejected_type, failed_type = self.result_types()
        variable, state = self.variables()[0]
        codec = NodeControlResultCodec(variable)
        variants = (
            codec.encode(
                read_type(
                    "request-read-1",
                    variable.state_codec,
                    4,
                    state,
                )
            ),
            codec.encode(
                transition_type(
                    "request-apply-1",
                    5,
                    NodeControlEvidence(NodeControlEvidenceCode.APPLIED),
                )
            ),
            codec.encode(
                rejected_type(
                    "request-rejected-1",
                    NodeControlOperation.APPLY_COMMAND,
                    NodeControlEvidence(NodeControlEvidenceCode.INVALID_COMMAND),
                )
            ),
            codec.encode(
                failed_type(
                    "request-failed-1",
                    NodeControlOperation.READ_STATE,
                )
            ),
        )
        forbidden = (
            ("version", 9),
            ("state_codec", ControlPlaneStateCodec.SCALAR_V1.value),
            ("state", ScalarControlState("stale-visible").descriptor()),
            ("payload", ScalarControlState("stale-visible").descriptor()),
        )
        for descriptor in variants[2:]:
            for key, value in forbidden:
                with self.subTest(status=descriptor["status"], key=key):
                    with self.assertRaises(NodeControlContractError):
                        codec.decode({**descriptor, key: value})

        for descriptor, extra in (
            (
                variants[0],
                {"evidence": {"code": NodeControlEvidenceCode.APPLIED.value}},
            ),
            (
                variants[1],
                {
                    "state_codec": ControlPlaneStateCodec.SCALAR_V1.value,
                    "state": ScalarControlState("target-a").descriptor(),
                },
            ),
        ):
            with self.subTest(status=descriptor["status"], extra=extra):
                with self.assertRaises(NodeControlContractError):
                    codec.decode({**descriptor, **extra})

    def test_codec_is_strict_and_bound_to_variable_descriptor(self) -> None:
        read_type, transition_type, _, _ = self.result_types()
        scalar_variable, scalar_state = self.variables()[0]
        map_variable, map_state = self.variables()[1]
        scalar_codec = NodeControlResultCodec(scalar_variable)
        map_codec = NodeControlResultCodec(map_variable)
        scalar_result = read_type(
            "request-read-1",
            ControlPlaneStateCodec.SCALAR_V1,
            4,
            scalar_state,
        )
        map_result = read_type(
            "request-read-2",
            ControlPlaneStateCodec.MAP_V1,
            4,
            map_state,
        )
        with self.assertRaises(NodeControlContractError):
            scalar_codec.encode(map_result)
        with self.assertRaises(NodeControlContractError):
            scalar_codec.decode(map_codec.encode(map_result))

        descriptors = (
            scalar_codec.encode(scalar_result),
            scalar_codec.encode(
                transition_type(
                    "request-apply-1",
                    5,
                    NodeControlEvidence(NodeControlEvidenceCode.NO_CHANGE),
                )
            ),
        )
        invalid = []
        for descriptor in descriptors:
            for key in descriptor:
                invalid.append(
                    {
                        candidate: value
                        for candidate, value in descriptor.items()
                        if candidate != key
                    }
                )
            invalid.append({**descriptor, "diagnostic": "provider-secret"})
        invalid.extend(
            (
                {**descriptors[0], "operation": "unknown"},
                {**descriptors[0], "status": "unknown"},
                {**descriptors[0], "codec": "unknown"},
                {**descriptors[0], "state_codec": "unknown"},
                {
                    **descriptors[1],
                    "evidence": [{"code": "no-change"}],
                },
                {
                    **descriptors[1],
                    "evidence": {"code": "unknown"},
                },
                {
                    "request_id": "request-legacy-1",
                    "status": "failed",
                    "codec": "control.transition.v1",
                    "version": 4,
                    "payload": None,
                    "evidence": [{"code": "internal-failure"}],
                },
            )
        )
        for descriptor in invalid:
            with self.subTest(descriptor=descriptor):
                with self.assertRaises(NodeControlContractError):
                    scalar_codec.decode(descriptor)

        with self.assertRaises(NodeControlContractError):
            NodeControlResultCodec("scalar-variable")

    def test_root_exports_expose_nominal_result_sum(self) -> None:
        result_types = self.result_types()
        for result_type in result_types:
            self.assertIs(getattr(core, result_type.__name__, None), result_type)
        self.assertEqual(core.MAX_NODE_CONTROL_EVIDENCE_ITEMS, 1)
        self.assertIsNotNone(getattr(node_control, "NodeControlResult", None))


if __name__ == "__main__":
    unittest.main()
