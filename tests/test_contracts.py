from unittest import TestCase, main

from control_plane_kit import (
    ControlValueKind,
    ControlVariableError,
    ControlVariableSpec,
    ReloadPolicy,
)


class ControlVariableProtocolTests(TestCase):
    def test_variable_descriptor_is_json_friendly(self):
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

    def test_required_value_validation_fails_structurally(self):
        variable = ControlVariableSpec("database_url", ControlValueKind.POSTGRES)

        with self.assertRaises(ControlVariableError) as raised:
            variable.validate(None)

        self.assertEqual(
            raised.exception.detail.descriptor(),
            {
                "variable": "database_url",
                "code": "required",
                "message": "database_url is required",
            },
        )

    def test_optional_value_allows_none(self):
        variable = ControlVariableSpec("note", ControlValueKind.TEXT, required=False)

        self.assertIsNone(variable.validate(None))

    def test_descriptor_value_is_opt_in(self):
        variable = ControlVariableSpec("message", ControlValueKind.TEXT)

        self.assertNotIn("value", variable.descriptor("hello"))
        self.assertEqual(variable.descriptor("hello", include_value=True)["value"], "hello")


if __name__ == "__main__":
    main()
