from __future__ import annotations

import unittest

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    PublicIngressLifecycle,
)
from control_plane_kit_core.secrets import SecretReference, SecretUseIntent
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotationDeploymentCheckpoint,
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationDeploymentStatus,
    GatewayKeyRotationError,
)
from control_plane_kit_operations.ingress_authorities import (
    CloudflareOwnedIngressResource,
    GeneratedIngressSecretReference,
    GeneratedSecretPurpose,
    IngressAuthorityProviderKind,
    IngressAuthorityRegistrationError,
    OwnedIngressResourceStatus,
)
from control_plane_kit_operations.postgres.ingress_authority_store import (
    GeneratedIngressSecretReferenceStore,
    IngressResourceStore,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    AuthorizedSecretUse,
    SecretProviderRegistrationError,
    secret_custody_correlation_for,
    secret_use_correlation_for,
)


class _RunText(str):
    pass


class _DatabaseTouched(RuntimeError):
    pass


class _FailOnDatabaseAccess:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def execute(self, query: object, parameters: object = ()) -> object:
        self.calls.append((query, parameters))
        raise _DatabaseTouched("database access occurred")


class RunProvenanceAuthorityIdentityTests(unittest.TestCase):
    def test_all_direct_carriers_share_the_canonical_run_language(self) -> None:
        carriers = (
            (
                "secret command",
                lambda value: self.authorize_secret_use(value).run_id,
                SecretProviderRegistrationError,
            ),
            (
                "secret evidence",
                lambda value: self.authorized_secret_use(value).run_id,
                SecretProviderRegistrationError,
            ),
            (
                "ingress source",
                lambda value: self.ingress_resource(source_run_id=value).source_run_id,
                IngressAuthorityRegistrationError,
            ),
            (
                "ingress removal",
                lambda value: self.ingress_resource(
                    status=OwnedIngressResourceStatus.REMOVED,
                    removed_at="2026-08-14T04:00:00Z",
                    removed_by_run_id=value,
                ).removed_by_run_id,
                IngressAuthorityRegistrationError,
            ),
            (
                "generated secret source",
                lambda value: self.generated_secret(source_run_id=value).source_run_id,
                IngressAuthorityRegistrationError,
            ),
            (
                "gateway checkpoint",
                lambda value: self.gateway_checkpoint(run_id=value).run_id,
                GatewayKeyRotationError,
            ),
        )
        for valid in ("r", "r" * 200):
            for name, construct, _error_type in carriers:
                with self.subTest(name=name, valid_length=len(valid)):
                    self.assertEqual(construct(valid), valid)
        invalid_values = (
            "run/bad",
            _RunText("run-a"),
            object(),
            True,
            "",
            " run-a",
            ".run-a",
            "run a",
            "run\na",
            "r" * 201,
        )
        for name, construct, error_type in carriers:
            with self.subTest(name=name):
                for invalid in invalid_values:
                    self.assert_owner_rejection(
                        lambda invalid=invalid: construct(invalid),
                        error_type,
                        invalid,
                    )

    def test_optional_secret_run_identity_preserves_absence(self) -> None:
        command = self.authorize_secret_use(None)
        evidence = self.authorized_secret_use(None)

        self.assertIsNone(command.run_id)
        self.assertIsNone(evidence.run_id)
        self.assertIsNone(evidence.descriptor()["run_id"])

    def test_both_secret_correlations_admit_run_identity_before_hashing(self) -> None:
        functions = (
            lambda run_id: secret_use_correlation_for(
                workspace_id="workspace-a",
                reference=SecretReference("secret://workspace-secrets/key-a"),
                intent=SecretUseIntent.APPLICATION_CONTROL_TOKEN,
                actor_subject="operator-a",
                run_id=run_id,
            ),
            lambda run_id: secret_custody_correlation_for(
                workspace_id="workspace-a",
                provider_registration_id="provider-a",
                reference=SecretReference("secret://workspace-secrets/key-a"),
                intent=SecretUseIntent.APPLICATION_CONTROL_TOKEN,
                actor_subject="operator-a",
                run_id=run_id,
            ),
        )
        for index, correlation_for in enumerate(functions):
            with self.subTest(index=index):
                self.assertEqual(correlation_for(None), correlation_for(None))
                self.assertEqual(correlation_for("run-a"), correlation_for("run-a"))
                self.assert_owner_rejection(
                    lambda: correlation_for("run/bad"),
                    SecretProviderRegistrationError,
                    "run/bad",
                )
                self.assert_owner_rejection(
                    lambda: correlation_for(_RunText("run-a")),
                    SecretProviderRegistrationError,
                    "run-a",
                )

    def test_ingress_transition_inputs_reject_before_database_access(self) -> None:
        cases = (
            lambda store: store.mark_removing(
                "workspace-a", "ingress-a", source_run_id="run/bad"
            ),
            lambda store: store.mark_removed(
                "workspace-a",
                "ingress-a",
                removed_at="2026-08-14T04:00:00Z",
                removed_by_run_id="run/bad",
            ),
            lambda store: store.mark_uncertain(
                "workspace-a", "ingress-a", source_run_id="run/bad"
            ),
        )
        for index, invoke in enumerate(cases):
            connection = _FailOnDatabaseAccess()
            store = IngressResourceStore(connection)  # type: ignore[arg-type]
            with self.subTest(index=index):
                error = self.capture_error(lambda: invoke(store))
                self.assertIs(type(error), IngressAuthorityRegistrationError)
                self.assertEqual(connection.calls, [])

    def test_generated_secret_source_lookup_rejects_before_database_access(self) -> None:
        connection = _FailOnDatabaseAccess()
        store = GeneratedIngressSecretReferenceStore(connection)  # type: ignore[arg-type]

        error = self.capture_error(
            lambda: store.get_by_source(
                workspace_id="workspace-a",
                purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                source_run_id="run/bad",
                source_activity_id="activity-a",
                source_event_id="event-a",
            )
        )

        self.assertIs(type(error), IngressAuthorityRegistrationError)
        self.assertEqual(connection.calls, [])

    def test_generated_secret_idempotency_lookup_rejects_before_database_access(
        self,
    ) -> None:
        connection = _FailOnDatabaseAccess()
        store = GeneratedIngressSecretReferenceStore(connection)  # type: ignore[arg-type]
        evidence = self.generated_secret(source_run_id="run-a")
        object.__setattr__(evidence, "source_run_id", "run/bad")

        error = self.capture_error(lambda: store.record(evidence))

        self.assertIs(type(error), IngressAuthorityRegistrationError)
        self.assertEqual(connection.calls, [])

    def test_valid_store_inputs_leave_driver_failures_untranslated(self) -> None:
        connection = _FailOnDatabaseAccess()
        ingress = IngressResourceStore(connection)  # type: ignore[arg-type]
        generated = GeneratedIngressSecretReferenceStore(connection)  # type: ignore[arg-type]
        calls = (
            lambda: ingress.mark_removing(
                "workspace-a", "ingress-a", source_run_id="run-a"
            ),
            lambda: ingress.mark_removed(
                "workspace-a",
                "ingress-a",
                removed_at="2026-08-14T04:00:00Z",
                removed_by_run_id="run-a",
            ),
            lambda: ingress.mark_uncertain(
                "workspace-a", "ingress-a", source_run_id="run-a"
            ),
            lambda: generated.get_by_source(
                workspace_id="workspace-a",
                purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                source_run_id="run-a",
                source_activity_id="activity-a",
                source_event_id="event-a",
            ),
        )
        for index, call in enumerate(calls):
            with self.subTest(index=index):
                with self.assertRaises(_DatabaseTouched):
                    call()

    def test_node_control_secret_authority_remains_run_unbound(self) -> None:
        command = AuthorizeSecretUse(
            workspace_id="workspace-a",
            reference=SecretReference("secret://workspace-secrets/transit-key"),
            intent=SecretUseIntent.GATEWAY_NODE_CONTROL_TRANSIT_SIGNING_KEY,
            actor_subject="operator-a",
            correlation_id="node-control-a",
            requested_at="2026-08-14T04:00:00Z",
            actor_scopes=(PolicyScope.SECRET_PROVIDER_USE,),
            operation_id="attempt-a",
        )

        self.assertIsNone(command.run_id)
        self.assertIsNone(command.session_id)
        self.assertIsNone(command.activity_id)
        self.assertIsNone(command.effect_id)
        self.assertIsNone(command.probe_id)

    def authorize_secret_use(self, run_id: object) -> AuthorizeSecretUse:
        return AuthorizeSecretUse(
            workspace_id="workspace-a",
            reference=SecretReference("secret://workspace-secrets/key-a"),
            intent=SecretUseIntent.APPLICATION_CONTROL_TOKEN,
            actor_subject="operator-a",
            correlation_id="correlation-a",
            requested_at="2026-08-14T04:00:00Z",
            actor_scopes=(PolicyScope.SECRET_PROVIDER_USE,),
            run_id=run_id,  # type: ignore[arg-type]
        )

    def authorized_secret_use(self, run_id: object) -> AuthorizedSecretUse:
        return AuthorizedSecretUse(
            authorization_id="authorization-a",
            workspace_id="workspace-a",
            reference_registration_id="reference-a",
            provider_registration_id="provider-a",
            reference=SecretReference("secret://workspace-secrets/key-a"),
            intent=SecretUseIntent.APPLICATION_CONTROL_TOKEN,
            actor_subject="operator-a",
            correlation_id="correlation-a",
            requested_at="2026-08-14T04:00:00Z",
            intent_fingerprint="a" * 64,
            run_id=run_id,  # type: ignore[arg-type]
        )

    def ingress_resource(
        self,
        *,
        source_run_id: object = "run-a",
        status: OwnedIngressResourceStatus = OwnedIngressResourceStatus.ACTIVE,
        removed_at: str | None = None,
        removed_by_run_id: object | None = None,
    ) -> CloudflareOwnedIngressResource:
        return CloudflareOwnedIngressResource(
            workspace_id="workspace-a",
            runtime_id="runtime-a",
            ingress_id="ingress-a",
            authority_ref=IngressAuthorityReference("authority-a"),
            provider_kind=IngressAuthorityProviderKind.CLOUDFLARE,
            tunnel_name="tunnel-a",
            tunnel_id="tunnel-id-a",
            dns_record_id="dns-a",
            hostname="gateway-a.example.com",
            zone_id="zone-a",
            lifecycle=PublicIngressLifecycle.EPHEMERAL,
            created_at="2026-08-14T04:00:00Z",
            observed_at="2026-08-14T04:00:01Z",
            source_run_id=source_run_id,  # type: ignore[arg-type]
            source_activity_id="activity-a",
            source_event_id="event-a",
            status=status,
            removed_at=removed_at,
            removed_by_run_id=removed_by_run_id,  # type: ignore[arg-type]
        )

    def generated_secret(self, *, source_run_id: object) -> GeneratedIngressSecretReference:
        return GeneratedIngressSecretReference(
            workspace_id="workspace-a",
            purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
            secret_ref=SecretReference("secret://workspace-secrets/tunnel-token"),
            provider_registration_id="provider-a",
            reference_registration_id="reference-a",
            custody_id="custody-a",
            provider_version_id="version-a",
            provider_version_number=1,
            recorded_at="2026-08-14T04:00:00Z",
            source_run_id=source_run_id,  # type: ignore[arg-type]
            source_activity_id="activity-a",
            source_event_id="event-a",
        )

    def gateway_checkpoint(
        self,
        *,
        run_id: object,
    ) -> GatewayKeyRotationDeploymentCheckpoint:
        return GatewayKeyRotationDeploymentCheckpoint(
            phase=GatewayKeyRotationDeploymentPhase.OVERLAP,
            status=GatewayKeyRotationDeploymentStatus.PREPARED,
            session_id="session-a",
            plan_id="plan-a",
            approval_request_id="approval-request-a",
            approval_decision_id="approval-decision-a",
            execution_request_id="execution-a",
            run_id=run_id,  # type: ignore[arg-type]
            base_authored_graph_id="graph-a",
            base_realized_projection_id="projection-a",
            desired_authored_graph_id="graph-b",
            desired_realized_projection_id="projection-b",
            desired_revision=2,
            prepared_at="2026-08-14T04:00:00Z",
        )

    def assert_owner_rejection(
        self,
        call: object,
        error_type: type[Exception],
        candidate: object,
    ) -> None:
        error = self.capture_error(call)
        self.assertIs(type(error), error_type)
        self.assertNotIn(str(candidate), str(error))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    def capture_error(self, call: object) -> BaseException:
        try:
            call()  # type: ignore[operator]
        except BaseException as error:
            return error
        self.fail("expected call to fail")


if __name__ == "__main__":
    unittest.main()
