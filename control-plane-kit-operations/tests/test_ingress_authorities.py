from __future__ import annotations

import os
import unittest

import psycopg

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    PublicIngressLifecycle,
)
from control_plane_kit_core.secrets import SecretReference, SecretValue
from control_plane_kit_operations.ingress_authorities import (
    CloudflareIngressTeardownActionKind,
    CloudflareOwnedIngressResource,
    CloudflareTunnelTokenDeliveryStep,
    CloudflareZoneIngressAuthority,
    GeneratedSecretPurpose,
    GeneratedSecretRecordingConflict,
    InMemoryGeneratedSecretRecorder,
    IngressAuthorityAuthorizationDenied,
    IngressAuthorityNotFound,
    IngressAuthorityProviderKind,
    IngressAuthorityRegistrationConflict,
    IngressAuthorityRegistrationService,
    OwnedIngressResourceConflict,
    RegisterIngressAuthorityCommand,
    RegisteredIngressAuthorityStatus,
    RevokeIngressAuthorityCommand,
    cloudflare_ingress_teardown_plan,
    cloudflare_tunnel_token_delivery_plan,
    record_generated_ingress_secret,
    require_cloudflared_tunnel_token_delivery,
)
from control_plane_kit_operations.postgres import (
    PostgresStoreBundle,
    PostgresUnitOfWork,
    install_schema,
)
from control_plane_kit_operations.read_services import InstanceReadService


