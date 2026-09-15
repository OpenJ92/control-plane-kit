"""Approved ready health runs with no preexisting start/use/preparation evidence."""
from contextlib import contextmanager, ExitStack
from dataclasses import replace
from datetime import datetime
from unittest import mock

from psycopg.types.json import Jsonb

from control_plane_kit_core.delegation_keys import DelegationKeyAlgorithm, DelegationKeyPurpose, DelegationPublicKey
from control_plane_kit_core.planning import derive_schedule, project_activity_journal
from control_plane_kit_core.policies import ApprovalPolicy, PolicyScope
from control_plane_kit_core.secrets import (
    SecretReference, SecretUseIntent, SecretProviderEndpointReference, SecretProviderId,
)
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations.delegation_signing_keys import (
    ActivateDelegationSigningKeyCommand, DelegationSigningKeyRegistrationService,
    RegisterDelegationSigningKeyCommand,
)
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.health_effect_preparations import health_effect_attempt_wire_id
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile, encode_stored_activity_plan
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.postgres.delegation_signing_key_store import DelegationSigningKeyStore
from control_plane_kit_operations.postgres.secret_provider_store import SecretProviderStore, SecretReferenceStore, SecretUseAuthorizationStore
from control_plane_kit_operations.records import ActivityEventKind, ActivityEventRecord, GraphVersionRecord, RealizedGraphProjectionRecord
from control_plane_kit_operations.secret_providers import (
    SecretProviderKind, SecretProviderRegistrationService,
    RegisterSecretProviderCommand, RegisterSecretReferenceCommand,
    AuthorizeSecretUse, authorize_secret_use_in_unit_of_work, secret_use_correlation_for,
)
from tests.execution_lease_recovery_fixture import Sequence
from tests.health_effect_start_fixture import HealthEffectStartValues, trusted_health_context
from tests.postgres_effect_attempt_start_fixture import PostgresEffectAttemptStartFixture
from tests.test_delegation_signing_keys import PUBLIC_KEY_A, PUBLIC_KEY_B


