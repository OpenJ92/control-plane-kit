from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
import os
import unittest

import psycopg

from control_plane_kit_core.algebra import BlockSockets, BlockSpec, ProviderSocket
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_core.planning import (
    ActivityId,
    ActivityPlan,
    AllocatePublicIngress,
    PlannedActivity,
    PublicIngressActivityTarget,
    RemovePublicIngress,
)
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    NamedPublicIngress,
    PublicIngressLifecycle,
    PublicIngressTarget,
)
from control_plane_kit_core.secrets import (
    SecretCustodyGrant,
    SecretCustodyReceipt,
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretResolutionGrant,
    SecretUseIntent,
)
from control_plane_kit_core.topology import (
    DeploymentGraph,
    Endpoint,
    LiteralAddress,
    Node,
    RuntimeRecord,
)
from control_plane_kit_core.types import BlockFamily, Protocol, RuntimeKind
from control_plane_kit_operations.coordinator import ActivityRealizationContext
from control_plane_kit_operations.ingress_authorities import (
    CloudflareOwnedIngressResource,
    CloudflareZoneIngressAuthority,
    GeneratedIngressSecretReference,
    GeneratedSecretPurpose,
    IngressAuthorityProviderKind,
    OwnedIngressResourceStatus,
)
from control_plane_kit_operations.ingress_realization import (
    IngressOwnedResourceCoordinates,
    IngressRealizationAdapter,
)
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityPlanStatus,
    ActivityRunRecord,
    AdmittedRun,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    GraphVersionRecord,
    RealizedGraphProjectionRecord,
    RetryIdentity,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    RegisteredSecretProvider,
    RegisteredSecretReference,
    SecretProviderKind,
)


class TrackingUnitOfWorkFactory:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.active = 0
        self.entered = 0
        self.committed = 0

    def __call__(self) -> "TrackingUnitOfWork":
        return TrackingUnitOfWork(self, PostgresUnitOfWork(self._connect))

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url)


class TrackingUnitOfWork:
    def __init__(
        self,
        factory: TrackingUnitOfWorkFactory,
        inner: PostgresUnitOfWork,
    ) -> None:
        self._factory = factory
        self._inner = inner

    @property
    def stores(self):
        return self._inner.stores

    def __enter__(self) -> "TrackingUnitOfWork":
        self._factory.entered += 1
        self._factory.active += 1
        self._inner.__enter__()
        return self

    def commit(self) -> None:
        self._factory.committed += 1
        self._inner.commit()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            self._inner.__exit__(exc_type, exc, traceback)
        finally:
            self._factory.active -= 1


@dataclass(frozen=True)
class FakeIngressAllocation:
    secret_custody_receipt: SecretCustodyReceipt
    tunnel_id: str = "tunnel-001"
    tunnel_name: str = "cpk-gateway-001"
    dns_record_id: str = "dns-001"
    hostname: str = "cpk-gateway-001.openj92.dev"
    endpoint_url: str = "https://cpk-gateway-001.openj92.dev"


class StructuralProviderFailureStage(StrEnum):
    DNS_PRE_OBSERVATION = "dns-pre-observation"
    TUNNEL_ALLOCATION = "tunnel-allocation"
    TUNNEL_CONFIGURATION = "tunnel-configuration"
    DNS_PRE_MUTATION_OBSERVATION = "dns-pre-mutation-observation"
    DNS_CREATE = "dns-create"
    DNS_RECONCILIATION = "dns-reconciliation"
    TUNNEL_TOKEN = "tunnel-token"
    SECRET_CUSTODY = "secret-custody"
    CLEANUP = "cleanup"


class StructuralProviderFailureCategory(StrEnum):
    HOSTNAME_OCCUPIED = "hostname-occupied"
    DNS_CONFLICT = "dns-conflict"
    MALFORMED_RESPONSE = "malformed-response"
    PROVIDER_STATUS = "provider-status"
    TRANSPORT = "transport"
    SECRET_CUSTODY = "secret-custody"
    CLEANUP = "cleanup"


class StructuralProviderMutationCertainty(StrEnum):
    NONE = "none"
    TUNNEL_CREATED = "tunnel-created"
    DNS_AND_TUNNEL_CREATED = "dns-and-tunnel-created"
    UNCERTAIN = "uncertain"


class StructuralProviderCleanupResult(StrEnum):
    NOT_REQUIRED = "not-required"
    COMPLETE = "complete"
    WITHHELD = "withheld"
    UNCERTAIN = "uncertain"