class IngressAuthorityValueTests(unittest.TestCase):
    def test_cloudflare_zone_authority_descriptor_is_secret_free(self) -> None:
        authority = CloudflareZoneIngressAuthority(
            account_id="account-openj92",
            zone_id="zone-openj92",
            zone_name="openj92.dev",
            api_token_ref=SecretReference("secret://cloudflare/openj92/api-token"),
            allowed_hostname_pattern="cpk-gateway-*.openj92.dev",
        )

        descriptor = authority.descriptor()

        self.assertEqual(descriptor["provider_kind"], "cloudflare")
        self.assertEqual(descriptor["zone_name"], "openj92.dev")
        self.assertEqual(
            descriptor["api_token_ref"],
            "secret://cloudflare/openj92/api-token",
        )
        self.assertNotIn("cf_api_token", repr(descriptor).lower())
        self.assertNotIn("bearer", repr(descriptor).lower())
        self.assertTrue(authority.allows_hostname("cpk-gateway-001.openj92.dev"))
        self.assertFalse(authority.allows_hostname("gateway-001.cpk.openj92.dev"))

    def test_cloudflare_zone_authority_rejects_raw_tokens_and_wide_patterns(self) -> None:
        with self.assertRaisesRegex(ValueError, "SecretReference"):
            CloudflareZoneIngressAuthority(
                account_id="account-openj92",
                zone_id="zone-openj92",
                zone_name="openj92.dev",
                api_token_ref="cf_api_token_raw",  # type: ignore[arg-type]
                allowed_hostname_pattern="cpk-gateway-*.openj92.dev",
            )
        with self.assertRaisesRegex(ValueError, "zone"):
            CloudflareZoneIngressAuthority(
                account_id="account-openj92",
                zone_id="zone-openj92",
                zone_name="openj92.dev",
                api_token_ref=SecretReference("secret://cloudflare/openj92/api-token"),
                allowed_hostname_pattern="*.example.com",
            )
        with self.assertRaisesRegex(ValueError, "secret"):
            CloudflareZoneIngressAuthority(
                account_id="account-openj92",
                zone_id="zone-openj92",
                zone_name="openj92.dev",
                api_token_ref=SecretReference("secret://cloudflare/openj92/api-token"),
                allowed_hostname_pattern="cpk-token-*.openj92.dev",
            )

    def test_cloudflare_owned_resource_evidence_is_bounded_and_secret_free(self) -> None:
        resource = self.cloudflare_resource()

        descriptor = resource.descriptor()

        self.assertEqual(descriptor["workspace_id"], "workspace-a")
        self.assertEqual(descriptor["runtime_id"], "docker-a")
        self.assertEqual(descriptor["tunnel_id"], "tunnel-001")
        self.assertEqual(descriptor["dns_record_id"], "dns-001")
        self.assertEqual(descriptor["hostname"], "cpk-gateway-001.openj92.dev")
        self.assertEqual(descriptor["lifecycle"], "ephemeral")
        self.assertNotIn("cf_api_token", repr(descriptor).lower())
        self.assertNotIn("bearer", repr(descriptor).lower())
        self.assertNotIn("eyj", repr(descriptor).lower())

    def test_cloudflare_teardown_uses_recorded_ids_not_broad_search(self) -> None:
        plan = cloudflare_ingress_teardown_plan(
            authority=self.cloudflare_authority(),
            resource=self.cloudflare_resource(),
        )

        self.assertEqual(
            [action.kind for action in plan.actions],
            [
                CloudflareIngressTeardownActionKind.DELETE_DNS_RECORD,
                CloudflareIngressTeardownActionKind.DELETE_TUNNEL,
            ],
        )
        self.assertEqual(plan.actions[0].resource_id, "dns-001")
        self.assertEqual(plan.actions[1].resource_id, "tunnel-001")
        self.assertNotIn("search", repr(plan.descriptor()).lower())
        self.assertNotIn("list", repr(plan.descriptor()).lower())

    def test_cloudflare_teardown_skips_retained_and_external_lifecycles(self) -> None:
        for lifecycle in (
            PublicIngressLifecycle.RETAINED,
            PublicIngressLifecycle.EXTERNAL,
        ):
            with self.subTest(lifecycle=lifecycle):
                plan = cloudflare_ingress_teardown_plan(
                    authority=self.cloudflare_authority(),
                    resource=self.cloudflare_resource(lifecycle=lifecycle),
                )

                self.assertEqual(
                    [action.kind for action in plan.actions],
                    [CloudflareIngressTeardownActionKind.SKIP_RETAINED_OR_EXTERNAL],
                )

    def test_cloudflare_teardown_fails_closed_on_missing_or_ambiguous_evidence(
        self,
    ) -> None:
        authority = self.cloudflare_authority()

        with self.assertRaisesRegex(ValueError, "ownership evidence"):
            cloudflare_ingress_teardown_plan(authority=authority, resource=None)
        with self.assertRaisesRegex(ValueError, "zone"):
            cloudflare_ingress_teardown_plan(
                authority=authority,
                resource=self.cloudflare_resource(zone_id="zone-other"),
            )
        with self.assertRaisesRegex(ValueError, "hostname"):
            cloudflare_ingress_teardown_plan(
                authority=authority,
                resource=self.cloudflare_resource(hostname="gateway-001.cpk.openj92.dev"),
            )
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            cloudflare_ingress_teardown_plan(
                authority=authority,
                resource=self.cloudflare_resource(tunnel_name="auth-potteryfactory"),
            )

    def test_cloudflare_tunnel_token_delivery_is_secret_reference_only(self) -> None:
        plan = cloudflare_tunnel_token_delivery_plan(
            authority=self.cloudflare_authority(),
            resource=self.cloudflare_resource(),
            connector_node_id="cloudflared-001",
            tunnel_token_ref=SecretReference(
                "secret://cloudflare/openj92/cpk-gateway-001-tunnel-token"
            ),
        )

        descriptor = plan.descriptor()

        self.assertEqual(descriptor["connector_node_id"], "cloudflared-001")
        self.assertEqual(
            descriptor["secret_delivery"],
            {
                "kind": "environment",
                "environment_name": "TUNNEL_TOKEN",
                "reference_id": (
                    "secret://cloudflare/openj92/cpk-gateway-001-tunnel-token"
                ),
            },
        )
        self.assertEqual(
            tuple(plan.ordering),
            (
                CloudflareTunnelTokenDeliveryStep.ALLOCATE_NAMED_INGRESS,
                CloudflareTunnelTokenDeliveryStep.RECORD_TUNNEL_TOKEN_SECRET,
                CloudflareTunnelTokenDeliveryStep.START_CLOUDFLARED_CONNECTOR,
            ),
        )
        self.assertNotIn("eyj", repr(plan).lower())
        self.assertNotIn("bearer", repr(plan).lower())
        self.assertNotIn("cf_api_token", repr(plan).lower())

    def test_generated_tunnel_token_recording_returns_secret_reference_evidence(
        self,
    ) -> None:
        recorder = InMemoryGeneratedSecretRecorder()

        evidence = record_generated_ingress_secret(
            recorder=recorder,
            workspace_id="workspace-a",
            purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
            source_run_id="run-001",
            source_activity_id="activity-001",
            source_event_id="event-001",
            recorded_at="2026-07-28T07:00:00Z",
            secret_value=SecretValue("eyJ-cloudflare-tunnel-token-bearer-value"),
        )
        descriptor = evidence.descriptor()

        self.assertEqual(
            evidence.secret_ref.reference_id,
            (
                "secret://generated/ingress/workspace-a/"
                "cloudflared-tunnel-token/run-001/activity-001/event-001"
            ),
        )
        self.assertEqual(
            recorder.resolve_generated_secret(evidence.secret_ref).reveal(),
            "eyJ-cloudflare-tunnel-token-bearer-value",
        )
        self.assertEqual(descriptor["purpose"], "cloudflared-tunnel-token")
        self.assertEqual(descriptor["source_event_id"], "event-001")
        self.assertNotIn("eyj-cloudflare", repr(descriptor).lower())
        self.assertNotIn("bearer-value", repr(descriptor).lower())

    def test_cloudflared_connector_requires_explicit_tunnel_token_delivery(
        self,
    ) -> None:
        plan = cloudflare_tunnel_token_delivery_plan(
            authority=self.cloudflare_authority(),
            resource=self.cloudflare_resource(),
            connector_node_id="cloudflared-001",
            tunnel_token_ref=SecretReference(
                "secret://cloudflare/openj92/cpk-gateway-001-tunnel-token"
            ),
        )

        self.assertEqual(
            require_cloudflared_tunnel_token_delivery((plan.secret_delivery,)),
            plan.secret_delivery,
        )
        with self.assertRaisesRegex(ValueError, "requires exactly one"):
            require_cloudflared_tunnel_token_delivery(())
        with self.assertRaisesRegex(ValueError, "requires exactly one"):
            require_cloudflared_tunnel_token_delivery(
                (plan.secret_delivery, plan.secret_delivery)
            )

    def test_cloudflare_tunnel_token_delivery_fails_closed_on_wrong_authority(
        self,
    ) -> None:
        authority = self.cloudflare_authority()

        with self.assertRaisesRegex(ValueError, "zone"):
            cloudflare_tunnel_token_delivery_plan(
                authority=authority,
                resource=self.cloudflare_resource(zone_id="zone-other"),
                connector_node_id="cloudflared-001",
                tunnel_token_ref=SecretReference(
                    "secret://cloudflare/openj92/cpk-gateway-001-tunnel-token"
                ),
            )
        with self.assertRaisesRegex(ValueError, "hostname"):
            cloudflare_tunnel_token_delivery_plan(
                authority=authority,
                resource=self.cloudflare_resource(hostname="gateway-001.cpk.openj92.dev"),
                connector_node_id="cloudflared-001",
                tunnel_token_ref=SecretReference(
                    "secret://cloudflare/openj92/cpk-gateway-001-tunnel-token"
                ),
            )

    def cloudflare_authority(self) -> CloudflareZoneIngressAuthority:
        return CloudflareZoneIngressAuthority(
            account_id="account-openj92",
            zone_id="zone-openj92",
            zone_name="openj92.dev",
            api_token_ref=SecretReference("secret://cloudflare/openj92/api-token"),
            allowed_hostname_pattern="cpk-gateway-*.openj92.dev",
        )

    def cloudflare_resource(
        self,
        *,
        lifecycle: PublicIngressLifecycle = PublicIngressLifecycle.EPHEMERAL,
        zone_id: str = "zone-openj92",
        hostname: str = "cpk-gateway-001.openj92.dev",
        tunnel_name: str = "cpk-gateway-001",
        tunnel_id: str = "tunnel-001",
    ) -> CloudflareOwnedIngressResource:
        return CloudflareOwnedIngressResource(
            workspace_id="workspace-a",
            runtime_id="docker-a",
            ingress_id="gateway-001",
            authority_ref=IngressAuthorityReference("openj92-public-ingress"),
            provider_kind=IngressAuthorityProviderKind.CLOUDFLARE,
            tunnel_name=tunnel_name,
            tunnel_id=tunnel_id,
            dns_record_id="dns-001",
            hostname=hostname,
            zone_id=zone_id,
            lifecycle=lifecycle,
            created_at="2026-07-27T23:30:00Z",
            observed_at="2026-07-27T23:31:00Z",
            source_run_id="run-001",
            source_activity_id="activity-001",
            source_event_id="event-001",
        )


