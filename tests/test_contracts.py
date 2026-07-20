from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import TestCase, main

from control_plane_kit import (
    ConflictingContractMutation,
    ContractCleanupUncertainty,
    ContractMutation,
    ContractMutationInProgress,
    ContractPreparationError,
    ContractPublicationConflict,
    ControlValueKind,
    EnvironmentContract,
    ControlVariableError,
    ControlVariableSpec,
    HttpVariable,
    PostgresVariable,
    ReloadPolicy,
    RuntimeContract,
    RuntimeMapVariable,
    RuntimeValueVariable,
    TextVariable,
    SecretVariable,
    StaleContractVersion,
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
        self.assertEqual(
            result.descriptor(),
            {
                "mutation_id": "local-1",
                "base_version": 0,
                "version": 1,
                "changed": {"storage_base_url": "live"},
            },
        )
        self.assertNotEqual(__import__("os").environ.get("STORAGE_BASE_URL"), "https://storage-v2.internal")

    def test_mutation_prepares_immutable_candidate_without_publication(self):
        class RouterState(EnvironmentContract):
            active_target = TextVariable("active_target")
            targets = RuntimeMapVariable("targets")

        original_targets = {"v1": "http://api-v1"}
        state = RouterState.from_mapping(
            {"active_target": "v1", "targets": original_targets}
        )
        mutation = ContractMutation(
            "route-v2",
            expected_version=0,
            assignments={
                "active_target": "v2",
                "targets": {"v2": "http://api-v2"},
            },
        )

        candidate = state.prepare_mutation(mutation)
        original_targets["v1"] = "http://changed-after-construction"

        self.assertEqual(state.version, 0)
        self.assertEqual(state.get("active_target"), "v1")
        self.assertEqual(candidate.base_version, 0)
        self.assertEqual(candidate.version, 1)
        self.assertEqual(candidate.get("active_target"), "v2")
        self.assertEqual(candidate.get("targets"), {"v2": "http://api-v2"})
        with self.assertRaises(TypeError):
            candidate.get("targets")["v3"] = "http://api-v3"

    def test_mutation_rejects_stale_version_before_preparation(self):
        class ApiEnvironment(EnvironmentContract):
            storage_base_url = HttpVariable("storage_base_url")

        env = ApiEnvironment.from_mapping(
            {"storage_base_url": "https://storage-v1.internal"}
        )
        env.apply_patch({"storage_base_url": "https://storage-v2.internal"})

        with self.assertRaises(StaleContractVersion):
            env.prepare_mutation(
                ContractMutation(
                    "stale",
                    expected_version=0,
                    assignments={
                        "storage_base_url": "https://storage-v3.internal"
                    },
                )
            )

        self.assertEqual(env.version, 1)
        self.assertEqual(
            env.get("storage_base_url"), "https://storage-v2.internal"
        )

    def test_preparation_validates_all_assignments_before_any_publication(self):
        class ApiEnvironment(EnvironmentContract):
            storage_base_url = HttpVariable("storage_base_url")
            database_url = PostgresVariable("database_url")

        env = ApiEnvironment.from_mapping(
            {
                "storage_base_url": "https://storage-v1.internal",
                "database_url": "postgresql://db-v1/app",
            }
        )

        with self.assertRaises(ControlVariableError):
            env.prepare_mutation(
                ContractMutation(
                    "invalid-database",
                    expected_version=0,
                    assignments={
                        "storage_base_url": "https://storage-v2.internal",
                        "database_url": "not-postgres",
                    },
                )
            )

        self.assertEqual(env.version, 0)
        self.assertEqual(
            env.get("storage_base_url"), "https://storage-v1.internal"
        )

    def test_prepared_mutation_identity_replays_or_rejects_conflicting_intent(self):
        class ApiEnvironment(EnvironmentContract):
            storage_base_url = HttpVariable("storage_base_url")

        env = ApiEnvironment.from_mapping(
            {"storage_base_url": "https://storage-v1.internal"}
        )
        mutation = ContractMutation(
            "storage-cutover",
            expected_version=0,
            assignments={"storage_base_url": "https://storage-v2.internal"},
        )

        first = env.prepare_mutation(mutation)
        replay = env.prepare_mutation(mutation)

        self.assertIs(replay, first)
        with self.assertRaises(ConflictingContractMutation):
            env.prepare_mutation(
                ContractMutation(
                    "storage-cutover",
                    expected_version=0,
                    assignments={
                        "storage_base_url": "https://storage-v3.internal"
                    },
                )
            )
        self.assertEqual(
            env.get("storage_base_url"), "https://storage-v1.internal"
        )

    def test_noop_candidate_does_not_advance_version(self):
        class ApiEnvironment(EnvironmentContract):
            storage_base_url = HttpVariable("storage_base_url")

        env = ApiEnvironment.from_mapping(
            {"storage_base_url": "https://storage.internal"}
        )
        candidate = env.prepare_mutation(
            ContractMutation(
                "same-value",
                expected_version=0,
                assignments={"storage_base_url": "https://storage.internal"},
            )
        )

        self.assertEqual(candidate.version, 0)
        self.assertEqual(candidate.changed, {})

    def test_published_mutation_identity_replays_without_reexecution(self):
        class ApiEnvironment(EnvironmentContract):
            storage_base_url = HttpVariable("storage_base_url")

        env = ApiEnvironment.from_mapping(
            {"storage_base_url": "https://storage-v1.internal"}
        )
        mutation = ContractMutation(
            "storage-cutover",
            expected_version=0,
            assignments={"storage_base_url": "https://storage-v2.internal"},
        )

        first = env.apply_mutation(mutation)
        replay = env.apply_mutation(mutation)

        self.assertIs(replay, first)
        self.assertEqual(env.version, 1)

    def test_mutation_and_candidate_descriptors_never_publish_values(self):
        class ApiEnvironment(EnvironmentContract):
            sendgrid_key = SecretVariable("sendgrid_key")

        env = ApiEnvironment.from_mapping({"sendgrid_key": "SG.old-secret"})
        mutation = ContractMutation(
            "rotate-sendgrid",
            expected_version=0,
            assignments={"sendgrid_key": "SG.new-secret"},
        )
        candidate = env.prepare_mutation(mutation)

        evidence = (
            f"{mutation!r} {mutation.descriptor()} "
            f"{candidate!r} {candidate.descriptor()}"
        )
        self.assertNotIn("SG.old-secret", evidence)
        self.assertNotIn("SG.new-secret", evidence)
        self.assertEqual(
            candidate.descriptor(),
            {
                "mutation_id": "rotate-sendgrid",
                "base_version": 0,
                "version": 1,
                "changed": {"sendgrid_key": "live"},
            },
        )

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

    def test_multi_resource_mutation_prepares_against_candidate_then_publishes_once(self):
        built: list[tuple[str, str]] = []
        disposed: list[str] = []

        class ApiEnvironment(EnvironmentContract):
            storage_base_url = HttpVariable("storage_base_url")

        env = ApiEnvironment.from_mapping(
            {"storage_base_url": "https://storage-v1.internal"}
        )

        def build(name):
            def builder(values):
                candidate_url = values.get("storage_base_url")
                built.append((name, candidate_url))
                self.assertEqual(
                    env.get("storage_base_url"),
                    "https://storage-v1.internal",
                )
                return f"{name}:{candidate_url}"

            return builder

        env.derived(
            "client-a",
            "storage_base_url",
            build("a"),
            dispose=disposed.append,
        )
        env.derived(
            "client-b",
            "storage_base_url",
            build("b"),
            dispose=disposed.append,
        )
        built.clear()

        result = env.apply_mutation(
            ContractMutation(
                "storage-cutover",
                expected_version=0,
                assignments={
                    "storage_base_url": "https://storage-v2.internal"
                },
            )
        )

        self.assertEqual(
            built,
            [
                ("a", "https://storage-v2.internal"),
                ("b", "https://storage-v2.internal"),
            ],
        )
        self.assertEqual(env.version, 1)
        self.assertEqual(
            env.get("storage_base_url"),
            "https://storage-v2.internal",
        )
        self.assertEqual(
            env.get_derived("client-a"),
            "a:https://storage-v2.internal",
        )
        self.assertEqual(
            env.get_derived("client-b"),
            "b:https://storage-v2.internal",
        )
        self.assertEqual(
            disposed,
            [
                "a:https://storage-v1.internal",
                "b:https://storage-v1.internal",
            ],
        )
        self.assertEqual(result.rebuilt_resources, ("client-a", "client-b"))

    def test_late_preparation_failure_preserves_old_projection_and_cleans_candidate(self):
        disposed: list[str] = []

        class ApiEnvironment(EnvironmentContract):
            storage_base_url = HttpVariable("storage_base_url")

        env = ApiEnvironment.from_mapping(
            {"storage_base_url": "https://storage-v1.internal"}
        )
        env.derived(
            "client-a",
            "storage_base_url",
            lambda values: f"a:{values.get('storage_base_url')}",
            dispose=disposed.append,
        )

        def failing_builder(values):
            if values.get("storage_base_url").endswith("v2.internal"):
                raise RuntimeError("contains sensitive provider diagnostics")
            return f"b:{values.get('storage_base_url')}"

        env.derived(
            "client-b",
            "storage_base_url",
            failing_builder,
            dispose=disposed.append,
        )

        with self.assertRaises(ContractPreparationError) as raised:
            env.apply_mutation(
                ContractMutation(
                    "storage-cutover",
                    expected_version=0,
                    assignments={
                        "storage_base_url": "https://storage-v2.internal"
                    },
                )
            )

        self.assertEqual(env.version, 0)
        self.assertEqual(
            env.get("storage_base_url"),
            "https://storage-v1.internal",
        )
        self.assertEqual(
            env.get_derived("client-a"),
            "a:https://storage-v1.internal",
        )
        self.assertEqual(
            env.get_derived("client-b"),
            "b:https://storage-v1.internal",
        )
        self.assertEqual(disposed, ["a:https://storage-v2.internal"])
        self.assertEqual(
            raised.exception.descriptor(),
            {
                "mutation_id": "storage-cutover",
                "resource_name": "client-b",
                "prepared_resources": ["client-a"],
                "cleanup_failures": [],
                "cleanup_uncertainties": [],
            },
        )
        self.assertNotIn("sensitive provider diagnostics", str(raised.exception))

    def test_preparation_cleanup_failure_is_bounded_and_does_not_publish(self):
        class ApiEnvironment(EnvironmentContract):
            storage_base_url = HttpVariable("storage_base_url")

        env = ApiEnvironment.from_mapping(
            {"storage_base_url": "https://storage-v1.internal"}
        )

        def failing_dispose(resource):
            raise RuntimeError(f"could not dispose {resource}")

        env.derived(
            "client-a",
            "storage_base_url",
            lambda values: f"a:{values.get('storage_base_url')}",
            dispose=failing_dispose,
        )

        def failing_build(values):
            if values.get("storage_base_url").endswith("v2.internal"):
                raise RuntimeError("build failed with secret")
            return f"b:{values.get('storage_base_url')}"

        env.derived("client-b", "storage_base_url", failing_build)

        with self.assertRaises(ContractPreparationError) as raised:
            env.apply_patch(
                {"storage_base_url": "https://storage-v2.internal"}
            )

        self.assertEqual(env.version, 0)
        self.assertEqual(raised.exception.cleanup_failures, ("client-a",))
        evidence = f"{raised.exception} {raised.exception.descriptor()}"
        self.assertNotIn("https://storage-v2.internal", evidence)
        self.assertNotIn("build failed with secret", evidence)

    def test_superseded_cleanup_failure_keeps_new_projection_and_replays_evidence(self):
        builds: list[str] = []
        disposals: list[str] = []

        class ApiEnvironment(EnvironmentContract):
            storage_base_url = HttpVariable("storage_base_url")

        env = ApiEnvironment.from_mapping(
            {"storage_base_url": "https://storage-v1.internal"}
        )

        def build(values):
            value = f"client:{values.get('storage_base_url')}"
            builds.append(value)
            return value

        def dispose(value):
            disposals.append(value)
            raise RuntimeError(f"provider leaked {value}")

        env.derived(
            "storage-client",
            "storage_base_url",
            build,
            dispose=dispose,
        )
        mutation = ContractMutation(
            "storage-cutover",
            expected_version=0,
            assignments={"storage_base_url": "https://storage-v2.internal"},
        )

        result = env.apply_mutation(mutation)
        replay = env.apply_mutation(mutation)

        self.assertIs(replay, result)
        self.assertEqual(env.version, 1)
        self.assertEqual(
            env.get_derived("storage-client"),
            "client:https://storage-v2.internal",
        )
        self.assertEqual(len(builds), 2)
        self.assertEqual(disposals, ["client:https://storage-v1.internal"])
        self.assertEqual(
            result.cleanup_uncertainties,
            (ContractCleanupUncertainty("storage-client"),),
        )
        evidence = str(result.descriptor())
        self.assertNotIn("provider leaked", evidence)
        self.assertNotIn("https://storage-v1.internal", evidence)

    def test_competing_prepared_mutations_have_one_publication_winner(self):
        class RouterState(RuntimeContract):
            active_target = RuntimeValueVariable("active_target")

        state = RouterState.from_mapping({"active_target": "v1"})
        first = ContractMutation(
            "route-v2",
            expected_version=0,
            assignments={"active_target": "v2"},
        )
        second = ContractMutation(
            "route-v3",
            expected_version=0,
            assignments={"active_target": "v3"},
        )
        state.prepare_mutation(first)
        state.prepare_mutation(second)
        barrier = Barrier(2)

        def publish(mutation):
            barrier.wait()
            try:
                return state.apply_mutation(mutation)
            except ContractPublicationConflict as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(publish, (first, second)))

        results = [value for value in outcomes if not isinstance(value, Exception)]
        conflicts = [
            value
            for value in outcomes
            if isinstance(value, ContractPublicationConflict)
        ]
        self.assertEqual(len(results), 1)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(state.version, 1)
        self.assertIn(state.get("active_target"), {"v2", "v3"})

    def test_concurrent_identical_mutations_build_dispose_and_publish_once(self):
        builds: list[str] = []
        disposals: list[str] = []

        class RouterState(RuntimeContract):
            active_target = RuntimeValueVariable("active_target")

        state = RouterState.from_mapping({"active_target": "v1"})

        def build(values):
            value = f"client:{values.get('active_target')}"
            builds.append(value)
            return value

        state.derived(
            "client",
            "active_target",
            build,
            dispose=disposals.append,
        )
        mutation = ContractMutation(
            "route-v2",
            expected_version=0,
            assignments={"active_target": "v2"},
        )
        barrier = Barrier(2)

        def publish():
            barrier.wait()
            return state.apply_mutation(mutation)

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(lambda _: publish(), range(2)))

        self.assertIs(outcomes[0], outcomes[1])
        self.assertEqual(builds, ["client:v1", "client:v2"])
        self.assertEqual(disposals, ["client:v1"])
        self.assertEqual(state.version, 1)

    def test_completed_identity_rejects_changed_intent_without_retaining_values(self):
        class RouterState(RuntimeContract):
            active_target = RuntimeValueVariable("active_target")

        state = RouterState.from_mapping({"active_target": "v1"})
        state.apply_mutation(
            ContractMutation(
                "route-change",
                expected_version=0,
                assignments={"active_target": "private-v2"},
            )
        )

        with self.assertRaises(ConflictingContractMutation) as raised:
            state.apply_mutation(
                ContractMutation(
                    "route-change",
                    expected_version=0,
                    assignments={"active_target": "private-v3"},
                )
            )

        evidence = f"{raised.exception} {state.descriptor()}"
        self.assertNotIn("private-v2", evidence)
        self.assertNotIn("private-v3", evidence)

    def test_retained_or_unowned_superseded_resources_are_never_disposed(self):
        disposed: list[str] = []

        class RouterState(RuntimeContract):
            active_target = RuntimeValueVariable("active_target")

        state = RouterState.from_mapping({"active_target": "v1"})
        state.derived(
            "retained-client",
            "active_target",
            lambda values: f"retained:{values.get('active_target')}",
            dispose=disposed.append,
            retained=True,
        )
        state.derived(
            "external-client",
            "active_target",
            lambda values: f"external:{values.get('active_target')}",
            dispose=disposed.append,
            owned=False,
        )

        result = state.apply_patch({"active_target": "v2"})

        self.assertEqual(disposed, [])
        self.assertEqual(
            result.preserved_resources,
            ("external-client", "retained-client"),
        )
        descriptor = state.descriptor()["derived_resources"]
        self.assertFalse(descriptor["external-client"]["owned"])
        self.assertTrue(descriptor["retained-client"]["retained"])

    def test_candidate_builder_cannot_publish_nested_contract_mutation(self):
        class RouterState(RuntimeContract):
            active_target = RuntimeValueVariable("active_target")

        state = RouterState.from_mapping({"active_target": "v1"})

        def nested_mutation(values):
            target = values.get("active_target")
            if target == "v2":
                state.apply_patch({"active_target": "nested"})
            return f"client:{target}"

        state.derived(
            "client",
            "active_target",
            nested_mutation,
        )

        with self.assertRaises(ContractPreparationError) as raised:
            state.apply_patch({"active_target": "v2"})

        self.assertIsInstance(raised.exception.__cause__, ContractMutationInProgress)
        self.assertEqual(state.version, 0)
        self.assertEqual(state.get("active_target"), "v1")
        self.assertEqual(state.get_derived("client"), "client:v1")


