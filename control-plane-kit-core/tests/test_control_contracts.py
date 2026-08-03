from __future__ import annotations

import unittest

from control_plane_kit_core.control_contracts import (
    ControlContract,
    ControlContractError,
    ControlValueKind,
    ControlVariableSpec,
    ReloadPolicy,
)


class ControlVariableSpecTests(unittest.TestCase):
    def test_variable_descriptor_is_json_friendly(self) -> None:
        variable = ControlVariableSpec(
            name="storage_base_url",
            kind=ControlValueKind.HTTP,
            mutable=True,
            required=True,
            reload_policy=ReloadPolicy.LIVE,
            description="Storage service URL",
            metadata={"env": "STORAGE_BASE_URL"},
        )

        self.assertEqual(
            variable.descriptor(),
            {
                "name": "storage_base_url",
                "kind": "http",
                "mutable": True,
                "required": True,
                "reload_policy": "live",
                "metadata": {"env": "STORAGE_BASE_URL"},
                "description": "Storage service URL",
            },
        )

    def test_required_optional_and_value_inclusion_laws_are_explicit(self) -> None:
        required = ControlVariableSpec("database_url", ControlValueKind.POSTGRES)
        optional = ControlVariableSpec("note", ControlValueKind.TEXT, required=False)
        message = ControlVariableSpec("message", ControlValueKind.TEXT)

        with self.assertRaises(ControlContractError) as raised:
            required.validate(None)
        self.assertEqual(
            raised.exception.detail.descriptor(),
            {
                "variable": "database_url",
                "code": "required",
                "message": "database_url is required",
            },
        )

        self.assertIsNone(optional.validate(None))
        self.assertNotIn("value", message.descriptor("hello"))
        self.assertEqual(
            message.descriptor("hello", include_value=True)["value"],
            "hello",
        )

    def test_protocol_variables_validate_through_closed_protocol_language(self) -> None:
        cases = (
            (
                ControlVariableSpec("api", ControlValueKind.HTTP),
                "https://api.internal",
            ),
            (
                ControlVariableSpec("redis", ControlValueKind.TCP),
                "redis:6379",
            ),
            (
                ControlVariableSpec("database", ControlValueKind.POSTGRES),
                "postgresql+psycopg://db:5432/app",
            ),
        )

        for variable, value in cases:
            with self.subTest(variable=variable.name):
                self.assertEqual(variable.validate(value), value)

        invalid = (
            (ControlVariableSpec("api", ControlValueKind.HTTP), "ftp://api.internal"),
            (ControlVariableSpec("redis", ControlValueKind.TCP), "redis"),
            (ControlVariableSpec("database", ControlValueKind.POSTGRES), "mysql://db/app"),
        )
        for variable, value in invalid:
            with self.subTest(variable=variable.name), self.assertRaises(ControlContractError):
                variable.validate(value)

    def test_secret_descriptors_never_expose_raw_values(self) -> None:
        required = ControlVariableSpec("sendgrid_key", ControlValueKind.SECRET)
        optional = ControlVariableSpec(
            "optional_key",
            ControlValueKind.SECRET,
            required=False,
        )

        descriptor = required.descriptor("SG.secret", include_value=True)

        self.assertEqual(descriptor["value"], {"present": True, "redacted": True})
        self.assertNotIn("SG.secret", str(descriptor))
        self.assertEqual(
            optional.descriptor(None, include_value=True)["value"],
            {"present": False, "redacted": True},
        )

    def test_runtime_map_variable_requires_mapping(self) -> None:
        variable = ControlVariableSpec("targets", ControlValueKind.RUNTIME_MAP)

        self.assertEqual(
            variable.validate({"v1": "http://api-v1"}),
            {"v1": "http://api-v1"},
        )
        with self.assertRaises(ControlContractError):
            variable.validate("http://api-v1")


