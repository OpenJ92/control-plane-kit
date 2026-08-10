from __future__ import annotations

import os
import unittest

import psycopg

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDelivery,
    RuntimeAuthorityAccessDeliveryKind,
    RuntimeAuthorityDeliverySecretReference,
    RuntimeAuthorityReference,
)
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.postgres import PostgresStoreBundle
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.read_services import InstanceReadService
from control_plane_kit_operations.runtime_authorities import (
    DockerRuntimeAuthorityCodec,
    LocalDockerSocketAuthority,
    RegisterRuntimeAuthorityCommand,
    RegisterRuntimeAuthorityDeliveryCommand,
    RegisteredRuntimeAuthorityDeliveryStatus,
    RegisteredRuntimeAuthorityStatus,
    RemoteDockerTlsAuthority,
    RevokeRuntimeAuthorityCommand,
    RevokeRuntimeAuthorityDeliveryCommand,
    RuntimeAuthorityKind,
    RuntimeAuthorityRegistrationError,
    RuntimeAuthorityRegistrationService,
    RuntimeAuthorityRegistrationConflict,
)


class RuntimeAuthorityValueTests(unittest.TestCase):
    def test_runtime_authority_descriptors_are_secret_free(self) -> None:
        authority = RemoteDockerTlsAuthority(
            endpoint="tcp://mac-mini.local:2376",
            ca_certificate=SecretReference("secret://local/docker/ca"),
            client_certificate=SecretReference("secret://local/docker/cert"),
            client_key=SecretReference("secret://local/docker/key"),
        )

        descriptor = authority.descriptor()
        storage = DockerRuntimeAuthorityCodec().encode(authority)

        self.assertEqual(descriptor["kind"], "remote-docker-tls")
        self.assertEqual(descriptor["endpoint"], "<redacted>")
        self.assertEqual(
            descriptor["credential_references"],
            {
                "ca_certificate": "secret://local/docker/ca",
                "client_certificate": "secret://local/docker/cert",
                "client_key": "secret://local/docker/key",
            },
        )
        self.assertNotIn("mac-mini.local", repr(descriptor))
        self.assertIn("mac-mini.local", repr(storage))
        self.assertEqual(DockerRuntimeAuthorityCodec().decode(storage), authority)

    def test_runtime_authority_rejects_secret_shaped_and_unsupported_data(self) -> None:
        with self.assertRaisesRegex(ValueError, "tcp endpoint"):
            RemoteDockerTlsAuthority(
                endpoint="https://mac-mini.local:2376",
                ca_certificate=SecretReference("secret://local/docker/ca"),
                client_certificate=SecretReference("secret://local/docker/cert"),
                client_key=SecretReference("secret://local/docker/key"),
            )
        with self.assertRaisesRegex(ValueError, "credentials"):
            RemoteDockerTlsAuthority(
                endpoint="tcp://token@mac-mini.local:2376",
                ca_certificate=SecretReference("secret://local/docker/ca"),
                client_certificate=SecretReference("secret://local/docker/cert"),
                client_key=SecretReference("secret://local/docker/key"),
            )
        with self.assertRaisesRegex(ValueError, "unknown keys"):
            DockerRuntimeAuthorityCodec().decode(
                {
                    "kind": "remote-docker-tls",
                    "endpoint": "tcp://mac-mini.local:2376",
                    "ca_certificate": "secret://local/docker/ca",
                    "client_certificate": "secret://local/docker/cert",
                    "client_key": "secret://local/docker/key",
                    "private_key": "do-not-store",
                }
            )


class RuntimeAuthorityStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run "
                "./control-plane-kit-operations/test.sh so Docker starts Postgres."
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created'),
                   ('workspace-b', 'Workspace B', 'created')
            """
        )

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def test_service_registers_workspace_scoped_runtime_authority(self) -> None:
        service = RuntimeAuthorityRegistrationService(self.unit_of_work)

        registered = service.register(
            RegisterRuntimeAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
                runtime_kind=RuntimeKind.DOCKER,
                authority=self.remote_authority(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:00:00.000001Z",
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,),
            )
        )

        self.assertEqual(registered.workspace_id, "workspace-a")
        self.assertEqual(
            registered.authority_ref,
            RuntimeAuthorityReference("mac-mini-docker"),
        )
        self.assertEqual(registered.runtime_kind, RuntimeKind.DOCKER)
        self.assertEqual(registered.authority_kind, RuntimeAuthorityKind.REMOTE_DOCKER_TLS)
        self.assertEqual(registered.admitted_at, "2026-07-24T12:00:00.000001Z")
        self.assertEqual(registered.status, RegisteredRuntimeAuthorityStatus.ACTIVE)
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.runtime_authorities.list_active("workspace-a"),
                (registered,),
            )
            self.assertEqual(
                unit_of_work.stores.runtime_authorities.list_active("workspace-b"),
                (),
            )

    def test_runtime_authority_is_idempotent_and_replacement_is_explicit(self) -> None:
        service = RuntimeAuthorityRegistrationService(self.unit_of_work)
        command = RegisterRuntimeAuthorityCommand(
            workspace_id="workspace-a",
            authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
            runtime_kind=RuntimeKind.DOCKER,
            authority=self.remote_authority(endpoint="tcp://mac-mini.local:2376"),
            admitted_by="operator-a",
            admitted_at="2026-07-24T12:00:00Z",
            actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,),
        )

        registered = service.register(command)

        self.assertEqual(service.register(command), registered)
        with self.assertRaisesRegex(RuntimeAuthorityRegistrationConflict, "replacement"):
            service.register(
                RegisterRuntimeAuthorityCommand(
                    workspace_id="workspace-a",
                    authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
                    runtime_kind=RuntimeKind.DOCKER,
                    authority=self.remote_authority(endpoint="tcp://other.local:2376"),
                    admitted_by="operator-a",
                    admitted_at="2026-07-24T12:05:00Z",
                    actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,),
                )
            )

    def test_revoked_runtime_authority_is_not_selectable_but_remains_inspectable(
        self,
    ) -> None:
        service = RuntimeAuthorityRegistrationService(self.unit_of_work)
        registered = service.register(
            RegisterRuntimeAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference("local-docker"),
                runtime_kind=RuntimeKind.DOCKER,
                authority=LocalDockerSocketAuthority(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:00:00Z",
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,),
            )
        )

        service.revoke(
            RevokeRuntimeAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference("local-docker"),
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REVOKE,),
            )
        )

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.runtime_authorities.list_active("workspace-a"),
                (),
            )
            revoked = unit_of_work.stores.runtime_authorities.get(
                "workspace-a",
                RuntimeAuthorityReference("local-docker"),
            )
        self.assertEqual(revoked.registration_id, registered.registration_id)
        self.assertEqual(revoked.status, RegisteredRuntimeAuthorityStatus.REVOKED)

    def test_runtime_authority_store_uses_caller_transaction(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.runtime_authorities.register(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
                runtime_kind=RuntimeKind.DOCKER,
                authority=self.remote_authority(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:00:00Z",
            )

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.runtime_authorities.list_active("workspace-a"),
                (),
            )

    def test_runtime_authority_rejects_noncanonical_timestamp_before_lookup_or_write(
        self,
    ) -> None:
        service = RuntimeAuthorityRegistrationService(self.unit_of_work)

        with self.assertRaisesRegex(
            ValueError,
            "^postgres timestamp must be canonical UTC text$",
        ):
            service.register(
                RegisterRuntimeAuthorityCommand(
                    workspace_id="workspace-a",
                    authority_ref=RuntimeAuthorityReference("invalid-time-runtime"),
                    runtime_kind=RuntimeKind.DOCKER,
                    authority=LocalDockerSocketAuthority(),
                    admitted_by="operator-a",
                    admitted_at="not-a-timestamp",
                    actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,),
                )
            )

        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_runtime_authorities"
            ).fetchone()[0],
            0,
        )

    def test_service_registers_workspace_scoped_authority_delivery(self) -> None:
        service = RuntimeAuthorityRegistrationService(self.unit_of_work)
        service.register(
            RegisterRuntimeAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference("local-docker"),
                runtime_kind=RuntimeKind.DOCKER,
                authority=LocalDockerSocketAuthority(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:00:00Z",
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,),
            )
        )

        registered = service.register_delivery(
            RegisterRuntimeAuthorityDeliveryCommand(
                workspace_id="workspace-a",
                delivery=self.local_delivery(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:01:00.000001Z",
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER,),
            )
        )

        self.assertEqual(registered.workspace_id, "workspace-a")
        self.assertEqual(
            registered.authority_ref,
            RuntimeAuthorityReference("local-docker"),
        )
        self.assertEqual(
            registered.delivery.delivery_kind,
            RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,
        )
        self.assertEqual(
            registered.status,
            RegisteredRuntimeAuthorityDeliveryStatus.ACTIVE,
        )
        self.assertEqual(registered.admitted_at, "2026-07-24T12:01:00.000001Z")
        self.assertNotIn("/var/run/docker.sock", repr(registered.descriptor()))

    def test_runtime_delivery_rejects_noncanonical_timestamp_before_lookup_or_write(
        self,
    ) -> None:
        service = RuntimeAuthorityRegistrationService(self.unit_of_work)
        service.register(
            RegisterRuntimeAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference("local-docker"),
                runtime_kind=RuntimeKind.DOCKER,
                authority=LocalDockerSocketAuthority(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:00:00Z",
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,),
            )
        )

        with self.assertRaisesRegex(
            ValueError,
            "^postgres timestamp must be canonical UTC text$",
        ):
            service.register_delivery(
                RegisterRuntimeAuthorityDeliveryCommand(
                    workspace_id="workspace-a",
                    delivery=self.local_delivery(),
                    admitted_by="operator-a",
                    admitted_at="not-a-timestamp",
                    actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER,),
                )
            )

        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_runtime_authority_deliveries"
            ).fetchone()[0],
            0,
        )

    def test_authority_delivery_requires_active_registered_authority(self) -> None:
        service = RuntimeAuthorityRegistrationService(self.unit_of_work)
        with self.assertRaisesRegex(RuntimeAuthorityRegistrationError, "active"):
            service.register_delivery(
                RegisterRuntimeAuthorityDeliveryCommand(
                    workspace_id="workspace-a",
                    delivery=self.local_delivery(),
                    admitted_by="operator-a",
                    admitted_at="2026-07-24T12:01:00Z",
                    actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER,),
                )
            )
        service.register(
            RegisterRuntimeAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference("local-docker"),
                runtime_kind=RuntimeKind.DOCKER,
                authority=LocalDockerSocketAuthority(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:00:00Z",
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,),
            )
        )
        service.revoke(
            RevokeRuntimeAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference("local-docker"),
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REVOKE,),
            )
        )

        with self.assertRaisesRegex(RuntimeAuthorityRegistrationError, "active"):
            service.register_delivery(
                RegisterRuntimeAuthorityDeliveryCommand(
                    workspace_id="workspace-a",
                    delivery=self.local_delivery(),
                    admitted_by="operator-a",
                    admitted_at="2026-07-24T12:02:00Z",
                    actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER,),
                )
            )

    def test_authority_delivery_is_idempotent_and_replacement_is_explicit(self) -> None:
        service = RuntimeAuthorityRegistrationService(self.unit_of_work)
        service.register(
            RegisterRuntimeAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
                runtime_kind=RuntimeKind.DOCKER,
                authority=self.remote_authority(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:00:00Z",
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,),
            )
        )
        command = RegisterRuntimeAuthorityDeliveryCommand(
            workspace_id="workspace-a",
            delivery=self.tls_delivery(),
            admitted_by="operator-a",
            admitted_at="2026-07-24T12:01:00Z",
            actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER,),
        )

        registered = service.register_delivery(command)

        self.assertEqual(service.register_delivery(command), registered)
        with self.assertRaisesRegex(RuntimeAuthorityRegistrationConflict, "replacement"):
            service.register_delivery(
                RegisterRuntimeAuthorityDeliveryCommand(
                    workspace_id="workspace-a",
                    delivery=RuntimeAuthorityAccessDelivery(
                        RuntimeAuthorityReference("mac-mini-docker"),
                        (
                            RuntimeAuthorityAccessDeliveryKind
                            .CLOUD_CREDENTIAL_SECRET_SESSION
                        ),
                        (
                            RuntimeAuthorityDeliverySecretReference(
                                "aws-role",
                                "secret://local/workspace-a/aws/role",
                            ),
                        ),
                    ),
                    admitted_by="operator-a",
                    admitted_at="2026-07-24T12:02:00Z",
                    actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER,),
                )
            )

    def test_authority_delivery_store_uses_caller_transaction(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.runtime_authorities.register(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference("local-docker"),
                runtime_kind=RuntimeKind.DOCKER,
                authority=LocalDockerSocketAuthority(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:00:00Z",
            )
            unit_of_work.stores.runtime_authority_deliveries.register(
                workspace_id="workspace-a",
                delivery=self.local_delivery(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:01:00Z",
            )

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.runtime_authority_deliveries.list_active(
                    "workspace-a"
                ),
                (),
            )

    def test_revoked_authority_delivery_is_not_selectable_but_remains_inspectable(
        self,
    ) -> None:
        service = RuntimeAuthorityRegistrationService(self.unit_of_work)
        service.register(
            RegisterRuntimeAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference("local-docker"),
                runtime_kind=RuntimeKind.DOCKER,
                authority=LocalDockerSocketAuthority(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:00:00Z",
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,),
            )
        )
        registered = service.register_delivery(
            RegisterRuntimeAuthorityDeliveryCommand(
                workspace_id="workspace-a",
                delivery=self.local_delivery(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:01:00Z",
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER,),
            )
        )

        service.revoke_delivery(
            RevokeRuntimeAuthorityDeliveryCommand(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference("local-docker"),
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REVOKE,),
            )
        )

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.runtime_authority_deliveries.list_active(
                    "workspace-a"
                ),
                (),
            )
            revoked = unit_of_work.stores.runtime_authority_deliveries.get(
                "workspace-a",
                RuntimeAuthorityReference("local-docker"),
            )
        self.assertEqual(revoked.delivery_id, registered.delivery_id)
        self.assertEqual(
            revoked.status,
            RegisteredRuntimeAuthorityDeliveryStatus.REVOKED,
        )

    def test_read_model_lists_authorities_without_endpoint_material(self) -> None:
        service = RuntimeAuthorityRegistrationService(self.unit_of_work)
        service.register(
            RegisterRuntimeAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
                runtime_kind=RuntimeKind.DOCKER,
                authority=self.remote_authority(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:00:00Z",
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,),
            )
        )
        read_service = InstanceReadService(
            workspace_store=PostgresStoreBundle(self.connection).workspaces,
            graph_topology_store=PostgresStoreBundle(self.connection).graphs,
            runtime_authority_store=PostgresStoreBundle(
                self.connection
            ).runtime_authorities,
        )

        descriptor = read_service.runtime_authorities("workspace-a").descriptor()

        self.assertEqual(descriptor["workspace_id"], "workspace-a")
        self.assertEqual(descriptor["items"][0]["authority_ref"], "mac-mini-docker")
        self.assertEqual(descriptor["items"][0]["authority"]["endpoint"], "<redacted>")
        self.assertNotIn("mac-mini.local", repr(descriptor))
        self.assertNotIn("2376", repr(descriptor))

    def test_read_model_lists_authority_deliveries_without_access_material(self) -> None:
        service = RuntimeAuthorityRegistrationService(self.unit_of_work)
        service.register(
            RegisterRuntimeAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
                runtime_kind=RuntimeKind.DOCKER,
                authority=self.remote_authority(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:00:00Z",
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,),
            )
        )
        service.register_delivery(
            RegisterRuntimeAuthorityDeliveryCommand(
                workspace_id="workspace-a",
                delivery=self.tls_delivery(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:01:00Z",
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER,),
            )
        )
        bundle = PostgresStoreBundle(self.connection)
        read_service = InstanceReadService(
            workspace_store=bundle.workspaces,
            graph_topology_store=bundle.graphs,
            runtime_authority_delivery_store=bundle.runtime_authority_deliveries,
        )

        descriptor = read_service.runtime_authority_deliveries(
            "workspace-a"
        ).descriptor()

        self.assertEqual(descriptor["workspace_id"], "workspace-a")
        self.assertEqual(
            descriptor["items"][0]["authority_ref"],
            "mac-mini-docker",
        )
        self.assertEqual(
            descriptor["items"][0]["delivery_kind"],
            "remote-docker-tls-secret-files",
        )
        self.assertEqual(
            descriptor["items"][0]["delivery"]["secret_references"],
            "<redacted>",
        )
        self.assertNotIn("secret://local/workspace-a/docker/client-key", repr(descriptor))
        self.assertNotIn("/var/run/docker.sock", repr(descriptor))
        self.assertNotIn("BEGIN", repr(descriptor))
        self.assertNotIn("PRIVATE KEY", repr(descriptor))
        self.assertNotIn("mac-mini.local", repr(descriptor))

    def test_service_requires_focused_runtime_authority_scopes(self) -> None:
        service = RuntimeAuthorityRegistrationService(self.unit_of_work)
        with self.assertRaisesRegex(RuntimeAuthorityRegistrationError, "register"):
            service.register(
                RegisterRuntimeAuthorityCommand(
                    workspace_id="workspace-a",
                    authority_ref=RuntimeAuthorityReference("local-docker"),
                    runtime_kind=RuntimeKind.DOCKER,
                    authority=LocalDockerSocketAuthority(),
                    admitted_by="operator-a",
                    admitted_at="2026-07-24T12:00:00Z",
                    actor_scopes=(PolicyScope.PLAN_EXECUTE,),
                )
            )

        service.register(
            RegisterRuntimeAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=RuntimeAuthorityReference("local-docker"),
                runtime_kind=RuntimeKind.DOCKER,
                authority=LocalDockerSocketAuthority(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:00:00Z",
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,),
            )
        )

        with self.assertRaisesRegex(RuntimeAuthorityRegistrationError, "revoke"):
            service.revoke(
                RevokeRuntimeAuthorityCommand(
                    workspace_id="workspace-a",
                    authority_ref=RuntimeAuthorityReference("local-docker"),
                    actor_scopes=(PolicyScope.PLAN_EXECUTE,),
                )
            )

        with self.assertRaisesRegex(RuntimeAuthorityRegistrationError, "delivery"):
            service.register_delivery(
                RegisterRuntimeAuthorityDeliveryCommand(
                    workspace_id="workspace-a",
                    delivery=self.local_delivery(),
                    admitted_by="operator-a",
                    admitted_at="2026-07-24T12:02:00Z",
                    actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,),
                )
            )

        service.register_delivery(
            RegisterRuntimeAuthorityDeliveryCommand(
                workspace_id="workspace-a",
                delivery=self.local_delivery(),
                admitted_by="operator-a",
                admitted_at="2026-07-24T12:02:00Z",
                actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER,),
            )
        )

        with self.assertRaisesRegex(RuntimeAuthorityRegistrationError, "delivery"):
            service.revoke_delivery(
                RevokeRuntimeAuthorityDeliveryCommand(
                    workspace_id="workspace-a",
                    authority_ref=RuntimeAuthorityReference("local-docker"),
                    actor_scopes=(PolicyScope.RUNTIME_AUTHORITY_REVOKE,),
                )
            )

    def local_delivery(self) -> RuntimeAuthorityAccessDelivery:
        return RuntimeAuthorityAccessDelivery(
            authority_ref=RuntimeAuthorityReference("local-docker"),
            delivery_kind=RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,
        )

    def tls_delivery(self) -> RuntimeAuthorityAccessDelivery:
        return RuntimeAuthorityAccessDelivery(
            authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
            delivery_kind=(
                RuntimeAuthorityAccessDeliveryKind.REMOTE_DOCKER_TLS_SECRET_FILES
            ),
            secret_references=(
                RuntimeAuthorityDeliverySecretReference(
                    "ca-cert",
                    "secret://local/workspace-a/docker/ca-cert",
                ),
                RuntimeAuthorityDeliverySecretReference(
                    "client-cert",
                    "secret://local/workspace-a/docker/client-cert",
                ),
                RuntimeAuthorityDeliverySecretReference(
                    "client-key",
                    "secret://local/workspace-a/docker/client-key",
                ),
            ),
        )

    def remote_authority(
        self,
        *,
        endpoint: str = "tcp://mac-mini.local:2376",
    ) -> RemoteDockerTlsAuthority:
        return RemoteDockerTlsAuthority(
            endpoint=endpoint,
            ca_certificate=SecretReference("secret://local/docker/ca"),
            client_certificate=SecretReference("secret://local/docker/cert"),
            client_key=SecretReference("secret://local/docker/key"),
        )