class PostgresHealthEffectStartFixture(HealthEffectStartValues, PostgresEffectAttemptStartFixture):
    def setUp(self):
        PostgresEffectAttemptStartFixture.setUp(self)
        self.seed_ready_health()

    def seed_ready_health(self, **context_options):
        self.health_plan, self.health_activity, current, desired, _ = self.health_context(**context_options)
        self.assertTrue(self.health_plan.ready_for_execution)
        self.projections = {}
        requirement = ApprovalPolicy().requirement_for(self.health_plan)
        with self.unit_of_work() as uow:
            for index, (name, graph) in enumerate((("health-base", current.graph), ("health-desired", desired.graph)), 3):
                authored = GraphVersionRecord.from_graph(graph_id=name, workspace_id="workspace-a", version=index,
                    graph=graph, created_by="operator-a", created_at="2026-08-15T03:55:00Z")
                uow.stores.graphs.save(authored)
                projection = RealizedGraphProjectionRecord.identity_for_authored(authored_record=authored)
                uow.stores.realized_graphs.save(projection)
                self.projections[name] = projection
            # Seed construction is not the mutation/admission under test. Keep all
            # compiled dependencies, and update the complete approval risk tuple.
            uow.stores.connection.execute("""
                UPDATE cpk_activity_plans SET base_graph_id='health-base', desired_graph_id='health-desired',
                  base_realized_projection_id=%s, desired_realized_projection_id=%s, payload=%s
                WHERE plan_id='plan-a'
            """, (self.projections["health-base"].projection_id, self.projections["health-desired"].projection_id,
                Jsonb(encode_stored_activity_plan(self.health_plan, profile=PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1))))
            uow.stores.connection.execute("""UPDATE cpk_approval_requests
                SET required_scope=%s, max_risk=%s, destructive=%s WHERE request_id='approval-request-a'""",
                (requirement.required_scope.value, requirement.max_risk.value, requirement.destructive))
            uow.stores.connection.execute("UPDATE cpk_approval_decisions SET scope=%s WHERE decision_id='approval-decision-a'",
                (requirement.required_scope.value,))
            events = list(uow.stores.execution.events_for_run("run-a"))
            # Seed successful predecessor history using the existing journal law;
            # never remove dependencies to force the health activity ready.
            for _ in range(len(self.health_plan.activities) + 1):
                journal = project_activity_journal(self.health_plan, activity_journal_events(events))
                ready = derive_schedule(self.health_plan, journal.state).ready
                if any(item.activity_id == self.health_activity.activity_id for item in ready):
                    break
                self.assertTrue(ready, "health fixture has no lawful ready predecessor")
                predecessor = ready[0]
                for kind in (ActivityEventKind.STEP_STARTED, ActivityEventKind.STEP_SUCCEEDED):
                    ordinal = len(events) + 1
                    event = ActivityEventRecord(f"seed-health-{ordinal}", "run-a", ordinal, kind,
                        "2026-08-15T04:00:00Z", activity_id=predecessor.activity_id.value)
                    uow.stores.execution.add_event(event)
                    events.append(event)
            else:
                self.fail("health fixture did not reach its selected activity")
            self.original_event_ordinal = len(events) + 1
            self.assertEqual(uow.stores.activity_history.get_plan("plan-a").plan, self.health_plan)
            uow.commit()
        self.start_value = self.health_start_value(activity=self.health_activity)
        self.seed_health_registrations()
        self.assertEqual(self.health_counts(), (0, 0, 0, 0))

    def reset_health(self, **context_options):
        PostgresEffectAttemptStartFixture.reset_start_truth(self)
        self.seed_ready_health(**context_options)

    def seed_health_registrations(self):
        service = SecretProviderRegistrationService(self.unit_of_work)
        self.family_intents = (
            SecretUseIntent.GATEWAY_NODE_HEALTH_READ_TRANSIT_SIGNING_KEY,
            SecretUseIntent.WORKLOAD_NODE_HEALTH_READ_SIGNING_KEY,
        )
        self.provider = service.register_provider(RegisterSecretProviderCommand(
            workspace_id="workspace-a", provider_id=SecretProviderId("health-secrets"),
            provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS, display_name="Health references",
            endpoint_reference=SecretProviderEndpointReference("health-provider"),
            credential_reference=SecretReference("secret://health-secrets/provider-token"),
            allowed_reference_prefixes=(SecretReference("secret://health-secrets/keys"),),
            allowed_intents=self.family_intents, admitted_by="operator-a", admitted_at="2026-08-01T11:00:00Z",
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        self.keys, self.references = {}, {}
        key_service = DelegationSigningKeyRegistrationService(self.unit_of_work)
        for family, purpose, intent, pem in (
            ("transit", DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT, self.family_intents[0], PUBLIC_KEY_A),
            ("workload", DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ, self.family_intents[1], PUBLIC_KEY_B),
        ):
            reference = SecretReference("secret://health-secrets/keys/" + family)
            self.references[family] = service.register_reference(RegisterSecretReferenceCommand(
                workspace_id="workspace-a", reference=reference, provider_registration_id=self.provider.registration_id,
                allowed_intents=(intent,), admitted_by="operator-a", admitted_at="2026-08-01T11:05:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
            self.keys[family] = key_service.register(RegisterDelegationSigningKeyCommand(
                workspace_id="workspace-a", purpose=purpose, issuer="cpk-server",
                public_key=DelegationPublicKey(key_id="health-" + family, algorithm=DelegationKeyAlgorithm.ED25519,
                    public_key_pem=pem), private_key_reference=reference, admitted_by="operator-a",
                admitted_at="2026-08-01T11:06:00Z", actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,)))
            self.keys[family] = key_service.activate(ActivateDelegationSigningKeyCommand(
                workspace_id="workspace-a", purpose=purpose, issuer="cpk-server",
                key_id=self.keys[family].key_id, activated_by="operator-a",
                activated_at="2026-08-01T11:07:00Z", actor_scopes=(PolicyScope.DELEGATION_KEY_ACTIVATE,)))

    def start_health_command(self, *, context=None, start=None):
        return self.health_command(start=self.start_value if start is None else start,
            context=trusted_health_context() if context is None else context)

    def health_service(self, *, ids=None, unit_of_work=None):
        ids = ids if ids is not None else Sequence("health-original", "health-request", "health-transit-jti", "health-workload-jti")
        return EffectAttemptStartService(unit_of_work or self.unit_of_work, id_factory=ids), ids

    def execute_health(self, *, command=None, ids=None, unit_of_work=None):
        command = self.start_health_command() if command is None else command
        service, sequence = self.health_service(ids=ids, unit_of_work=unit_of_work)
        self.assertTrue(callable(getattr(service, "execute_health", None)), "#1852 atomic health first-start entrance is missing")
        return service.execute_health(command), sequence

    def health_counts(self):
        return tuple(self.connection.execute("SELECT count(*) FROM " + relation).fetchone()[0]
            for relation in ("cpk_effect_attempt_intents", "cpk_effect_attempts", "cpk_secret_use_authorizations", "cpk_health_effect_preparations"))

    def health_snapshot(self):
        return (self.attempt_snapshot(), *(
            tuple(self.connection.execute("SELECT * FROM " + relation + " ORDER BY 1, 2, 3").fetchall())
            for relation in ("cpk_secret_use_authorizations", "cpk_health_effect_preparations")))

    def expected_use_command(self, family, *, requested_at, actor="health-operator"):
        key = self.keys[family]
        fields = dict(workspace_id="workspace-a", reference=key.private_key_reference,
            intent=self.family_intents[0 if family == "transit" else 1], actor_subject=actor,
            operation_id=health_effect_attempt_wire_id(self.start_value.transition.identity),
            session_id="session-a", run_id="run-a", activity_id=self.health_activity.activity_id.value)
        return AuthorizeSecretUse(**fields, correlation_id=secret_use_correlation_for(**fields),
            requested_at=requested_at, actor_scopes=(PolicyScope.SECRET_PROVIDER_USE,))

    def seed_existing_use(self, family, *, requested_at):
        command = self.expected_use_command(family, requested_at=requested_at)
        with self.unit_of_work() as uow:
            result, _ = authorize_secret_use_in_unit_of_work(uow, command)
            uow.commit()
        return result

    @contextmanager
    def observed_time(self, observed_at, *, expires_at="2030-01-01T00:10:00Z"):
        self.connection.execute("UPDATE cpk_execution_requests SET claimed_at='2026-08-15T03:59:00Z', lease_expires_at=%s WHERE request_id='request-a'", (expires_at,))
        original = PostgresExecutionStore.observe_request_lease_for_update
        calls = []
        def observe(store, request_id):
            value = original(store, request_id)
            expired = datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            result = replace(value, observed_at=observed_at, expired=expired)
            calls.append(result)
            return result
        with mock.patch.object(PostgresExecutionStore, "observe_request_lease_for_update", observe):
            yield calls

    @contextmanager
    def forbid_fresh_health(self):
        with ExitStack() as stack:
            for owner, method in (
                (PostgresExecutionStore, "observe_request_lease_for_update"),
                (DelegationSigningKeyStore, "require_unambiguous_active"),
                (SecretReferenceStore, "get_active_for_update"),
                (SecretProviderStore, "require_active_registration_for_update"),
                (SecretUseAuthorizationStore, "add"),
            ):
                stack.enter_context(mock.patch.object(owner, method,
                    side_effect=AssertionError("replay entered fresh admission: " + method)))
            yield