class StructuralUnknownProviderFailureValue(StrEnum):
    UNKNOWN = "unknown-provider-value"


@dataclass(frozen=True)
class StructuralProviderFailure:
    stage: object
    category: object
    mutation_certainty: object
    tunnel_id: object
    dns_record_id: object
    cleanup_result: object


@dataclass(frozen=True)
class HostileProviderFailure(StructuralProviderFailure):
    api_token: str
    provider_body: str


class StructuralProviderError(RuntimeError):
    def __init__(self, provider_failure: object) -> None:
        super().__init__(
            "provider failed with api_token=secret-token and raw-body-private"
        )
        self.provider_failure = provider_failure


class RecordingIngressInterpreter:
    def __init__(self, tracker: TrackingUnitOfWorkFactory) -> None:
        self.tracker = tracker
        self.create_active_counts: list[int] = []
        self.create_allocation_names: list[str] = []
        self.create_origins: list[str] = []
        self.create_authorities: list[CloudflareZoneIngressAuthority] = []
        self.create_grants: list[SecretResolutionGrant] = []
        self.create_custody_grants: list[SecretCustodyGrant] = []
        self.teardown_active_counts: list[int] = []
        self.teardown_resources: list[IngressOwnedResourceCoordinates] = []
        self.teardown_grants: list[SecretResolutionGrant] = []
        self.teardown_custody_grants: list[SecretCustodyGrant] = []
        self.fail_teardown = False
        self.return_mismatched_custody_receipt = False
        self.return_invalid_coordinates = False
        self.create_error: Exception | None = None
        self.on_create: Callable[[], None] | None = None

    def create(
        self,
        ingress: NamedPublicIngress,
        *,
        authority: CloudflareZoneIngressAuthority,
        allocation_name: str,
        origin_service_url: str,
        secret_resolution_grant: SecretResolutionGrant,
        secret_custody_grant: SecretCustodyGrant,
    ) -> FakeIngressAllocation:
        self.create_active_counts.append(self.tracker.active)
        self.create_allocation_names.append(allocation_name)
        self.create_origins.append(origin_service_url)
        self.create_authorities.append(authority)
        self.create_grants.append(secret_resolution_grant)
        self.create_custody_grants.append(secret_custody_grant)
        if self.on_create is not None:
            self.on_create()
        if self.create_error is not None:
            raise self.create_error
        receipt_reference = secret_custody_grant.reference
        if self.return_mismatched_custody_receipt:
            receipt_reference = SecretReference(
                "secret://generated/ingress/untrusted-reference"
            )
        return FakeIngressAllocation(
            secret_custody_receipt=SecretCustodyReceipt(
                custody_id=secret_custody_grant.custody_id,
                provider_registration_id=(
                    secret_custody_grant.provider_registration_id
                ),
                reference=receipt_reference,
                version_id="version-tunnel-token",
                version_number=1,
            ),
            tunnel_id=(
                "tunnel\ninvalid"
                if self.return_invalid_coordinates
                else "tunnel-001"
            ),
            tunnel_name=allocation_name,
            hostname=ingress.hostname,
        )

    def teardown(
        self,
        *,
        authority: CloudflareZoneIngressAuthority,
        resources: IngressOwnedResourceCoordinates,
        secret_resolution_grant: SecretResolutionGrant,
        secret_custody_grant: SecretCustodyGrant,
    ) -> None:
        del authority
        self.teardown_active_counts.append(self.tracker.active)
        self.teardown_resources.append(resources)
        self.teardown_grants.append(secret_resolution_grant)
        self.teardown_custody_grants.append(secret_custody_grant)
        if self.fail_teardown:
            raise RuntimeError("provider teardown failed")


class RecordingSecretUseAuthorizer:
    def __init__(self) -> None:
        self.commands: list[AuthorizeSecretUse] = []

    def authorize_resolution(
        self,
        command: AuthorizeSecretUse,
    ) -> SecretResolutionGrant:
        self.commands.append(command)
        return SecretResolutionGrant(
            authorization_id="suse_" + "a" * 64,
            workspace_id=command.workspace_id,
            reference_registration_id="sref_" + "b" * 64,
            provider_registration_id="sprov_" + "c" * 64,
            endpoint_reference=SecretProviderEndpointReference("provider-a"),
            credential_reference=SecretReference(
                "secret://bootstrap/provider-a-token"
            ),
            reference=command.reference,
            intent=command.intent,
            actor_subject=command.actor_subject,
            correlation_id=command.correlation_id,
            intent_fingerprint="d" * 64,
            operation_id=command.operation_id,
            session_id=command.session_id,
            run_id=command.run_id,
            activity_id=command.activity_id,
            effect_id=command.effect_id,
            probe_id=command.probe_id,
        )


