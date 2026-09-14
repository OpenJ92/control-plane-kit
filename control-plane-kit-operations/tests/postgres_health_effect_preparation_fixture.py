"""Committed existing owners for #1851; never execute a health activity."""
from dataclasses import replace

from psycopg.types.json import Jsonb

from control_plane_kit_core.delegation_keys import DelegationKeyAlgorithm, DelegationKeyPurpose, DelegationPublicKey
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretReference, SecretUseIntent, SecretProviderEndpointReference, SecretProviderId,
)
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyRegistrationService, RegisterDelegationSigningKeyCommand,
)
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile, encode_stored_activity_plan
from control_plane_kit_operations.records import GraphVersionRecord, RealizedGraphProjectionRecord
from control_plane_kit_operations.secret_providers import (
    SecretProviderKind, SecretProviderRegistrationService,
    RegisterSecretProviderCommand, RegisterSecretReferenceCommand,
    AuthorizeSecretUse, authorize_secret_use_in_unit_of_work, secret_use_correlation_for,
)
from tests.health_effect_preparation_fixture import (
    HealthEffectPreparationFixture, STORE_MODULE, wire_identity,
)
from tests.postgres_effect_attempt_intent_store_fixture import PostgresEffectAttemptIntentStoreFixture
from tests.test_delegation_signing_keys import PUBLIC_KEY_A, PUBLIC_KEY_B


