from unittest import TestCase, main

from control_plane_kit import (
    ControlValueKind,
    ControlVariableError,
    ControlVariableSpec,
    HttpVariable,
    PostgresVariable,
    ReloadPolicy,
    RuntimeMapVariable,
    SecretVariable,
    TcpVariable,
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


class ConcreteControlVariableTests(TestCase):
    def test_protocol_variables_validate_shape(self):
        self.assertEqual(HttpVariable("api").validate("https://api.internal"), "https://api.internal")
        self.assertEqual(TcpVariable("redis").validate("redis:6379"), "redis:6379")
        self.assertEqual(
            PostgresVariable("database").validate("postgresql+psycopg://db:5432/app"),
            "postgresql+psycopg://db:5432/app",
        )

    def test_protocol_variables_reject_invalid_shape(self):
        with self.assertRaises(ControlVariableError):
            HttpVariable("api").validate("ftp://api.internal")
        with self.assertRaises(ControlVariableError):
            TcpVariable("redis").validate("redis")
        with self.assertRaises(ControlVariableError):
            PostgresVariable("database").validate("mysql://db/app")

    def test_secret_descriptor_never_exposes_raw_value(self):
        variable = SecretVariable("sendgrid_key")

        descriptor = variable.descriptor("SG.secret", include_value=True)

        self.assertEqual(descriptor["value"], {"present": True, "redacted": True})
        self.assertNotIn("SG.secret", str(descriptor))

    def test_secret_descriptor_reports_missing_without_value(self):
        variable = SecretVariable("sendgrid_key", required=False)

        self.assertEqual(variable.descriptor(None, include_value=True)["value"], {"present": False, "redacted": True})

    def test_runtime_map_variable_requires_mapping(self):
        variable = RuntimeMapVariable("targets")

        self.assertEqual(variable.validate({"v1": "http://api-v1"}), {"v1": "http://api-v1"})
        with self.assertRaises(ControlVariableError):
            variable.validate("http://api-v1")


if __name__ == "__main__":
    main()