class ControlContractTests(unittest.TestCase):
    def test_contract_loads_explicit_values_by_name_or_metadata_without_process_reads(self) -> None:
        contract = ControlContract(
            (
                ControlVariableSpec("storage_base_url", ControlValueKind.HTTP),
                ControlVariableSpec(
                    "database_url",
                    ControlValueKind.POSTGRES,
                    metadata={"env": "DATABASE_URL"},
                ),
            )
        )

        snapshot = contract.load(
            {
                "storage_base_url": "https://storage.internal",
                "DATABASE_URL": "postgresql+psycopg://db:5432/app",
            }
        )

        self.assertEqual(snapshot.get("storage_base_url"), "https://storage.internal")
        self.assertEqual(
            snapshot.get("database_url"),
            "postgresql+psycopg://db:5432/app",
        )
        with self.assertRaises(TypeError):
            contract.load_from_process()

    def test_missing_required_and_immutable_patch_are_structural_pure_failures(self) -> None:
        contract = ControlContract(
            (
                ControlVariableSpec("storage_base_url", ControlValueKind.HTTP),
                ControlVariableSpec(
                    "immutable_name",
                    ControlValueKind.TEXT,
                    mutable=False,
                ),
            )
        )

        with self.assertRaises(ControlContractError) as missing:
            contract.load({"immutable_name": "api"})
        self.assertEqual(missing.exception.detail.code, "required")

        snapshot = contract.load(
            {
                "storage_base_url": "https://storage.internal",
                "immutable_name": "api",
            }
        )
        with self.assertRaises(ControlContractError) as immutable:
            snapshot.prepare_patch({"immutable_name": "renamed"})
        self.assertEqual(immutable.exception.detail.code, "immutable")

    def test_contract_descriptors_redact_by_default_and_unsafe_mode_is_explicit(self) -> None:
        contract = ControlContract(
            (
                ControlVariableSpec("storage_base_url", ControlValueKind.HTTP),
                ControlVariableSpec("sendgrid_key", ControlValueKind.SECRET),
            )
        )
        snapshot = contract.load(
            {
                "storage_base_url": "https://storage.internal",
                "sendgrid_key": "SG.secret",
            }
        )

        descriptor = snapshot.descriptor()

        self.assertFalse(descriptor["runtime"])
        self.assertEqual(
            descriptor["variables"]["storage_base_url"]["value"],
            {"present": True, "redacted": True},
        )
        self.assertEqual(
            descriptor["variables"]["sendgrid_key"]["value"],
            {"present": True, "redacted": True},
        )
        self.assertNotIn("https://storage.internal", str(descriptor))
        self.assertNotIn("SG.secret", str(descriptor))

        unsafe = snapshot.unsafe_descriptor()
        self.assertTrue(unsafe["unsafe"])
        self.assertEqual(
            unsafe["variables"]["storage_base_url"]["value"],
            "https://storage.internal",
        )
        self.assertEqual(
            unsafe["variables"]["sendgrid_key"]["value"],
            {"present": True, "redacted": True},
        )
        self.assertNotIn("SG.secret", str(unsafe))

    def test_runtime_contract_loads_explicit_state_and_redacts_descriptors(self) -> None:
        contract = ControlContract(
            (
                ControlVariableSpec("active_target", ControlValueKind.RUNTIME_VALUE),
                ControlVariableSpec("targets", ControlValueKind.RUNTIME_MAP),
            ),
            runtime=True,
        )
        state = contract.load(
            {
                "active_target": "http://private-target",
                "targets": {"v1": "http://api-v1"},
            }
        )

        self.assertEqual(state.get("active_target"), "http://private-target")
        self.assertEqual(state.get("targets"), {"v1": "http://api-v1"})

        descriptor = state.descriptor()
        self.assertTrue(descriptor["runtime"])
        self.assertEqual(
            descriptor["variables"]["active_target"]["value"],
            {"present": True, "redacted": True},
        )
        self.assertNotIn("http://private-target", str(descriptor))


if __name__ == "__main__":
    unittest.main()