class PostgresHealthEffectPreparationFixture(
    HealthEffectPreparationFixture, PostgresEffectAttemptIntentStoreFixture,
):
    def setUp(self):
        PostgresEffectAttemptIntentStoreFixture.setUp(self)
        self.addCleanup(PostgresEffectAttemptIntentStoreFixture.tearDown, self)
        self.seed_health_owners()

    def seed_health_owners(self, side=None, relation_digest=None):
        options = {} if side is None else {"side": side}
        self.values = self.material(**options)
        plan, activity, current, desired, _ = self.health_context(**options)
        if relation_digest is not None:
            from control_plane_kit_core.planning import ActivityPlan
            operation = replace(activity.operation, target=replace(activity.operation.target, relation_digest=relation_digest))
            plan = ActivityPlan(tuple(replace(item, operation=operation) if item.activity_id == activity.activity_id
                else item for item in plan.activities))
            activity = plan.activity(activity.activity_id)
        self.health_activity = activity
        with self.unit_of_work() as uow:
            stores = uow.stores
            for index, (name, graph) in enumerate((("health-base", current.graph), ("health-desired", desired.graph)), 3):
                authored = GraphVersionRecord.from_graph(graph_id=name, workspace_id="workspace-a",
                    version=index, graph=graph, created_by="operator-a", created_at="2026-08-15T03:55:00Z")
                stores.graphs.save(authored)
                projection = RealizedGraphProjectionRecord.identity_for_authored(authored_record=authored)
                self.values[("base" if name == "health-base" else "desired") + "_realized_projection_id"] = projection.projection_id
                stores.realized_graphs.save(projection)
            # This is seed construction, not mutation/admission under test. Retain
            # the existing request/run/approval IDs while installing a real plan.
            uow.stores.connection.execute("""
                UPDATE cpk_activity_plans SET base_graph_id='health-base', desired_graph_id='health-desired',
                  base_realized_projection_id=%s,
                  desired_realized_projection_id=%s, payload=%s
                WHERE plan_id='plan-a'
            """, (self.values["base_realized_projection_id"], self.values["desired_realized_projection_id"],
                Jsonb(encode_stored_activity_plan(plan, profile=PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1))))
            uow.commit()
        intent = self.intent(activity_id=activity.activity_id.value)
        intent = replace(intent, operation=activity.operation, products=(), authority_deliveries=(),
            source=replace(intent.source, base_graph_id="health-base", desired_graph_id="health-desired"))
        attempt, evidence = self.intent_attempt(intent=intent, activity_id=activity.activity_id.value,
            event_id=self.values["original_event_id"])
        self.persist_evidence_chain(attempt, evidence)
        self.health_attempt, self.health_intent = attempt, evidence
        self.values.update(identity=evidence.identity, request_fingerprint=evidence.request_fingerprint)
        self.seed_health_authority()
        # Existing typed owner reconstruction is proven before missing-module guards.
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.effect_attempt_intents.get(evidence.identity), evidence)
            self.assertEqual(uow.stores.activity_history.get_plan("plan-a").plan, plan)
            uow.commit()

    def seed_health_authority(self):
        registration = SecretProviderRegistrationService(self.unit_of_work)
        intents = (SecretUseIntent.GATEWAY_NODE_HEALTH_READ_TRANSIT_SIGNING_KEY,
            SecretUseIntent.WORKLOAD_NODE_HEALTH_READ_SIGNING_KEY)
        provider = registration.register_provider(RegisterSecretProviderCommand(
            workspace_id="workspace-a", provider_id=SecretProviderId("health-secrets"),
            provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
            display_name="Health references", endpoint_reference=SecretProviderEndpointReference("health-provider"),
            credential_reference=SecretReference("secret://health-secrets/provider-token"),
            allowed_reference_prefixes=(SecretReference("secret://health-secrets/keys"),),
            allowed_intents=intents, admitted_by="operator-a", admitted_at="2026-08-01T11:00:00Z",
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        self.provider = provider
        self.keys, self.uses, self.references = {}, {}, {}
        key_service = DelegationSigningKeyRegistrationService(self.unit_of_work)
        for family, purpose, use_intent, pem in (
            ("transit", DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT, intents[0], PUBLIC_KEY_A),
            ("workload", DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ, intents[1], PUBLIC_KEY_B),
        ):
            reference = SecretReference("secret://health-secrets/keys/" + family)
            registered = registration.register_reference(RegisterSecretReferenceCommand(
                workspace_id="workspace-a", reference=reference,
                provider_registration_id=provider.registration_id, allowed_intents=(use_intent,),
                admitted_by="operator-a", admitted_at="2026-08-01T11:05:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
            key = key_service.register(RegisterDelegationSigningKeyCommand(
                workspace_id="workspace-a", purpose=purpose, issuer="cpk-server",
                public_key=DelegationPublicKey(key_id="health-" + family,
                    algorithm=DelegationKeyAlgorithm.ED25519, public_key_pem=pem),
                private_key_reference=reference, admitted_by="operator-a", admitted_at="2026-08-01T11:06:00Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,)))
            fields = dict(workspace_id="workspace-a", reference=reference, intent=use_intent,
                actor_subject="operator-a", operation_id=wire_identity(self.health_intent.identity),
                session_id="session-a", run_id="run-a", activity_id=self.health_activity.activity_id.value)
            command = AuthorizeSecretUse(**fields, correlation_id=secret_use_correlation_for(**fields),
                requested_at="2026-08-01T11:07:00Z", actor_scopes=(PolicyScope.SECRET_PROVIDER_USE,))
            with self.unit_of_work() as uow:
                authorized, selected_provider = authorize_secret_use_in_unit_of_work(uow, command)
                self.assertEqual(selected_provider, provider)
                uow.commit()
            self.keys[family], self.uses[family], self.references[family] = key, authorized, registered
            self.values[family + "_key_registration_id"] = key.registration_id
            self.values[family + "_authorization_id"] = authorized.authorization_id

    def health_record(self):
        return self.api().HealthEffectPreparationRecord(**self.values)

    def health_store(self, connection=None):
        return self.api(STORE_MODULE).HealthEffectPreparationStore(connection or self.connection)

    def persist_health(self):
        record = self.health_record()
        with self.unit_of_work() as uow:
            store = self.health_store(uow.stores.connection)
            self.assertEqual(store.insert_absent(record), record)
            uow.commit()
        return record

    def seed_retry_health_values(self):
        """Existing lawful failed original and next intent; no start service call."""
        from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
        from control_plane_kit_operations.effect_attempt_intent_evidence import EffectAttemptIntentRecord
        number = self.health_attempt.state.identity.attempt + 1
        prior = self.health_attempt
        failed_state = replace(self.state("failed", attempt=number - 1,
            activity_id=self.health_activity.activity_id.value), request_fingerprint=prior.state.request_fingerprint)
        failed_event = self.event(failed_state, self.event_kind("failed", compensation=False),
            event_id=f"health-failed-{number - 1}", ordinal=2 * number,
            occurred_at=f"2030-01-01T00:00:{2 * number:02d}Z")
        failed = EffectAttemptRecord(failed_state, prior.original_start_event, failed_event)
        with self.unit_of_work() as uow:
            uow.stores.execution.add_event(failed.latest_transition_event)
            self.assertEqual(uow.stores.effect_attempts.compare_and_set(self.health_attempt, failed), failed)
            uow.commit()
        state = replace(self.state("started", attempt=number, activity_id=self.health_activity.activity_id.value),
            request_fingerprint=self.health_intent.request_fingerprint)
        event = self.event(state, self.health_attempt.original_start_event.kind,
            event_id=f"health-retry-original-{number}", ordinal=2 * number + 1, occurred_at=f"2030-01-01T00:00:{2 * number + 1:02d}Z")
        attempt = EffectAttemptRecord(state, event, event)
        evidence = EffectAttemptIntentRecord(state.identity, event, self.health_intent.intent)
        self.persist_evidence_chain(attempt, evidence)
        self.health_attempt, self.health_intent = attempt, evidence
        request = replace(self.values["request"], request_id=f"health-request-{number}")
        self.values = {**self.values, "identity": state.identity, "original_event_id": event.event_id,
            "request": request,
            "transit_grant": replace(self.values["transit_grant"], attempt_id=wire_identity(state.identity),
                request_id=request.request_id, request_digest=request.canonical_digest(), jti=f"transit-jti-{number}"),
            "workload_grant": replace(self.values["workload_grant"], request_id=request.request_id,
                request_digest=request.canonical_digest(), jti=f"workload-jti-{number}")}
        self.seed_health_authority()
        return self.health_record()

    def alternate_provider_reference(self):
        service = SecretProviderRegistrationService(self.unit_of_work)
        intents = tuple(use.intent for use in self.uses.values())
        provider = service.register_provider(RegisterSecretProviderCommand(
            workspace_id="workspace-a", provider_id=SecretProviderId("other-health-secrets"),
            provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS, display_name="Other health references",
            endpoint_reference=SecretProviderEndpointReference("other-health-provider"),
            credential_reference=SecretReference("secret://other-health/provider-token"),
            allowed_reference_prefixes=(SecretReference("secret://other-health/keys"),),
            allowed_intents=intents, admitted_by="operator-a", admitted_at="2026-08-01T11:00:00Z",
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        reference = service.register_reference(RegisterSecretReferenceCommand(
            workspace_id="workspace-a", reference=SecretReference("secret://other-health/keys/other"),
            provider_registration_id=provider.registration_id, allowed_intents=intents,
            admitted_by="operator-a", admitted_at="2026-08-01T11:05:00Z",
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        return provider, reference

    def coherent_use(self, family, **changes):
        from control_plane_kit_operations.secret_providers import authorized_secret_use_for
        use = self.uses[family]
        context = dict(workspace_id=use.workspace_id, reference=use.reference, intent=use.intent,
            actor_subject=use.actor_subject, operation_id=use.operation_id, session_id=use.session_id,
            run_id=use.run_id, activity_id=use.activity_id, effect_id=use.effect_id, probe_id=use.probe_id)
        context.update(changes)
        command = AuthorizeSecretUse(**context, correlation_id=secret_use_correlation_for(**context),
            requested_at=use.requested_at, actor_scopes=())
        candidate = authorized_secret_use_for(command, reference=self.references[family], provider=self.provider)
        self.assertEqual(candidate.authorization_id, "suse_" + candidate.intent_fingerprint)
        return candidate
