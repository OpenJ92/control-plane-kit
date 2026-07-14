from unittest import TestCase, main

from control_plane_kit import (
    ControlValueKind,
    EnvironmentContract,
    ControlVariableError,
    ControlVariableSpec,
    HttpVariable,
    PostgresVariable,
    ReloadPolicy,
    RuntimeMapVariable,
    TextVariable,
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


class EnvironmentContractTests(TestCase):
    def test_from_mapping_loads_declared_values_by_variable_name(self):
        class ApiEnvironment(EnvironmentContract):
            storage_base_url = HttpVariable("storage_base_url")

        env = ApiEnvironment.from_mapping({"storage_base_url": "https://storage.internal"})

        self.assertEqual(env.get("storage_base_url"), "https://storage.internal")

    def test_from_mapping_loads_declared_values_by_env_metadata(self):
        class ApiEnvironment(EnvironmentContract):
            database_url = PostgresVariable("database_url", metadata={"env": "DATABASE_URL"})

        env = ApiEnvironment.from_mapping({"DATABASE_URL": "postgresql+psycopg://db:5432/app"})

        self.assertEqual(env.get("database_url"), "postgresql+psycopg://db:5432/app")

    def test_apply_patch_updates_holder_not_process_environment(self):
        class ApiEnvironment(EnvironmentContract):
            storage_base_url = HttpVariable("storage_base_url", metadata={"env": "STORAGE_BASE_URL"})

        env = ApiEnvironment.from_mapping({"STORAGE_BASE_URL": "https://storage-v1.internal"})
        result = env.apply_patch({"storage_base_url": "https://storage-v2.internal"})

        self.assertEqual(env.get("storage_base_url"), "https://storage-v2.internal")
        self.assertEqual(result.descriptor(), {"storage_base_url": "live"})
        self.assertNotEqual(__import__("os").environ.get("STORAGE_BASE_URL"), "https://storage-v2.internal")

    def test_immutable_value_rejects_patch(self):
        class ApiEnvironment(EnvironmentContract):
            name = TextVariable("name", mutable=False)

        env = ApiEnvironment.from_mapping({"name": "api-v1"})

        with self.assertRaises(ControlVariableError):
            env.apply_patch({"name": "api-v2"})

    def test_access_is_always_lookup(self):
        class ApiEnvironment(EnvironmentContract):
            message = TextVariable("message")

        env = ApiEnvironment.from_mapping({"message": "before"})
        first = env.get("message")
        env.set("message", "after")

        self.assertEqual(first, "before")
        self.assertEqual(env.get("message"), "after")

    def test_missing_required_value_fails_structurally(self):
        class ApiEnvironment(EnvironmentContract):
            database_url = PostgresVariable("database_url")

        with self.assertRaises(ControlVariableError) as raised:
            ApiEnvironment.from_mapping({})

        self.assertEqual(raised.exception.detail.code, "required")


class ContractDescriptorRedactionTests(TestCase):
    def test_contract_descriptor_redacts_secret_and_non_secret_values(self):
        class ApiEnvironment(EnvironmentContract):
            storage_base_url = HttpVariable("storage_base_url")
            sendgrid_key = SecretVariable("sendgrid_key")

        env = ApiEnvironment.from_mapping({
            "storage_base_url": "https://storage.internal",
            "sendgrid_key": "SG.secret",
        })

        descriptor = env.descriptor()

        self.assertEqual(descriptor["variables"]["storage_base_url"]["value"], {"present": True, "redacted": True})
        self.assertEqual(descriptor["variables"]["sendgrid_key"]["value"], {"present": True, "redacted": True})
        self.assertNotIn("https://storage.internal", str(descriptor))
        self.assertNotIn("SG.secret", str(descriptor))

    def test_unsafe_descriptor_is_explicit_and_still_redacts_secrets(self):
        class ApiEnvironment(EnvironmentContract):
            storage_base_url = HttpVariable("storage_base_url")
            sendgrid_key = SecretVariable("sendgrid_key")

        env = ApiEnvironment.from_mapping({
            "storage_base_url": "https://storage.internal",
            "sendgrid_key": "SG.secret",
        })

        descriptor = env.unsafe_descriptor()

        self.assertTrue(descriptor["unsafe"])
        self.assertEqual(descriptor["variables"]["storage_base_url"]["value"], "https://storage.internal")
        self.assertEqual(descriptor["variables"]["sendgrid_key"]["value"], {"present": True, "redacted": True})
        self.assertNotIn("SG.secret", str(descriptor))


class DerivedResourceTests(TestCase):
    def test_live_patch_rebuilds_and_disposes_derived_resource(self):
        disposed: list[str] = []

        class ApiEnvironment(EnvironmentContract):
            storage_base_url = HttpVariable("storage_base_url")

        env = ApiEnvironment.from_mapping({"storage_base_url": "https://storage-v1.internal"})
        first = env.derived(
            "storage_client",
            "storage_base_url",
            lambda contract: f"client:{contract.get('storage_base_url')}",
            dispose=disposed.append,
        )

        result = env.apply_patch({"storage_base_url": "https://storage-v2.internal"})

        self.assertEqual(first, "client:https://storage-v1.internal")
        self.assertEqual(env.get_derived("storage_client"), "client:https://storage-v2.internal")
        self.assertEqual(disposed, ["client:https://storage-v1.internal"])
        self.assertEqual(result.rebuilt_resources, ("storage_client",))
        self.assertFalse(env.is_derived_stale("storage_client"))

    def test_drain_required_patch_marks_derived_resource_stale(self):
        disposed: list[str] = []

        class ApiEnvironment(EnvironmentContract):
            database_url = PostgresVariable("database_url")

        env = ApiEnvironment.from_mapping({"database_url": "postgresql+psycopg://db-v1:5432/app"})
        env.derived(
            "database_engine",
            "database_url",
            lambda contract: f"engine:{contract.get('database_url')}",
            dispose=disposed.append,
        )

        result = env.apply_patch({"database_url": "postgresql+psycopg://db-v2:5432/app"})

        self.assertEqual(env.get_derived("database_engine"), "engine:postgresql+psycopg://db-v1:5432/app")
        self.assertEqual(disposed, [])
        self.assertEqual(result.stale_resources, ("database_engine",))
        self.assertTrue(env.is_derived_stale("database_engine"))

    def test_explicit_rebuild_refreshes_stale_resource(self):
        disposed: list[str] = []

        class ApiEnvironment(EnvironmentContract):
            database_url = PostgresVariable("database_url")

        env = ApiEnvironment.from_mapping({"database_url": "postgresql+psycopg://db-v1:5432/app"})
        env.derived(
            "database_engine",
            "database_url",
            lambda contract: f"engine:{contract.get('database_url')}",
            dispose=disposed.append,
        )
        env.apply_patch({"database_url": "postgresql+psycopg://db-v2:5432/app"})

        rebuilt = env.rebuild_derived("database_engine")

        self.assertEqual(rebuilt, "engine:postgresql+psycopg://db-v2:5432/app")
        self.assertEqual(disposed, ["engine:postgresql+psycopg://db-v1:5432/app"])
        self.assertFalse(env.is_derived_stale("database_engine"))

    def test_descriptor_reports_derived_resource_status_without_resource_value(self):
        class ApiEnvironment(EnvironmentContract):
            storage_base_url = HttpVariable("storage_base_url")

        env = ApiEnvironment.from_mapping({"storage_base_url": "https://storage.internal"})
        env.derived("storage_client", "storage_base_url", lambda contract: "resource-object")

        descriptor = env.descriptor()

        self.assertEqual(descriptor["derived_resources"]["storage_client"]["variables"], ["storage_base_url"])
        self.assertNotIn("resource-object", str(descriptor))


if __name__ == "__main__":
    main()