class RuntimeContractTests(TestCase):
    def test_runtime_contract_loads_explicit_runtime_state(self):
        class RouterState(RuntimeContract):
            active_target = RuntimeValueVariable("active_target")
            targets = RuntimeMapVariable("targets")

        state = RouterState.from_mapping({
            "active_target": "v1",
            "targets": {"v1": "http://api-v1"},
        })

        self.assertEqual(state.get("active_target"), "v1")
        self.assertEqual(state.get("targets"), {"v1": "http://api-v1"})

    def test_runtime_contract_patch_updates_holder(self):
        class RouterState(RuntimeContract):
            active_target = RuntimeValueVariable("active_target")

        state = RouterState.from_mapping({"active_target": "v1"})
        result = state.apply_patch({"active_target": "v2"})

        self.assertEqual(state.get("active_target"), "v2")
        self.assertEqual(
            result.descriptor(),
            {
                "mutation_id": "local-1",
                "base_version": 0,
                "version": 1,
                "changed": {"active_target": "live"},
            },
        )

    def test_runtime_contract_descriptor_redacts_values(self):
        class RouterState(RuntimeContract):
            active_target = RuntimeValueVariable("active_target")

        state = RouterState.from_mapping({"active_target": "http://private-target"})

        descriptor = state.descriptor()

        self.assertTrue(descriptor["runtime"])
        self.assertEqual(descriptor["variables"]["active_target"]["value"], {"present": True, "redacted": True})
        self.assertNotIn("http://private-target", str(descriptor))

    def test_runtime_contract_does_not_read_process_environment(self):
        class RouterState(RuntimeContract):
            active_target = RuntimeValueVariable("active_target", required=True)

        with self.assertRaises(ControlVariableError):
            RouterState.from_mapping({})
        with self.assertRaises(TypeError):
            RouterState.from_process()


if __name__ == "__main__":
    main()
