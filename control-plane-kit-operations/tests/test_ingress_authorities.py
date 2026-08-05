from __future__ import annotations

import os
import unittest

import psycopg

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    PublicIngressLifecycle,
)
from control_plane_kit_core.secrets import SecretCustodyReceipt, SecretReference
from control_plane_kit_operations.ingress_authorities import (
    CloudflareIngressTeardownActionKind,
    CloudflareOwnedIngressResource,
    CloudflareTunnelTokenDeliveryStep,
    CloudflareZoneIngressAuthority,
    GeneratedSecretPurpose,
    GeneratedSecretRecordingConflict,
    IngressAuthorityAuthorizationDenied,
    IngressAuthorityNotFound,
    IngressAuthorityProviderKind,
    IngressAuthorityRegistrationConflict,
    IngressAuthorityRegistrationService,
    IngressAuthorityRegistrationError,
    OwnedIngressResourceStatus,
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

            generated_secret_provider_registration_id="sprov-generated-ingress",

            generated_secret_reference_prefix=SecretReference("secret://generated/ingress"),
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

                generated_secret_provider_registration_id="sprov-generated-ingress",

                generated_secret_reference_prefix=SecretReference("secret://generated/ingress"),
            )
        with self.assertRaisesRegex(ValueError, "zone"):
            CloudflareZoneIngressAuthority(
                account_id="account-openj92",
                zone_id="zone-openj92",
                zone_name="openj92.dev",
                api_token_ref=SecretReference("secret://cloudflare/openj92/api-token"),
                allowed_hostname_pattern="*.example.com",

                generated_secret_provider_registration_id="sprov-generated-ingress",

                generated_secret_reference_prefix=SecretReference("secret://generated/ingress"),
            )
        authority = CloudflareZoneIngressAuthority(
            account_id="account-openj92",
            zone_id="zone-openj92",
            zone_name="openj92.dev",
            api_token_ref=SecretReference("secret://cloudflare/openj92/api-token"),
            allowed_hostname_pattern="cpk-token-*.openj92.dev",
            generated_secret_provider_registration_id="sprov-generated-ingress",
            generated_secret_reference_prefix=SecretReference("secret://generated/ingress"),
        )
        self.assertTrue(authority.allows_hostname("cpk-token-001.openj92.dev"))

    def test_cloudflare_owned_resource_evidence_is_bounded_and_secret_free(self) -> None:
        resource = self.cloudflare_resource()

        descriptor = resource.descriptor()

        self.assertEqual(descriptor["workspace_id"], "workspace-a")
        self.assertEqual(descriptor["runtime_id"], "docker-a")
        self.assertEqual(descriptor["tunnel_id"], "tunnel-001")
        self.assertEqual(descriptor["dns_record_id"], "dns-001")
        self.assertEqual(descriptor["hostname"], "cpk-gateway-001.openj92.dev")
        self.assertEqual(descriptor["epoch"], 1)
        self.assertEqual(descriptor["status"], "active")
        self.assertEqual(descriptor["lifecycle"], "ephemeral")
        self.assertNotIn("cf_api_token", repr(descriptor).lower())
        self.assertNotIn("bearer", repr(descriptor).lower())
        self.assertNotIn("eyj", repr(descriptor).lower())

    def test_cloudflare_owned_resource_accepts_benign_secret_shaped_identifiers(
        self,
    ) -> None:
        resource = self.cloudflare_resource(
            workspace_id="workspace-secret-operations",
            runtime_id="runtime-token-worker",
            ingress_id="credential-probe",
            tunnel_id="tunnel-token-evidence",
        )

        descriptor = resource.descriptor()

        self.assertEqual(descriptor["workspace_id"], "workspace-secret-operations")
        self.assertEqual(descriptor["runtime_id"], "runtime-token-worker")
        self.assertEqual(descriptor["ingress_id"], "credential-probe")
        self.assertEqual(descriptor["tunnel_id"], "tunnel-token-evidence")
        self.assertNotIn("api_token", descriptor)
        self.assertNotIn("credential", descriptor)

    def test_owned_ingress_resource_status_is_operations_lifecycle_truth(
        self,
    ) -> None:
        self.assertNotIsInstance(
            OwnedIngressResourceStatus.ACTIVE,
            PublicIngressLifecycle,
        )
        resource = self.cloudflare_resource(
            epoch=2,
            status=OwnedIngressResourceStatus.REMOVED,
            removed_at="removed-at",
            removed_by_run_id="run-002",
        )

        descriptor = resource.descriptor()
        self.assertEqual(descriptor["epoch"], 2)
        self.assertEqual(descriptor["status"], "removed")
        self.assertEqual(descriptor["lifecycle"], "ephemeral")
        self.assertEqual(descriptor["removed_at"], "removed-at")
        self.assertEqual(descriptor["removed_by_run_id"], "run-002")

        with self.assertRaisesRegex(
            IngressAuthorityRegistrationError,
            "removal evidence",
        ):
            self.cloudflare_resource(status=OwnedIngressResourceStatus.REMOVED)
        with self.assertRaisesRegex(
            IngressAuthorityRegistrationError,
            "only removed",
        ):
            self.cloudflare_resource(
                status=OwnedIngressResourceStatus.ACTIVE,
                removed_at="removed-at",
                removed_by_run_id="run-002",
            )

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
                "intent": "cloudflare.tunnel-token",
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

    def test_generated_tunnel_token_receipt_returns_reference_only_evidence(
        self,
    ) -> None:
        reference = SecretReference(
            "secret://generated/ingress/cloudflared-tunnel-token/token-001"
        )

        evidence = record_generated_ingress_secret(
            workspace_id="workspace-a",
            purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
            receipt=SecretCustodyReceipt(
                custody_id="scust-generated-ingress",
                provider_registration_id="sprov-generated-ingress",
                reference=reference,
                version_id="version-generated-ingress",
                version_number=1,
            ),
            reference_registration_id="sref-generated-ingress",
            source_run_id="run-001",
            source_activity_id="activity-001",
            source_event_id="event-001",
            recorded_at="2026-07-28T07:00:00Z",
        )
        descriptor = evidence.descriptor()

        self.assertEqual(
            evidence.secret_ref,
            reference,
        )
        self.assertEqual(evidence.provider_version_id, "version-generated-ingress")
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

            generated_secret_provider_registration_id="sprov-generated-ingress",

            generated_secret_reference_prefix=SecretReference("secret://generated/ingress"),
        )

    def cloudflare_resource(
        self,
        *,
        workspace_id: str = "workspace-a",
        runtime_id: str = "docker-a",
        lifecycle: PublicIngressLifecycle = PublicIngressLifecycle.EPHEMERAL,
        ingress_id: str = "gateway-001",
        epoch: int = 1,
        status: OwnedIngressResourceStatus = OwnedIngressResourceStatus.ACTIVE,
        zone_id: str = "zone-openj92",
        hostname: str = "cpk-gateway-001.openj92.dev",
        tunnel_name: str = "cpk-gateway-001",
        tunnel_id: str = "tunnel-001",
        source_run_id: str = "run-001",
        source_activity_id: str = "activity-001",
        source_event_id: str = "event-001",
        reservation_id: str | None = None,
        removed_at: str | None = None,
        removed_by_run_id: str | None = None,
    ) -> CloudflareOwnedIngressResource:
        if lifecycle is PublicIngressLifecycle.RETAINED and reservation_id is None:
            reservation_id = "reservation-001"
        return CloudflareOwnedIngressResource(
            workspace_id=workspace_id,
            runtime_id=runtime_id,
            ingress_id=ingress_id,
            reservation_id=reservation_id,
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
            source_run_id=source_run_id,
            source_activity_id=source_activity_id,
            source_event_id=source_event_id,
            epoch=epoch,
            status=status,
            removed_at=removed_at,
            removed_by_run_id=removed_by_run_id,
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

    def test_removed_cloudflare_resource_permits_new_epoch_without_losing_history(
        self,
    ) -> None:
        resource = IngressAuthorityValueTests().cloudflare_resource()
        with self.unit_of_work() as unit_of_work:
            recorded = unit_of_work.stores.ingress_resources.record_cloudflare(
                resource
            )
            removed = unit_of_work.stores.ingress_resources.mark_removed(
                "workspace-a",
                "gateway-001",
                removed_at="removed-at",
                removed_by_run_id="run-002",
            )
            reallocated = unit_of_work.stores.ingress_resources.record_cloudflare(
                IngressAuthorityValueTests().cloudflare_resource(
                    tunnel_id="tunnel-002"
                )
            )
            unit_of_work.commit()

        self.assertEqual(recorded.epoch, 1)
        self.assertEqual(removed.epoch, 1)
        self.assertEqual(removed.status, OwnedIngressResourceStatus.REMOVED)
        self.assertEqual(reallocated.epoch, 2)
        self.assertEqual(reallocated.status, OwnedIngressResourceStatus.ACTIVE)

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.ingress_resources.get_cloudflare(
                    "workspace-a",
                    "gateway-001",
                ),
                reallocated,
            )
            history = unit_of_work.stores.ingress_resources.list_cloudflare(
                "workspace-a"
            )

        self.assertEqual(history, (removed, reallocated))
        self.assertNotIn("cf_api_token", repr(history).lower())
        self.assertNotIn("bearer", repr(history).lower())
        self.assertNotIn("eyj", repr(history).lower())

    def test_uncertain_and_orphaned_cloudflare_resources_block_reentry(
        self,
    ) -> None:
        for status in (
            OwnedIngressResourceStatus.UNCERTAIN,
            OwnedIngressResourceStatus.ORPHANED,
        ):
            with self.subTest(status=status):
                with self.unit_of_work() as unit_of_work:
                    resource = unit_of_work.stores.ingress_resources.record_cloudflare(
                        IngressAuthorityValueTests().cloudflare_resource(
                            ingress_id=f"gateway-{status.value}",
                            status=status,
                            tunnel_id=f"tunnel-{status.value}",
                        )
                    )
                    unit_of_work.commit()

                with self.unit_of_work() as unit_of_work:
                    self.assertEqual(
                        unit_of_work.stores.ingress_resources.get_cloudflare(
                            "workspace-a",
                            f"gateway-{status.value}",
                        ),
                        resource,
                    )
                    with self.assertRaisesRegex(
                        OwnedIngressResourceConflict,
                        "replacement",
                    ):
                        unit_of_work.stores.ingress_resources.record_cloudflare(
                            IngressAuthorityValueTests().cloudflare_resource(
                                ingress_id=f"gateway-{status.value}",
                                tunnel_id="tunnel-reentry"
                            )
                        )

    def test_cloudflare_resource_transition_methods_preserve_epoch(
        self,
    ) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.ingress_resources.record_cloudflare(
                IngressAuthorityValueTests().cloudflare_resource()
            )
            removing = unit_of_work.stores.ingress_resources.mark_removing(
                "workspace-a",
                "gateway-001",
            )
            removed = unit_of_work.stores.ingress_resources.mark_removed(
                "workspace-a",
                "gateway-001",
                removed_at="removed-at",
                removed_by_run_id="run-003",
            )
            unit_of_work.commit()

        self.assertEqual(removing.epoch, 1)
        self.assertEqual(removing.status, OwnedIngressResourceStatus.REMOVING)
        self.assertEqual(removing.source_run_id, "run-001")
        self.assertEqual(removing.source_activity_id, "activity-001")
        self.assertEqual(removing.source_event_id, "event-001")
        self.assertEqual(removed.epoch, 1)
        self.assertEqual(removed.status, OwnedIngressResourceStatus.REMOVED)
        self.assertEqual(removed.source_run_id, "run-001")
        self.assertEqual(removed.source_activity_id, "activity-001")
        self.assertEqual(removed.source_event_id, "event-001")
        self.assertEqual(removed.removed_by_run_id, "run-003")

    def test_uncertain_cloudflare_resource_preserves_allocation_provenance(
        self,
    ) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.ingress_resources.record_cloudflare(
                IngressAuthorityValueTests().cloudflare_resource()
            )
            unit_of_work.stores.ingress_resources.mark_removing(
                "workspace-a",
                "gateway-001",
            )
            uncertain = unit_of_work.stores.ingress_resources.mark_uncertain(
                "workspace-a",
                "gateway-001",
            )
            unit_of_work.commit()

        self.assertEqual(uncertain.status, OwnedIngressResourceStatus.UNCERTAIN)
        self.assertEqual(uncertain.source_run_id, "run-001")
        self.assertEqual(uncertain.source_activity_id, "activity-001")
        self.assertEqual(uncertain.source_event_id, "event-001")

    def test_removed_cloudflare_resource_keeps_generated_secret_joinable(
        self,
    ) -> None:
        first_secret = record_generated_ingress_secret(
            workspace_id="workspace-a",
            purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
            receipt=SecretCustodyReceipt(
                custody_id="scust-allocation",
                provider_registration_id="sprov-generated-ingress",
                reference=SecretReference(
                    "secret://generated/ingress/cloudflared-tunnel-token/token-001"
                ),
                version_id="version-allocation",
                version_number=1,
            ),
            reference_registration_id="sref-allocation",
            source_run_id="run-001",
            source_activity_id="activity-001",
            source_event_id="event-001",
            recorded_at="2026-08-05T13:00:00Z",
        )
        second_secret = record_generated_ingress_secret(
            workspace_id="workspace-a",
            purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
            receipt=SecretCustodyReceipt(
                custody_id="scust-reallocation",
                provider_registration_id="sprov-generated-ingress",
                reference=SecretReference(
                    "secret://generated/ingress/cloudflared-tunnel-token/token-002"
                ),
                version_id="version-reallocation",
                version_number=2,
            ),
            reference_registration_id="sref-reallocation",
            source_run_id="run-002",
            source_activity_id="activity-002",
            source_event_id="event-002",
            recorded_at="2026-08-05T13:05:00Z",
        )

        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.ingress_resources.record_cloudflare(
                IngressAuthorityValueTests().cloudflare_resource()
            )
            unit_of_work.stores.generated_ingress_secrets.record(first_secret)
            unit_of_work.stores.ingress_resources.mark_removing(
                "workspace-a",
                "gateway-001",
            )
            removed = unit_of_work.stores.ingress_resources.mark_removed(
                "workspace-a",
                "gateway-001",
                removed_at="removed-at",
                removed_by_run_id="run-remove",
            )
            reallocated = unit_of_work.stores.ingress_resources.record_cloudflare(
                IngressAuthorityValueTests().cloudflare_resource(
                    tunnel_id="tunnel-002",
                    source_run_id="run-002",
                    source_activity_id="activity-002",
                    source_event_id="event-002",
                )
            )
            unit_of_work.stores.generated_ingress_secrets.record(second_secret)
            unit_of_work.commit()

        with self.unit_of_work() as unit_of_work:
            joined_first_secret = (
                unit_of_work.stores.generated_ingress_secrets.get_by_source(
                    workspace_id=removed.workspace_id,
                    purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                    source_run_id=removed.source_run_id,
                    source_activity_id=removed.source_activity_id,
                    source_event_id=removed.source_event_id,
                )
            )
            joined_second_secret = (
                unit_of_work.stores.generated_ingress_secrets.get_by_source(
                    workspace_id=reallocated.workspace_id,
                    purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                    source_run_id=reallocated.source_run_id,
                    source_activity_id=reallocated.source_activity_id,
                    source_event_id=reallocated.source_event_id,
                )
            )

        self.assertEqual(removed.epoch, 1)
        self.assertEqual(reallocated.epoch, 2)
        self.assertEqual(joined_first_secret, first_secret)
        self.assertEqual(joined_second_secret, second_secret)

    def test_generated_ingress_secret_reference_is_durable_and_secret_free(
        self,
    ) -> None:
        reference = SecretReference(
            "secret://generated/ingress/cloudflared-tunnel-token/token-001"
        )
        evidence = record_generated_ingress_secret(
            workspace_id="workspace-a",
            purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
            receipt=SecretCustodyReceipt(
                custody_id="scust-generated-ingress",
                provider_registration_id="sprov-generated-ingress",
                reference=reference,
                version_id="version-generated-ingress",
                version_number=1,
            ),
            reference_registration_id="sref-generated-ingress",
            source_run_id="run-001",
            source_activity_id="activity-001",
            source_event_id="event-001",
            recorded_at="2026-07-28T07:00:00Z",
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
                        workspace_id="workspace-a",
                        purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                        receipt=SecretCustodyReceipt(
                            custody_id="scust-generated-alternate",
                            provider_registration_id="sprov-generated-ingress",
                            reference=SecretReference(
                                "secret://generated/ingress/alternate/token-001"
                            ),
                            version_id="version-generated-alternate",
                            version_number=1,
                        ),
                        reference_registration_id="sref-generated-alternate",
                        source_run_id="run-001",
                        source_activity_id="activity-001",
                        source_event_id="event-001",
                        recorded_at="2026-07-28T07:00:00Z",
                    )
                )

        descriptor = recorded.descriptor()
        self.assertEqual(
            descriptor["secret_ref"],
            reference.reference_id,
        )
        self.assertNotIn("eyj-cloudflare", repr(descriptor).lower())
        self.assertNotIn("bearer-value", repr(descriptor).lower())

    def test_generated_ingress_secret_reference_encodes_activity_ids(
        self,
    ) -> None:
        reference = SecretReference(
            "secret://generated/ingress/cloudflared-tunnel-token/token-activity"
        )

        evidence = record_generated_ingress_secret(
            workspace_id="workspace-a",
            purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
            receipt=SecretCustodyReceipt(
                custody_id="scust-generated-ingress",
                provider_registration_id="sprov-generated-ingress",
                reference=reference,
                version_id="version-generated-ingress",
                version_number=1,
            ),
            reference_registration_id="sref-generated-ingress",
            source_run_id="run-001",
            source_activity_id="allocate-public-ingress:9800f8498edba0a5",
            source_event_id="event-001",
            recorded_at="2026-07-28T07:00:00Z",
        )

        self.assertEqual(evidence.secret_ref, reference)
        self.assertEqual(
            evidence.source_activity_id,
            "allocate-public-ingress:9800f8498edba0a5",
        )
        self.assertEqual(
            evidence.descriptor()["source_activity_id"],
            "allocate-public-ingress:9800f8498edba0a5",
        )
        self.assertNotIn("cf_api_token", repr(evidence.descriptor()).lower())

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

            generated_secret_provider_registration_id="sprov-generated-ingress",

            generated_secret_reference_prefix=SecretReference("secret://generated/ingress"),
        )


if __name__ == "__main__":
    unittest.main()
