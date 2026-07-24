from __future__ import annotations

import os
import unittest

import psycopg

from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.postgres import PostgresStoreBundle
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.read_services import InstanceReadService
from control_plane_kit_operations.runtime_authorities import (
    DockerRuntimeAuthorityCodec,
    LocalDockerSocketAuthority,
    RegisterRuntimeAuthorityCommand,
    RegisteredRuntimeAuthorityStatus,
    RemoteDockerTlsAuthority,
    RuntimeAuthorityKind,
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
                admitted_at="2026-07-24T12:00:00Z",
            )
        )

        self.assertEqual(registered.workspace_id, "workspace-a")
        self.assertEqual(
            registered.authority_ref,
            RuntimeAuthorityReference("mac-mini-docker"),
        )
        self.assertEqual(registered.runtime_kind, RuntimeKind.DOCKER)
        self.assertEqual(registered.authority_kind, RuntimeAuthorityKind.REMOTE_DOCKER_TLS)
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
            )
        )

        service.revoke(
            workspace_id="workspace-a",
            authority_ref=RuntimeAuthorityReference("local-docker"),
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