class IngressAuthorityStoreTests(unittest.TestCase):
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

    def test_service_registers_workspace_scoped_ingress_authority(self) -> None:
        service = IngressAuthorityRegistrationService(self.unit_of_work)

        registered = service.register(
            RegisterIngressAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=IngressAuthorityReference("openj92-public-ingress"),
                authority=self.cloudflare_authority(),
                admitted_by="operator-a",
                admitted_at="2026-07-27T22:50:00Z",
                actor_scopes=(PolicyScope.INGRESS_AUTHORITY_REGISTER,),
            )
        )

        self.assertEqual(registered.workspace_id, "workspace-a")
        self.assertEqual(
            registered.authority_ref,
            IngressAuthorityReference("openj92-public-ingress"),
        )
        self.assertEqual(
            registered.provider_kind,
            IngressAuthorityProviderKind.CLOUDFLARE,
        )
        self.assertEqual(registered.status, RegisteredIngressAuthorityStatus.ACTIVE)
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.ingress_authorities.list_active("workspace-a"),
                (registered,),
            )
            self.assertEqual(
                unit_of_work.stores.ingress_authorities.list_active("workspace-b"),
                (),
            )

    def test_ingress_authority_registration_is_idempotent_and_replacement_is_explicit(
        self,
    ) -> None:
        service = IngressAuthorityRegistrationService(self.unit_of_work)
        command = RegisterIngressAuthorityCommand(
            workspace_id="workspace-a",
            authority_ref=IngressAuthorityReference("openj92-public-ingress"),
            authority=self.cloudflare_authority(zone_id="zone-a"),
            admitted_by="operator-a",
            admitted_at="2026-07-27T22:50:00Z",
            actor_scopes=(PolicyScope.INGRESS_AUTHORITY_REGISTER,),
        )

        registered = service.register(command)

        self.assertEqual(service.register(command), registered)
        with self.assertRaisesRegex(IngressAuthorityRegistrationConflict, "replacement"):
            service.register(
                RegisterIngressAuthorityCommand(
                    workspace_id="workspace-a",
                    authority_ref=IngressAuthorityReference("openj92-public-ingress"),
                    authority=self.cloudflare_authority(zone_id="zone-b"),
                    admitted_by="operator-a",
                    admitted_at="2026-07-27T22:55:00Z",
                    actor_scopes=(PolicyScope.INGRESS_AUTHORITY_REGISTER,),
                )
            )

    def test_revoked_ingress_authority_is_not_selectable_but_remains_inspectable(
        self,
    ) -> None:
        service = IngressAuthorityRegistrationService(self.unit_of_work)
        registered = service.register(
            RegisterIngressAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=IngressAuthorityReference("openj92-public-ingress"),
                authority=self.cloudflare_authority(),
                admitted_by="operator-a",
                admitted_at="2026-07-27T22:50:00Z",
                actor_scopes=(PolicyScope.INGRESS_AUTHORITY_REGISTER,),
            )
        )

        service.revoke(
            RevokeIngressAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=IngressAuthorityReference("openj92-public-ingress"),
                actor_scopes=(PolicyScope.INGRESS_AUTHORITY_REVOKE,),
            )
        )

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.ingress_authorities.list_active("workspace-a"),
                (),
            )
            revoked = unit_of_work.stores.ingress_authorities.get(
                "workspace-a",
                IngressAuthorityReference("openj92-public-ingress"),
            )
        self.assertEqual(revoked.registration_id, registered.registration_id)
        self.assertEqual(revoked.status, RegisteredIngressAuthorityStatus.REVOKED)

    def test_service_requires_focused_scopes_and_read_model_redacts(self) -> None:
        service = IngressAuthorityRegistrationService(self.unit_of_work)

        with self.assertRaises(IngressAuthorityAuthorizationDenied):
            service.register(
                RegisterIngressAuthorityCommand(
                    workspace_id="workspace-a",
                    authority_ref=IngressAuthorityReference("openj92-public-ingress"),
                    authority=self.cloudflare_authority(),
                    admitted_by="operator-a",
                    admitted_at="2026-07-27T22:50:00Z",
                    actor_scopes=(PolicyScope.PLAN_EXECUTE,),
                )
            )

        registered = service.register(
            RegisterIngressAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=IngressAuthorityReference("openj92-public-ingress"),
                authority=self.cloudflare_authority(),
                admitted_by="operator-a",
                admitted_at="2026-07-27T22:50:00Z",
                actor_scopes=(PolicyScope.INGRESS_AUTHORITY_REGISTER,),
            )
        )
        read_service = self.read_service()

        listed = read_service.ingress_authorities("workspace-a").descriptor()
        detail = read_service.ingress_authority_detail(
            "workspace-a",
            IngressAuthorityReference("openj92-public-ingress"),
        ).descriptor()

        self.assertEqual(listed["items"][0]["authority_ref"], "openj92-public-ingress")
        self.assertEqual(
            detail["ingress_authority"]["registration_id"],
            registered.registration_id,
        )
        self.assertIn("api_token_ref", repr(detail))
        self.assertNotIn("cf_api_token", repr(detail).lower())
        self.assertNotIn("bearer", repr(detail).lower())

    def test_active_authority_use_is_hostname_policy_guarded(self) -> None:
        service = IngressAuthorityRegistrationService(self.unit_of_work)
        service.register(
            RegisterIngressAuthorityCommand(
                workspace_id="workspace-a",
                authority_ref=IngressAuthorityReference("openj92-public-ingress"),
                authority=self.cloudflare_authority(),
                admitted_by="operator-a",
                admitted_at="2026-07-27T22:50:00Z",
                actor_scopes=(PolicyScope.INGRESS_AUTHORITY_REGISTER,),
            )
        )

        with self.unit_of_work() as unit_of_work:
            selected = unit_of_work.stores.ingress_authorities.require_active_for_hostname(
                "workspace-a",
                IngressAuthorityReference("openj92-public-ingress"),
                "cpk-gateway-001.openj92.dev",
            )
            self.assertEqual(selected.authority_ref.reference_id, "openj92-public-ingress")
            with self.assertRaises(IngressAuthorityNotFound):
                unit_of_work.stores.ingress_authorities.require_active_for_hostname(
                    "workspace-a",
                    IngressAuthorityReference("openj92-public-ingress"),
                    "gateway-001.cpk.openj92.dev",
                )

    def test_owned_cloudflare_resource_evidence_is_durable_and_idempotent(
        self,
    ) -> None:
        resource = IngressAuthorityValueTests().cloudflare_resource()
        with self.unit_of_work() as unit_of_work:
            recorded = unit_of_work.stores.ingress_resources.record_cloudflare(
                resource
            )
            unit_of_work.commit()

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.ingress_resources.get_cloudflare(
                    "workspace-a",
                    "gateway-001",
                ),
                recorded,
            )
            self.assertEqual(
                unit_of_work.stores.ingress_resources.list_cloudflare("workspace-a"),
                (recorded,),
            )
            self.assertEqual(
                unit_of_work.stores.ingress_resources.list_cloudflare("workspace-b"),
                (),
            )
            self.assertEqual(
                unit_of_work.stores.ingress_resources.record_cloudflare(resource),
                recorded,
            )
            with self.assertRaisesRegex(OwnedIngressResourceConflict, "replacement"):
                unit_of_work.stores.ingress_resources.record_cloudflare(
                    IngressAuthorityValueTests().cloudflare_resource(
                        tunnel_id="tunnel-002"
                    )
                )

        descriptor = recorded.descriptor()
        self.assertEqual(descriptor["authority_ref"], "openj92-public-ingress")
        self.assertEqual(descriptor["provider_kind"], "cloudflare")
        self.assertEqual(descriptor["source_run_id"], "run-001")
        self.assertEqual(descriptor["source_activity_id"], "activity-001")
        self.assertEqual(descriptor["source_event_id"], "event-001")
        self.assertNotIn("cf_api_token", repr(descriptor).lower())
        self.assertNotIn("bearer", repr(descriptor).lower())
        self.assertNotIn("eyj", repr(descriptor).lower())

    def test_generated_ingress_secret_reference_is_durable_and_secret_free(
        self,
    ) -> None:
        recorder = InMemoryGeneratedSecretRecorder()
        evidence = record_generated_ingress_secret(
            recorder=recorder,
            workspace_id="workspace-a",
            purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
            source_run_id="run-001",
            source_activity_id="activity-001",
            source_event_id="event-001",
            recorded_at="2026-07-28T07:00:00Z",
            secret_value=SecretValue("eyJ-cloudflare-tunnel-token-bearer-value"),
        )

        with self.unit_of_work() as unit_of_work:
            recorded = unit_of_work.stores.generated_ingress_secrets.record(evidence)
            unit_of_work.commit()

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.generated_ingress_secrets.get_by_source(
                    workspace_id="workspace-a",
                    purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                    source_run_id="run-001",
                    source_activity_id="activity-001",
                    source_event_id="event-001",
                ),
                recorded,
            )
            self.assertEqual(
                unit_of_work.stores.generated_ingress_secrets.list_for_workspace(
                    "workspace-a"
                ),
                (recorded,),
            )
            self.assertEqual(
                unit_of_work.stores.generated_ingress_secrets.list_for_workspace(
                    "workspace-b"
                ),
                (),
            )
            self.assertEqual(
                unit_of_work.stores.generated_ingress_secrets.record(evidence),
                recorded,
            )
            with self.assertRaisesRegex(GeneratedSecretRecordingConflict, "replacement"):
                unit_of_work.stores.generated_ingress_secrets.record(
                    record_generated_ingress_secret(
                        recorder=InMemoryGeneratedSecretRecorder(
                            "secret://generated/alternate"
                        ),
                        workspace_id="workspace-a",
                        purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                        source_run_id="run-001",
                        source_activity_id="activity-001",
                        source_event_id="event-001",
                        recorded_at="2026-07-28T07:00:00Z",
                        secret_value=SecretValue(
                            "eyJ-cloudflare-tunnel-token-bearer-value"
                        ),
                    )
                )

        descriptor = recorded.descriptor()
        self.assertEqual(
            descriptor["secret_ref"],
            (
                "secret://generated/ingress/workspace-a/"
                "cloudflared-tunnel-token/run-001/activity-001/event-001"
            ),
        )
        self.assertNotIn("eyj-cloudflare", repr(descriptor).lower())
        self.assertNotIn("bearer-value", repr(descriptor).lower())
        self.assertNotIn("cf_api_token", repr(descriptor).lower())

    def read_service(self) -> InstanceReadService:
        stores = PostgresStoreBundle(self.connection)
        return InstanceReadService(
            workspace_store=stores.workspaces,
            graph_topology_store=stores.graphs,
            ingress_authority_store=stores.ingress_authorities,
        )

    def cloudflare_authority(
        self,
        *,
        zone_id: str = "zone-openj92",
    ) -> CloudflareZoneIngressAuthority:
        return CloudflareZoneIngressAuthority(
            account_id="account-openj92",
            zone_id=zone_id,
            zone_name="openj92.dev",
            api_token_ref=SecretReference("secret://cloudflare/openj92/api-token"),
            allowed_hostname_pattern="cpk-gateway-*.openj92.dev",
        )


if __name__ == "__main__":
    unittest.main()