class IngressRealizationAdapterTests(unittest.TestCase):
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
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )
        self.tracker = TrackingUnitOfWorkFactory(database_url)
        self.authorizer = RecordingSecretUseAuthorizer()
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.secret_providers.register(
                RegisteredSecretProvider(
                    registration_id="sprov-generated-ingress",
                    workspace_id="workspace-a",
                    provider_id=SecretProviderId("generated"),
                    provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
                    display_name="Generated ingress secrets",
                    endpoint_reference=SecretProviderEndpointReference(
                        "workspace-secrets"
                    ),
                    credential_reference=SecretReference(
                        "secret://bootstrap/provider-token"
                    ),
                    allowed_reference_prefixes=(
                        SecretReference("secret://generated/ingress"),
                    ),
                    allowed_intents=(
                        SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN,
                    ),
                    admitted_by="operator-a",
                    admitted_at="2026-07-28T08:00:00Z",
                )
            )
            unit_of_work.stores.ingress_authorities.register(
                workspace_id="workspace-a",
                authority_ref=IngressAuthorityReference("openj92-public-ingress"),
                authority=CloudflareZoneIngressAuthority(
                    account_id="account-openj92",
                    zone_id="zone-openj92",
                    zone_name="openj92.dev",
                    api_token_ref=SecretReference(
                        "secret://cloudflare/openj92/api-token"
                    ),
                    allowed_hostname_pattern="cpk-gateway-*.openj92.dev",
                    generated_secret_provider_registration_id=(
                        "sprov-generated-ingress"
                    ),
                    generated_secret_reference_prefix=SecretReference(
                        "secret://generated/ingress"
                    ),
                ),
                admitted_by="operator-a",
                admitted_at="2026-07-28T08:00:00Z",
            )
            unit_of_work.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> TrackingUnitOfWork:
        return self.tracker()

    def test_allocate_public_ingress_calls_provider_outside_transaction_and_records_references(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:01:00Z",
            secret_use_authorizer=self.authorizer,
        )

        outcome = adapter.execute(self.context())

        self.assertEqual(interpreter.create_active_counts, [0])
        self.assertEqual(interpreter.create_origins, ["http://gateway:8000"])
        self.assertEqual(
            interpreter.create_authorities[0].api_token_ref.reference_id,
            "secret://cloudflare/openj92/api-token",
        )
        self.assertEqual(len(interpreter.create_grants), 1)
        self.assertEqual(len(interpreter.create_custody_grants), 1)
        self.assertTrue(
            interpreter.create_custody_grants[0].permits(
                interpreter.create_custody_grants[0].reference,
                SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN,
            )
        )
        self.assertTrue(
            interpreter.create_grants[0].permits(
                SecretReference("secret://cloudflare/openj92/api-token"),
                SecretUseIntent.CLOUDFLARE_API_TOKEN,
            )
        )
        self.assertEqual(self.authorizer.commands[0].actor_subject, "worker-a")
        self.assertEqual(self.authorizer.commands[0].effect_id, "event-001")
        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(
            interpreter.create_allocation_names,
            ["cpk-gateway-001-c0303ba7369e"],
        )
        descriptor = outcome.evidence.descriptor()
        self.assertEqual(descriptor["provider_kind"], "cloudflare")
        self.assertEqual(descriptor["ingress_id"], "gateway-001")
        self.assertEqual(descriptor["runtime_id"], "docker-a")
        self.assertIs(descriptor["connector_material_recorded"], True)
        self.assertNotIn("secret://", repr(descriptor).lower())
        self.assertNotIn("eyj-cloudflare", repr(descriptor).lower())
        self.assertNotIn("bearer-value", repr(descriptor).lower())

        with self.unit_of_work() as unit_of_work:
            resource = unit_of_work.stores.ingress_resources.get_cloudflare(
                "workspace-a",
                "gateway-001",
            )
            generated = unit_of_work.stores.generated_ingress_secrets.get_by_source(
                workspace_id="workspace-a",
                purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                source_run_id="run-a",
                source_activity_id="allocate-gateway",
                source_event_id="event-001",
            )

        self.assertEqual(resource.tunnel_id, "tunnel-001")
        self.assertEqual(resource.tunnel_name, "cpk-gateway-001-c0303ba7369e")
        self.assertEqual(resource.dns_record_id, "dns-001")
        self.assertEqual(resource.hostname, "cpk-gateway-001.openj92.dev")
        self.assertEqual(resource.lifecycle, PublicIngressLifecycle.EPHEMERAL)
        self.assertEqual(descriptor["tunnel_id"], resource.tunnel_id)
        self.assertEqual(descriptor["tunnel_name"], resource.tunnel_name)
        self.assertEqual(descriptor["dns_record_id"], resource.dns_record_id)
        self.assertEqual(descriptor["hostname"], resource.hostname)
        self.assertEqual(descriptor["lifecycle"], resource.lifecycle.value)
        self.assertEqual(
            generated.secret_ref.reference_id,
            interpreter.create_custody_grants[0].reference.reference_id,
        )
        self.assertEqual(generated.provider_registration_id, "sprov-generated-ingress")
        self.assertEqual(generated.provider_version_id, "version-tunnel-token")
        self.assertEqual(generated.provider_version_number, 1)

    def test_allocate_public_ingress_projects_bounded_structural_provider_failure(
        self,
    ) -> None:
        valid = StructuralProviderFailure(
            stage=StructuralProviderFailureStage.DNS_CREATE,
            category=StructuralProviderFailureCategory.TRANSPORT,
            mutation_certainty=StructuralProviderMutationCertainty.UNCERTAIN,
            tunnel_id="tunnel-001",
            dns_record_id="dns-001",
            cleanup_result=StructuralProviderCleanupResult.WITHHELD,
        )
        positive_values = {
            "stage": tuple(StructuralProviderFailureStage),
            "category": tuple(StructuralProviderFailureCategory),
            "mutation_certainty": tuple(StructuralProviderMutationCertainty),
            "cleanup_result": tuple(StructuralProviderCleanupResult),
        }
        positive_cases = [
            *(
                (
                    f"{field_name}-{value.value}",
                    replace(valid, **{field_name: value}),
                )
                for field_name, values in positive_values.items()
                for value in values
            ),
            (
                "resource-shape-none",
                replace(valid, tunnel_id=None, dns_record_id=None),
            ),
            ("resource-shape-tunnel", replace(valid, dns_record_id=None)),
            ("resource-shape-dns", replace(valid, tunnel_id=None)),
            ("resource-shape-both", valid),
        ]

        for label, provider_failure in positive_cases:
            with self.subTest(label=label):
                interpreter = RecordingIngressInterpreter(self.tracker)
                interpreter.create_error = StructuralProviderError(provider_failure)
                adapter = IngressRealizationAdapter(
                    self.unit_of_work,
                    interpreters={
                        IngressAuthorityProviderKind.CLOUDFLARE: interpreter
                    },
                    clock=lambda: "2026-07-28T08:01:00Z",
                    secret_use_authorizer=self.authorizer,
                )

                outcome = adapter.execute(self.context())

                resources = []
                if provider_failure.tunnel_id is not None:
                    resources.append(
                        {"id": provider_failure.tunnel_id, "kind": "tunnel"}
                    )
                if provider_failure.dns_record_id is not None:
                    resources.append(
                        {"id": provider_failure.dns_record_id, "kind": "dns-record"}
                    )
                self.assertEqual(outcome.kind.name, "UNCERTAIN")
                self.assertEqual(outcome.failure.code, "ingress.allocate-uncertain")
                self.assertEqual(interpreter.create_active_counts, [0])
                self.assertEqual(interpreter.teardown_resources, [])
                self.assertEqual(
                    outcome.failure.details.descriptor(),
                    {
                        "exception_type": "StructuralProviderError",
                        "provider_cleanup_result": provider_failure.cleanup_result.value,
                        "provider_failure_category": provider_failure.category.value,
                        "provider_failure_stage": provider_failure.stage.value,
                        "provider_kind": "cloudflare",
                        "provider_mutation_certainty": (
                            provider_failure.mutation_certainty.value
                        ),
                        "provider_resources": resources,
                    },
                )
                rendered = repr(outcome.failure)
                self.assertNotIn("secret-token", rendered)
                self.assertNotIn("raw-body-private", rendered)

    def test_allocate_public_ingress_rejects_hostile_provider_failure_evidence(
        self,
    ) -> None:
        valid = StructuralProviderFailure(
            stage=StructuralProviderFailureStage.DNS_CREATE,
            category=StructuralProviderFailureCategory.TRANSPORT,
            mutation_certainty=StructuralProviderMutationCertainty.UNCERTAIN,
            tunnel_id="tunnel-001",
            dns_record_id="dns-001",
            cleanup_result=StructuralProviderCleanupResult.WITHHELD,
        )
        hostile_values = [
            (
                "mapping",
                {
                    "stage": "dns-create",
                    "api_token": "secret-token",
                },
            ),
            (
                "raw-strings",
                StructuralProviderFailure(
                    stage="dns-create",
                    category="transport",
                    mutation_certainty="uncertain",
                    tunnel_id="tunnel-001",
                    dns_record_id="dns-001",
                    cleanup_result="withheld",
                ),
            ),
            *(
                (
                    f"unknown-{field_name}",
                    replace(
                        valid,
                        **{
                            field_name: StructuralUnknownProviderFailureValue.UNKNOWN
                        },
                    ),
                )
                for field_name in (
                    "stage",
                    "category",
                    "mutation_certainty",
                    "cleanup_result",
                )
            ),
            (
                "secret-shaped-id",
                replace(valid, tunnel_id="secret://cloudflare/token"),
            ),
            ("malformed-id", replace(valid, dns_record_id="dns\n001")),
            *(
                (
                    f"{field_name}-{label}",
                    replace(valid, **{field_name: value}),
                )
                for field_name in ("tunnel_id", "dns_record_id")
                for label, value in (
                    ("empty", ""),
                    ("non-string", 7),
                    ("oversized", "d" * 129),
                )
            ),
            (
                "extra-provider-data",
                HostileProviderFailure(
                    **valid.__dict__,
                    api_token="secret-token",
                    provider_body="raw-body-private",
                ),
            ),
        ]

        for label, provider_failure in hostile_values:
            with self.subTest(label=label):
                interpreter = RecordingIngressInterpreter(self.tracker)
                interpreter.create_error = StructuralProviderError(provider_failure)
                adapter = IngressRealizationAdapter(
                    self.unit_of_work,
                    interpreters={
                        IngressAuthorityProviderKind.CLOUDFLARE: interpreter
                    },
                    clock=lambda: "2026-07-28T08:01:00Z",
                    secret_use_authorizer=self.authorizer,
                )

                outcome = adapter.execute(self.context())

                self.assertEqual(outcome.kind.name, "UNCERTAIN")
                self.assertEqual(
                    outcome.failure.details.descriptor(),
                    {"exception_type": "StructuralProviderError"},
                )
                self.assertEqual(interpreter.create_active_counts, [0])
                self.assertEqual(interpreter.teardown_resources, [])
                rendered = repr(outcome.failure)
                self.assertNotIn("secret-token", rendered)
                self.assertNotIn("raw-body-private", rendered)
                self.assertNotIn("secret://cloudflare/token", rendered)

    def test_mismatched_custody_receipt_compensates_without_admission(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        interpreter.return_mismatched_custody_receipt = True
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:01:00Z",
            secret_use_authorizer=self.authorizer,
        )

        outcome = adapter.execute(self.context())

        self.assertEqual(outcome.kind.name, "FAILED")
        self.assertEqual(len(interpreter.teardown_resources), 1)
        self.assertEqual(
            interpreter.teardown_custody_grants,
            interpreter.create_custody_grants,
        )
        with self.unit_of_work() as unit_of_work:
            resources = unit_of_work.stores.ingress_resources.list_cloudflare(
                "workspace-a"
            )
            references = unit_of_work.stores.secret_references.list_active(
                "workspace-a"
            )
        self.assertEqual(resources, ())
        self.assertEqual(references, ())

    def test_projection_failure_after_create_compensates_from_allocation_result(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        interpreter.return_invalid_coordinates = True
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:01:00Z",
            secret_use_authorizer=self.authorizer,
        )

        outcome = adapter.execute(self.context())

        self.assertEqual(outcome.kind.name, "FAILED")
        self.assertEqual(len(interpreter.teardown_resources), 1)
        allocation = interpreter.teardown_resources[0]
        self.assertEqual(allocation.tunnel_id, "tunnel\ninvalid")
        self.assertEqual(allocation.dns_record_id, "dns-001")
        self.assertEqual(interpreter.teardown_active_counts, [0])

    def test_invalid_fold_timestamp_fails_before_provider_mutation(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "invalid\ntimestamp",
            secret_use_authorizer=self.authorizer,
        )

        outcome = adapter.execute(self.context())

        self.assertEqual(outcome.kind.name, "UNSUPPORTED")
        self.assertEqual(interpreter.create_active_counts, [])
        self.assertEqual(interpreter.teardown_resources, [])

    def test_durable_fold_race_after_create_compensates_exact_allocation(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)

        def revoke_generated_secret_provider() -> None:
            with self.unit_of_work() as unit_of_work:
                unit_of_work.stores.secret_providers.revoke_active(
                    "workspace-a",
                    SecretProviderId("generated"),
                    revoked_by="concurrent-operator",
                    revoked_at="2026-07-28T08:00:59Z",
                )
                unit_of_work.commit()

        interpreter.on_create = revoke_generated_secret_provider
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:01:00Z",
            secret_use_authorizer=self.authorizer,
        )

        outcome = adapter.execute(self.context())

        self.assertEqual(outcome.kind.name, "FAILED")
        self.assertEqual(len(interpreter.teardown_resources), 1)
        self.assertEqual(interpreter.teardown_resources[0].tunnel_id, "tunnel-001")
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.ingress_resources.list_cloudflare("workspace-a"),
                (),
            )

    def test_compensation_failure_preserves_bounded_owned_coordinates(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        interpreter.return_mismatched_custody_receipt = True
        interpreter.fail_teardown = True
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:01:00Z",
            secret_use_authorizer=self.authorizer,
        )

        outcome = adapter.execute(self.context())

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assertEqual(outcome.failure.code, "ingress.compensation-uncertain")
        details = outcome.failure.details.descriptor()
        self.assertEqual(details["tunnel_id"], "tunnel-001")
        self.assertEqual(details["dns_record_id"], "dns-001")
        self.assertEqual(details["hostname"], "cpk-gateway-001.openj92.dev")
        self.assertEqual(details["fold_exception_type"], "InvalidOperationCommand")
        self.assertEqual(details["compensation_exception_type"], "RuntimeError")

    def test_allocate_public_ingress_uses_unique_tunnel_names_for_distinct_runs(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:01:00Z",
            secret_use_authorizer=self.authorizer,
        )

        first = adapter.execute(self.context())
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.ingress_resources.mark_removed(
                "workspace-a",
                "gateway-001",
                removed_at="2026-07-28T08:02:00Z",
                removed_by_run_id="run-a",
            )
            unit_of_work.commit()
        second = adapter.execute(
            self.context(run_id="run-b", intent_event_id="event-002")
        )

        self.assertEqual(first.kind.name, "SUCCEEDED")
        self.assertEqual(second.kind.name, "SUCCEEDED")
        self.assertEqual(len(interpreter.create_allocation_names), 2)
        self.assertNotEqual(
            interpreter.create_allocation_names[0],
            interpreter.create_allocation_names[1],
        )
        for allocation_name in interpreter.create_allocation_names:
            self.assertRegex(allocation_name, r"^cpk-gateway-001-[0-9a-f]{12}$")

    def test_remove_public_ingress_marks_resource_removed_around_provider_io(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:02:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_existing_ingress(unit_of_work)
            unit_of_work.commit()

        outcome = adapter.execute(
            self.context(
                activity_id="remove-gateway",
                operation=RemovePublicIngress(PublicIngressActivityTarget("gateway-001")),
                base_graph=self.graph(),
                desired_graph=DeploymentGraph("empty"),
            )
        )

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(interpreter.teardown_active_counts, [0])
        self.assertEqual(len(interpreter.teardown_grants), 1)
        self.assertTrue(
            interpreter.teardown_grants[0].permits(
                SecretReference("secret://cloudflare/openj92/api-token"),
                SecretUseIntent.CLOUDFLARE_API_TOKEN,
            )
        )
        self.assertEqual(interpreter.teardown_resources[0].status.name, "REMOVING")
        with self.unit_of_work() as unit_of_work:
            history = unit_of_work.stores.ingress_resources.list_cloudflare(
                "workspace-a"
            )
        self.assertEqual(history[0].status, OwnedIngressResourceStatus.REMOVED)
        self.assertEqual(history[0].removed_at, "2026-07-28T08:02:00Z")
        self.assertEqual(history[0].removed_by_run_id, "run-a")

    def test_remove_public_ingress_marks_uncertain_when_provider_fails(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        interpreter.fail_teardown = True
        clock_calls = 0

        def forbidden_clock() -> str:
            nonlocal clock_calls
            clock_calls += 1
            raise AssertionError("removal clock must follow successful teardown")

        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=forbidden_clock,
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_existing_ingress(unit_of_work)
            unit_of_work.commit()

        outcome = adapter.execute(
            self.context(
                activity_id="remove-gateway",
                operation=RemovePublicIngress(PublicIngressActivityTarget("gateway-001")),
                base_graph=self.graph(),
                desired_graph=DeploymentGraph("empty"),
            )
        )

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assertEqual(clock_calls, 0)
        self.assertEqual(interpreter.teardown_active_counts, [0])
        with self.unit_of_work() as unit_of_work:
            resource = unit_of_work.stores.ingress_resources.get_cloudflare(
                "workspace-a",
                "gateway-001",
            )
        self.assertEqual(resource.status, OwnedIngressResourceStatus.UNCERTAIN)

    def test_remove_public_ingress_malformed_post_teardown_clock_is_uncertain(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        clock_entry_counts: list[int] = []

        def malformed_clock() -> str:
            clock_entry_counts.append(self.tracker.entered)
            return "not-a-timestamp"

        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=malformed_clock,
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_existing_ingress(unit_of_work)
            unit_of_work.commit()

        outcome = adapter.execute(
            self.context(
                activity_id="remove-gateway",
                operation=RemovePublicIngress(PublicIngressActivityTarget("gateway-001")),
                base_graph=self.graph(),
                desired_graph=DeploymentGraph("empty"),
            )
        )

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assertEqual(outcome.failure.code, "ingress.remove-uncertain")
        self.assertEqual(clock_entry_counts, [self.tracker.entered])
        self.assertEqual(interpreter.teardown_active_counts, [0])
        with self.unit_of_work() as unit_of_work:
            resource = unit_of_work.stores.ingress_resources.get_cloudflare(
                "workspace-a",
                "gateway-001",
            )
        self.assertEqual(resource.status, OwnedIngressResourceStatus.REMOVING)
        self.assertIsNone(resource.removed_at)
        self.assertIsNone(resource.removed_by_run_id)
        rendered = repr(outcome.failure)
        for excluded in (
            "not-a-timestamp",
            "cpk-gateway-001.openj92.dev",
            "tunnel-001",
            "dns-001",
            "secret://",
        ):
            self.assertNotIn(excluded, rendered)

    def test_allocate_public_ingress_rejects_noncanonical_clock_before_provider(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T04:01:00-04:00",
            secret_use_authorizer=self.authorizer,
        )

        outcome = adapter.execute(self.context())

        self.assertEqual(outcome.kind.name, "UNSUPPORTED")
        self.assertEqual(outcome.failure.code, "ingress.allocate-unsupported")
        self.assertEqual(interpreter.create_active_counts, [])
        self.assertEqual(interpreter.create_allocation_names, [])

    def graph(self) -> DeploymentGraph:
        return DeploymentGraph(
            "ingress-test",
            nodes={
                "gateway": Node(
                    node_id="gateway",
                    block_family=BlockFamily.PROXY,
                    block_spec=BlockSpec("gateway"),
                    kind="container",
                    runtime_id="docker-a",
                    sockets=BlockSockets(
                        providers=(ProviderSocket("control", Protocol.HTTP),)
                    ),
                    endpoints={
                        "control": Endpoint(
                            LiteralAddress("http://gateway:8000"),
                            Protocol.HTTP,
                        )
                    },
                ),
                "cloudflared": Node(
                    node_id="cloudflared",
                    block_family=BlockFamily.PROXY,
                    block_spec=BlockSpec("cloudflared"),
                    kind="container",
                    runtime_id="docker-a",
                    sockets=BlockSockets(),
                ),
            },
            runtimes={
                "docker-a": RuntimeRecord(
                    "docker-a",
                    RuntimeKind.DOCKER,
                    children=("gateway", "cloudflared"),
                )
            },
            public_ingresses=(
                NamedPublicIngress(
                    ingress_id="gateway-001",
                    authority_ref=IngressAuthorityReference("openj92-public-ingress"),
                    target=PublicIngressTarget("gateway", "control"),
                    connector_node_id="cloudflared",
                    hostname="cpk-gateway-001.openj92.dev",
                ),
            ),
        )

    def cloudflare_resource(self) -> CloudflareOwnedIngressResource:
        return CloudflareOwnedIngressResource(
            workspace_id="workspace-a",
            runtime_id="docker-a",
            ingress_id="gateway-001",
            authority_ref=IngressAuthorityReference("openj92-public-ingress"),
            provider_kind=IngressAuthorityProviderKind.CLOUDFLARE,
            tunnel_name="cpk-gateway-001",
            tunnel_id="tunnel-001",
            dns_record_id="dns-001",
            hostname="cpk-gateway-001.openj92.dev",
            zone_id="zone-openj92",
            lifecycle=self.graph().public_ingresses[0].lifecycle,
            created_at="2026-07-28T08:01:00Z",
            observed_at="2026-07-28T08:01:00Z",
            source_run_id="run-a",
            source_activity_id="allocate-gateway",
            source_event_id="event-001",
        )

    def record_existing_ingress(self, unit_of_work: TrackingUnitOfWork) -> None:
        reference = SecretReference(
            "secret://generated/ingress/cloudflared-tunnel-token/existing"
        )
        registered = unit_of_work.stores.secret_references.register(
            RegisteredSecretReference(
                registration_id="sref-existing-ingress",
                workspace_id="workspace-a",
                reference=reference,
                provider_registration_id="sprov-generated-ingress",
                allowed_intents=(SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN,),
                admitted_by="worker-a",
                admitted_at="2026-07-28T08:01:00Z",
            )
        )
        unit_of_work.stores.ingress_resources.record_cloudflare(
            self.cloudflare_resource()
        )
        unit_of_work.stores.generated_ingress_secrets.record(
            GeneratedIngressSecretReference(
                workspace_id="workspace-a",
                purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                secret_ref=reference,
                provider_registration_id="sprov-generated-ingress",
                reference_registration_id=registered.registration_id,
                custody_id="scust-existing-ingress",
                provider_version_id="version-existing-ingress",
                provider_version_number=1,
                recorded_at="2026-07-28T08:01:00Z",
                source_run_id="run-a",
                source_activity_id="allocate-gateway",
                source_event_id="event-001",
            )
        )

    def context(
        self,
        *,
        activity_id: str = "allocate-gateway",
        run_id: str = "run-a",
        intent_event_id: str = "event-001",
        operation: object | None = None,
        base_graph: DeploymentGraph | None = None,
        desired_graph: DeploymentGraph | None = None,
    ) -> ActivityRealizationContext:
        graph = self.graph()
        operation = operation or AllocatePublicIngress(
            PublicIngressActivityTarget("gateway-001")
        )
        activity = PlannedActivity(
            ActivityId(activity_id),
            operation,
        )
        return ActivityRealizationContext(
            activity=activity,
            request=ExecutionRequestRecord(
                ExecutionRequestIdentity(
                    "request-a",
                    "workspace-a",
                    "session-a",
                    "plan-a",
                ),
                ExecutionRequestStatus.CLAIMED,
                "operator-a",
                "2026-07-28T08:00:00Z",
                "approval-request-a",
                "approval-decision-a",
                ExecutionIdempotency("execute-a", "fingerprint-a"),
                claim=ClaimIdentity(
                    "worker-a",
                    1,
                    "2026-07-28T08:00:30Z",
                    "2026-07-28T08:10:30Z",
                ),
            ),
            run=ActivityRunRecord(
                run_id,
                "plan-a",
                AdmittedRun("request-a"),
                RetryIdentity(1),
                ActivityRunStatus.RUNNING,
                "2026-07-28T08:00:45Z",
                started_at="2026-07-28T08:00:50Z",
            ),
            plan_record=ActivityPlanRecord(
                "plan-a",
                "session-a",
                "graph-current",
                "graph-desired",
                ActivityPlanStatus.PLANNED,
                "2026-07-28T08:00:10Z",
                ActivityPlan((activity,)),
            ),
            base_graph=RealizedGraphProjectionRecord.identity_for_authored(
                authored_record=GraphVersionRecord.from_graph(
                    graph_id="graph-current",
                    workspace_id="workspace-a",
                    version=1,
                    graph=base_graph or DeploymentGraph("empty"),
                    created_by="operator-a",
                    created_at="2026-07-28T08:00:00Z",
                )
            ),
            desired_graph=RealizedGraphProjectionRecord.identity_for_authored(
                authored_record=GraphVersionRecord.from_graph(
                    graph_id="graph-desired",
                    workspace_id="workspace-a",
                    version=2,
                    graph=desired_graph or graph,
                    created_by="operator-a",
                    created_at="2026-07-28T08:00:05Z",
                )
            ),
            registered_products=(),
            authority=ExecutionWorkerAuthority(
                "worker-a",
                (
                    PolicyScope.EXECUTION_OPERATE,
                    PolicyScope.SECRET_PROVIDER_USE,
                ),
            ),
            fence=ExecutionLeaseFence("worker-a", 1),
            intent_event=ActivityEventRecord(
                intent_event_id,
                run_id,
                1,
                ActivityEventKind.STEP_STARTED,
                "2026-07-28T08:01:00Z",
                activity_id=activity_id,
            ),
        )


if __name__ == "__main__":
    unittest.main()
