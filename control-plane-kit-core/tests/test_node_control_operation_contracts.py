import unittest

import control_plane_kit_core as core
import control_plane_kit_core.node_control as node_control
from control_plane_kit_core.node_control import (
    ControlPlaneCommandCodec,
    ControlPlaneResultCodec,
    ControlPlaneStateCodec,
    ControlPlaneVariableDescriptor,
    ControlPlaneVariableDescriptorCodec,
    ControlPlaneVariableKind,
    NodeControlContractError,
    NodeControlOperation,
)


class NodeControlOperationContractTests(unittest.TestCase):
    def operation_contract_type(self):
        contract_type = getattr(
            node_control,
            "ControlPlaneVariableOperationContract",
            None,
        )
        self.assertIsNotNone(
            contract_type,
            "ControlPlaneVariableOperationContract is not implemented",
        )
        return contract_type

    def contracts(self, command_codec: ControlPlaneCommandCodec):
        contract_type = self.operation_contract_type()
        return (
            contract_type(
                operation=NodeControlOperation.READ_STATE,
                command_codec=None,
                result_codec=ControlPlaneResultCodec.STATE_V1,
            ),
            contract_type(
                operation=NodeControlOperation.APPLY_COMMAND,
                command_codec=command_codec,
                result_codec=ControlPlaneResultCodec.TRANSITION_V1,
            ),
        )

    def variable(
        self,
        *,
        kind: ControlPlaneVariableKind = ControlPlaneVariableKind.WEIGHTED_ROUTING,
        state_codec: ControlPlaneStateCodec = (
            ControlPlaneStateCodec.WEIGHTED_ROUTING_V1
        ),
        command_codec: ControlPlaneCommandCodec = (
            ControlPlaneCommandCodec.REPLACE_WEIGHTED_ROUTING_V1
        ),
        operation_contracts=None,
    ) -> ControlPlaneVariableDescriptor:
        return ControlPlaneVariableDescriptor(
            variable_name="routing",
            kind=kind,
            state_codec=state_codec,
            operation_contracts=(
                self.contracts(command_codec)
                if operation_contracts is None
                else operation_contracts
            ),
            description="Atomic graph target weights.",
        )

    def test_all_variable_kinds_round_trip_exact_operation_contracts(self) -> None:
        cases = (
            (
                ControlPlaneVariableKind.SCALAR,
                ControlPlaneStateCodec.SCALAR_V1,
                ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
            ),
            (
                ControlPlaneVariableKind.MAP,
                ControlPlaneStateCodec.MAP_V1,
                ControlPlaneCommandCodec.REPLACE_MAP_V1,
            ),
            (
                ControlPlaneVariableKind.WEIGHTED_ROUTING,
                ControlPlaneStateCodec.WEIGHTED_ROUTING_V1,
                ControlPlaneCommandCodec.REPLACE_WEIGHTED_ROUTING_V1,
            ),
        )
        codec = ControlPlaneVariableDescriptorCodec()

        for kind, state_codec, command_codec in cases:
            with self.subTest(kind=kind):
                variable = self.variable(
                    kind=kind,
                    state_codec=state_codec,
                    command_codec=command_codec,
                )
                encoded = codec.encode(variable)

                self.assertEqual(
                    encoded["operation_contracts"],
                    [
                        {
                            "operation": "read-state",
                            "command_codec": None,
                            "result_codec": "control.state.v1",
                        },
                        {
                            "operation": "apply-command",
                            "command_codec": command_codec.value,
                            "result_codec": "control.transition.v1",
                        },
                    ],
                )
                self.assertNotIn("command_codec", encoded)
                self.assertNotIn("result_codec", encoded)
                self.assertEqual(codec.decode(encoded), variable)
                self.assertEqual(
                    variable.contract_for(NodeControlOperation.READ_STATE),
                    variable.operation_contracts[0],
                )
                self.assertEqual(
                    variable.contract_for(NodeControlOperation.APPLY_COMMAND),
                    variable.operation_contracts[1],
                )

    def test_operation_contracts_enforce_operation_local_laws(self) -> None:
        contract_type = self.operation_contract_type()
        invalid = (
            {
                "operation": "read-state",
                "command_codec": None,
                "result_codec": ControlPlaneResultCodec.STATE_V1,
            },
            {
                "operation": NodeControlOperation.READ_STATE,
                "command_codec": ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
                "result_codec": ControlPlaneResultCodec.STATE_V1,
            },
            {
                "operation": NodeControlOperation.READ_STATE,
                "command_codec": None,
                "result_codec": ControlPlaneResultCodec.TRANSITION_V1,
            },
            {
                "operation": NodeControlOperation.APPLY_COMMAND,
                "command_codec": None,
                "result_codec": ControlPlaneResultCodec.TRANSITION_V1,
            },
            {
                "operation": NodeControlOperation.APPLY_COMMAND,
                "command_codec": ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
                "result_codec": ControlPlaneResultCodec.STATE_V1,
            },
            {
                "operation": NodeControlOperation.APPLY_COMMAND,
                "command_codec": "control.replace-scalar.v1",
                "result_codec": ControlPlaneResultCodec.TRANSITION_V1,
            },
            {
                "operation": NodeControlOperation.APPLY_COMMAND,
                "command_codec": ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
                "result_codec": "control.transition.v1",
            },
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(NodeControlContractError):
                    contract_type(**values)

    def test_descriptor_requires_total_canonical_operation_index(self) -> None:
        read, apply = self.contracts(
            ControlPlaneCommandCodec.REPLACE_WEIGHTED_ROUTING_V1
        )
        invalid = (
            (),
            (read,),
            (apply,),
            (read, read),
            (apply, apply),
            (apply, read),
            (read, apply, apply),
            [read, apply],
        )
        for operation_contracts in invalid:
            with self.subTest(operation_contracts=operation_contracts):
                with self.assertRaises(NodeControlContractError):
                    self.variable(operation_contracts=operation_contracts)

        variable = self.variable()
        for operation in ("read-state", None, object()):
            with self.subTest(operation=operation):
                with self.assertRaises(NodeControlContractError):
                    variable.contract_for(operation)

    def test_descriptor_rejects_cross_kind_apply_command_codec(self) -> None:
        invalid = (
            (
                ControlPlaneVariableKind.SCALAR,
                ControlPlaneStateCodec.SCALAR_V1,
                ControlPlaneCommandCodec.REPLACE_MAP_V1,
            ),
            (
                ControlPlaneVariableKind.MAP,
                ControlPlaneStateCodec.SCALAR_V1,
                ControlPlaneCommandCodec.REPLACE_MAP_V1,
            ),
            (
                ControlPlaneVariableKind.WEIGHTED_ROUTING,
                ControlPlaneStateCodec.WEIGHTED_ROUTING_V1,
                ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
            ),
        )
        for kind, state_codec, command_codec in invalid:
            with self.subTest(kind=kind, command_codec=command_codec):
                with self.assertRaises(NodeControlContractError):
                    self.variable(
                        kind=kind,
                        state_codec=state_codec,
                        command_codec=command_codec,
                    )

    def test_descriptor_codec_is_strict_for_nested_and_legacy_shapes(self) -> None:
        codec = ControlPlaneVariableDescriptorCodec()
        descriptor = codec.encode(self.variable())
        contracts = descriptor["operation_contracts"]
        self.assertIsInstance(contracts, list)

        legacy = {
            key: value
            for key, value in descriptor.items()
            if key != "operation_contracts"
        }
        legacy.update(
            {
                "command_codec": "control.replace-weighted-routing.v1",
                "result_codec": "control.transition.v1",
            }
        )
        nested_cases = (
            {**descriptor, "operation_contracts": tuple(contracts)},
            {**descriptor, "operation_contracts": contracts[:1]},
            {**descriptor, "operation_contracts": list(reversed(contracts))},
            {
                **descriptor,
                "operation_contracts": [
                    {**contracts[0], "authority": "secret"},
                    contracts[1],
                ],
            },
            {
                **descriptor,
                "operation_contracts": [
                    {
                        key: value
                        for key, value in contracts[0].items()
                        if key != "result_codec"
                    },
                    contracts[1],
                ],
            },
            {
                **descriptor,
                "operation_contracts": [
                    contracts[0],
                    {**contracts[1], "operation": "arbitrary-http"},
                ],
            },
            {
                **descriptor,
                "operation_contracts": [
                    contracts[0],
                    {**contracts[1], "command_codec": "arbitrary-http.v1"},
                ],
            },
            {
                **descriptor,
                "operation_contracts": [
                    contracts[0],
                    {**contracts[1], "result_codec": "arbitrary-result.v1"},
                ],
            },
            legacy,
        )
        for value in nested_cases:
            with self.subTest(value=value):
                with self.assertRaises(NodeControlContractError):
                    codec.decode(value)

        self.assertIs(
            getattr(core, "ControlPlaneVariableOperationContract", None),
            self.operation_contract_type(),
        )


if __name__ == "__main__":
    unittest.main()
