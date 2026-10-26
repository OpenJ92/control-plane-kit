"""Workload preparation retains original truth through the actual start owner."""

from dataclasses import replace
import unittest

from control_plane_kit_core.planning import ActivityId, ActivityPlan, NodeTarget, PlannedActivity, StartNode
from control_plane_kit_core.products import ProductDescriptorCodec
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.effect_attempt_start import NewlyStarted
from control_plane_kit_operations.execution_invocations import ExecutionInvocationKind
from control_plane_kit_operations.products import RegisteredProduct
from control_plane_kit_operations.runtime_authorities import LocalDockerSocketAuthority, RemoteDockerTlsAuthority
from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_context
from tests.effect_attempt_intent_fixture import product_material
from tests.postgres_effect_attempt_reconciliation_fixture import FailIfObserver
from tests.test_postgres_prepared_runtime_invocations import _PreparedRuntimeInvocationFixture
from tests.test_runtime_interpreter_dispatcher import _registered_product, graph_with_node


class _WorkloadInvocationFixture(_PreparedRuntimeInvocationFixture):
    workload_preparation = True
    workload_authority = True
    workload_runtime_authority = LocalDockerSocketAuthority()

    def reset_start_truth(self, **_options):
        material = product_material()
        if not self.workload_preparation:
            from control_plane_kit_core.verification import VerificationContract
            material = replace(material, product=replace(material.product,
                runtime_contract=replace(material.product.runtime_contract,
                    secret_deliveries=(), verification=VerificationContract())), secret_selections=())
        document = ProductDescriptorCodec().encode_document(material.product)
        product = RegisteredProduct.from_document(workspace_id="workspace-a",
            descriptor_document=document, source=_registered_product().source,
            imported_by="operator-a", imported_at=self.clock())
        desired = graph_with_node(RuntimeKind.DOCKER,
            authority_ref=RuntimeAuthorityReference("prepared-runtime") if self.workload_authority else None,
            registered_product=product, secret_selections=material.secret_selections)
        desired = replace(desired, nodes={"api": replace(desired.nodes["api"],
            sockets=material.product.runtime_contract.sockets,
            block_spec=replace(desired.nodes["api"].block_spec,
                verification=material.product.runtime_contract.verification))})
        self.activity_id = "start-workload"
        plan = ActivityPlan((PlannedActivity(ActivityId(self.activity_id), StartNode(NodeTarget("api"))),))
        base = replace(desired, name="current", nodes={},
            runtimes={key: replace(runtime, children=()) for key, runtime in desired.runtimes.items()})
        self.engine = super().reset_start_truth(plan=plan,
            base=base, desired=replace(desired, name="desired"),
            product_document=document, runtime_authority=self.workload_runtime_authority)


class PostgresWorkloadInvocationTargets(_WorkloadInvocationFixture, unittest.TestCase):
    def test_registered_local_consumer_start_retains_atomic_prepared_marker(self):
        bound = self.binding(generation=self.runtime_fence.generation)
        owner = self.engine.coordinator(FailIfObserver())
        context = owner._load_context(self.engine.command(generation=self.runtime_fence.generation))
        intent = _runtime_effect_intent_for_context(context, context.plan.activity(ActivityId(self.activity_id)))
        material, = intent.products
        self.assertEqual(material.secret_selections, product_material().secret_selections)
        self.assertEqual(intent.authority_ref, self.reference)
        transition = self.transition(identity=self.identity(activity_id=self.activity_id), intent=intent)
        command = self.start_command(intent=intent, transition=transition, fence=self.runtime_fence)
        started = self.fresh_start().execute(command, command_context=bound)
        self.assertIs(type(started), NewlyStarted)
        with self.unit_of_work() as uow:
            original = uow.stores.effect_attempt_intents.get(started.attempt.state.identity)
            self.assertEqual(original.intent, intent)
            self.assertEqual(original.original_start_event, started.attempt.original_start_event)
            self.assertEqual(uow.stores.effect_attempts.get(started.attempt.state.identity), started.attempt)
            marker = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id=self.activity_id, kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        self.assertIsNotNone(marker, "registered local consumer start lacks its atomic prepared dispatch marker")
        self.assertEqual(marker.disposition.value, "planned")
        self.assertEqual(marker.runtime_registration_id, self.runtime.registration_id)
        self.assertEqual(marker.original_event_id, started.attempt.original_start_event.event_id)
        self.assertEqual(marker.request_fingerprint, started.attempt.state.request_fingerprint)
        self.assertIsNone(marker.decision)
        self.assertIsNone(marker.outcome)


class _ConsumerWorkloadRuntime:
    """Exercise the public port for the consumed selection, without a provider."""
    def __init__(self, fixture):
        self.fixture = fixture
        self.requests = []
        self.committed = []

    def execute(self, request, *, invocations=None):
        raise AssertionError("prepared workload must use execute_with_authority")

    def execute_with_authority(self, request, authority, *, invocations):
        from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
        from control_plane_kit_core.secret_version_grants import SecretVersionResolutionGrant
        self.requests.append(request)
        material, = request.products
        selection, = material.secret_selections
        grant, = request.secret_resolution_grants
        self.fixture.assertIs(type(grant), SecretVersionResolutionGrant)
        self.fixture.assertEqual(grant.selection, selection)
        self.fixture.assertEqual(grant.session_id, "session-a")
        # The product also declares a probe. Node execution consumes only the
        # selected delivery: no generic-grant loop defines the runtime's roles.
        secret = invocations.admit("secret-resolution", authorization_id=grant.authorization_id)
        self._committed(secret)
        invocations.record_outcome(secret, "returned")
        final = invocations.admit("prepared-runtime-dispatch")
        self._committed(final)
        invocations.record_outcome(final, "returned")
        return RuntimeEffectResult.failed(request.effect_id,
            RuntimeEffectFailure("fixture.stopped", "No installation was performed."))

    def _committed(self, permit):
        self.fixture.assertEqual(self.fixture.ledger.active, 0)
        with self.fixture.unit_of_work() as uow:
            retained = uow.stores.execution.command_invocation(permit.record.command, permit.record.invocation_id)
        self.fixture.assertEqual(retained, permit.record)
        self.fixture.assertEqual((retained.disposition.value, retained.outcome.value), ("admitted", "pending"))
        self.committed.append(retained)


class PostgresWorkloadVersionInvocationTests(_WorkloadInvocationFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        import control_plane_kit_operations as operations
        from control_plane_kit_core.identity import AuthenticatedPrincipal
        from control_plane_kit_core.policies import PolicyScope
        from control_plane_kit_core.secrets import SecretProviderEndpointReference, SecretReference
        from control_plane_kit_operations.secret_providers import (
            RegisterSecretProviderCommand, RegisterSecretReferenceCommand,
            SecretProviderKind, SecretProviderRegistrationService,
        )
        from control_plane_kit_operations.workflows import IdempotencyKey
        from tests.postgres_effect_attempt_reconciliation_fixture import UnitOfWorkLedger
        self.permissions().grant(operations.GrantWorkspacePermissions(self.admin_context, "workspace-a",
            self.worker, (PolicyScope.SECRET_PROVIDER_USE,), 1, IdempotencyKey("grant-workload-secrets")))
        self.original = operations.WorkspaceContextResolver(self.unit_of_work,
            installation_id="installation-a").resolve(AuthenticatedPrincipal(self.worker), "workspace-a")
        selection, = product_material().secret_selections
        registrations = SecretProviderRegistrationService(self.unit_of_work)
        self.provider = registrations.register_provider(RegisterSecretProviderCommand(
            workspace_id="workspace-a", provider_id=selection.reference.provider_id,
            provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS, display_name="Workload fixture",
            endpoint_reference=SecretProviderEndpointReference("workload-provider"),
            credential_reference=SecretReference("secret://bootstrap/workload/token"),
            allowed_reference_prefixes=(selection.reference,), allowed_intents=(selection.intent,),
            admitted_by="operator-a", admitted_at=self.clock(), actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        registrations.register_reference(RegisterSecretReferenceCommand(workspace_id="workspace-a",
            reference=selection.reference, provider_registration_id=self.provider.registration_id,
            allowed_intents=(selection.intent,), admitted_by="operator-a", admitted_at=self.clock(),
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        self.ledger = UnitOfWorkLedger(self.unit_of_work)
        self.adapter = _ConsumerWorkloadRuntime(self)
        self.observer = FailIfObserver()

    def composition(self, **options):
        from tests.test_postgres_runtime_invocation_carriage import _PreparedRuntimeCarriageFixture
        return _PreparedRuntimeCarriageFixture.composition(self, **{"exact_versions": True, **options})

    def original_start(self):
        bound = self.binding(generation=self.runtime_fence.generation, scopes=self.original.granted_scopes)
        context = self.engine.coordinator(FailIfObserver())._load_context(
            self.engine.command(generation=self.runtime_fence.generation))
        intent = _runtime_effect_intent_for_context(context, context.plan.activity(ActivityId(self.activity_id)))
        transition = self.transition(identity=self.identity(activity_id=self.activity_id), intent=intent)
        start = self.start_command(intent=intent, transition=transition, fence=self.runtime_fence,
            authority=self.authority(scopes=self.original.granted_scopes))
        started = self.fresh_start().execute(start, command_context=bound)
        self.assertIs(type(started), NewlyStarted)
        return bound, start, started

    def authorized_use(self, start, started, *, version=None, session_id="session-a"):
        from control_plane_kit_core.runtime_effect_observation import runtime_effect_request_for_intent
        from control_plane_kit_operations.runtime_secret_uses import secret_authorization_command
        from control_plane_kit_operations.secret_providers import SecretUseAuthorizationService
        request = runtime_effect_request_for_intent(start.intent,
            effect_id=started.attempt.original_start_event.event_id, secret_resolution_grants=())
        selection, = request.products[0].secret_selections
        if version is not None:
            selection = replace(selection, version_id=version)
        command = secret_authorization_command(request, selection, actor_subject=self.original.actor_id,
            requested_at=self.clock(), actor_scopes=self.original.granted_scopes, session_id=session_id)
        grant = SecretUseAuthorizationService(self.unit_of_work).authorize_version_resolution(command)
        return grant

    def bind(self, bound, start, started, *, factory=None):
        import control_plane_kit_operations as operations
        service = operations.ExecutionInvocationService(factory or self.ledger,
            permission_policy=self.policy(), clock=self.clock)
        return service, service.bind_runtime(command_context=bound, start=start, started=started)

    def test_local_consumer_executes_only_actual_role_after_committed_admission(self):
        import asyncio
        owner, command = self.composition()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("failed", 1))
        self.assertEqual(len(self.adapter.requests), 1)
        self.assertEqual([row.kind.value for row in self.adapter.committed],
            ["secret-resolution", "prepared-runtime-dispatch"])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 1)
        with self.unit_of_work() as uow:
            attempt = uow.stores.effect_attempts.get(self.identity(activity_id=self.activity_id))
            marker = uow.stores.execution.original_final_invocation(attempt.state.identity,
                original_event_id=attempt.original_start_event.event_id,
                request_fingerprint=attempt.state.request_fingerprint,
                kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        self.assertEqual((marker.disposition.value, marker.outcome.value), ("admitted", "returned"))
        self.assertEqual(attempt.state.status.value, "failed")
        self.assertEqual(self.observer.calls, [])

    def test_missing_version_or_invocation_qualification_stops_before_new_work(self):
        import asyncio
        for options in ({"exact_versions": False}, {"profile": False}, {"service": False}):
            with self.subTest(options=options):
                owner, command = self.composition(**options)
                command = replace(command, execution=replace(command.execution,
                    idempotency_key=type(command.execution.idempotency_key)("missing-" + next(iter(options)))))
                result = asyncio.run(owner.execute_managed(command))
                self.assertEqual((result.status.value, result.effects_attempted), ("unsupported", 0))
                self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 0)
                self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)
                self.assertEqual(self.adapter.requests, [])

    def test_original_selection_and_session_cannot_be_substituted(self):
        import control_plane_kit_operations as operations
        bound, start, started = self.original_start()
        _, port = self.bind(bound, start, started)
        for changes in ({"version": "other-version"}, {"session_id": "foreign-session"}):
            with self.subTest(changes=changes):
                grant = self.authorized_use(start, started, **changes)
                with self.assertRaises(operations.ExecutionInvocationConflict):
                    port.admit("secret-resolution", authorization_id=grant.authorization_id)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 1)

    def test_current_secret_permission_is_rechecked_before_version_resolution(self):
        import control_plane_kit_operations as operations
        from control_plane_kit_core.policies import PolicyScope
        from control_plane_kit_operations.workflows import IdempotencyKey
        bound, start, started = self.original_start()
        grant = self.authorized_use(start, started)
        service, port = self.bind(bound, start, started)
        self.permissions().revoke(operations.RevokeWorkspacePermissions(self.admin_context, "workspace-a",
            self.worker, (PolicyScope.SECRET_PROVIDER_USE,), 2, IdempotencyKey("revoke-workload-secrets")))
        with self.assertRaises(operations.ExecutionInvocationDenied):
            port.admit("secret-resolution", authorization_id=grant.authorization_id)
        self.assertEqual(service.inspect_runtime_dispatch(port).disposition.value, "planned")
        with self.unit_of_work() as uow:
            denied = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id=self.activity_id, kind=ExecutionInvocationKind.SECRET_RESOLUTION,
                authorization_id=grant.authorization_id)
        self.assertEqual(denied.disposition.value, "denied")
        self.assertIsNone(denied.outcome)

    def test_lost_version_admission_ack_retains_original_without_returning_permit(self):
        import control_plane_kit_operations as operations
        from control_plane_kit_operations.postgres import PostgresUnitOfWork
        from tests.test_postgres_health_effect_start_rollback import _CommitThenRaiseConnection
        bound, start, started = self.original_start()
        grant = self.authorized_use(start, started)
        failure = RuntimeError("version admission acknowledgement lost")
        connections = []
        def factory():
            def connect():
                connection = _CommitThenRaiseConnection(self.connect(), failure)
                connections.append(connection)
                return connection
            return PostgresUnitOfWork(connect)
        _, port = self.bind(bound, start, started, factory=factory)
        with self.assertRaises(operations.ExecutionInvocationConflict):
            port.admit("secret-resolution", authorization_id=grant.authorization_id)
        self.assertEqual(sum(connection.commits for connection in connections), 1)
        _, original = self.bind(bound, start, started)
        with self.assertRaises(operations.ExecutionInvocationExisting):
            original.admit("secret-resolution", authorization_id=grant.authorization_id)
        with self.unit_of_work() as uow:
            retained = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id=self.activity_id, kind=ExecutionInvocationKind.SECRET_RESOLUTION,
                authorization_id=grant.authorization_id)
        self.assertEqual((retained.disposition.value, retained.outcome.value), ("admitted", "pending"))

    def test_original_version_grant_cannot_bypass_current_provider_revocation(self):
        import control_plane_kit_operations as operations
        bound, start, started = self.original_start()
        grant = self.authorized_use(start, started)
        service, port = self.bind(bound, start, started)
        with self.unit_of_work() as uow:
            uow.stores.secret_providers.revoke_active("workspace-a", self.provider.provider_id,
                revoked_by="operator-a", revoked_at=self.clock())
            uow.commit()
        with self.assertRaises(operations.ExecutionInvocationConflict):
            port.admit("secret-resolution", authorization_id=grant.authorization_id)
        self.assertEqual(service.inspect_runtime_dispatch(port).disposition.value, "planned")
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 1)


class _UnpreparedWorkloadRuntime:
    def __init__(self):
        self.requests = []

    def execute(self, request):
        from control_plane_kit_core.runtime_effects import RuntimeEffectFailure, RuntimeEffectResult
        self.requests.append(request)
        return RuntimeEffectResult.failed(request.effect_id, RuntimeEffectFailure("fixture.stopped", "Stopped."))

    def execute_with_authority(self, request, authority):
        return self.execute(request)


class _WorkloadEligibilityFixture(_WorkloadInvocationFixture):
    def composition(self):
        from tests.postgres_effect_attempt_reconciliation_fixture import UnitOfWorkLedger
        from tests.test_postgres_runtime_invocation_carriage import _PreparedRuntimeCarriageFixture
        self.ledger = UnitOfWorkLedger(self.unit_of_work)
        self.adapter = _UnpreparedWorkloadRuntime()
        self.observer = FailIfObserver()
        return _PreparedRuntimeCarriageFixture.composition(self, exact_versions=True)


class PostgresAuthoritylessWorkloadTests(_WorkloadEligibilityFixture, unittest.TestCase):
    workload_authority = False

    def test_authorityless_preparation_refuses_before_start_or_grants(self):
        import asyncio
        owner, command = self.composition()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("unsupported", 0))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_effect_attempts").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_secret_use_authorizations").fetchone()[0], 0)
        self.assertEqual(self.adapter.requests, [])

    def test_direct_preparation_start_refuses_without_fabricating_permission_denial(self):
        from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartConflict
        bound = self.binding(generation=self.runtime_fence.generation)
        context = self.engine.coordinator(FailIfObserver())._load_context(
            self.engine.command(generation=self.runtime_fence.generation))
        intent = _runtime_effect_intent_for_context(context, context.plan.activity(ActivityId(self.activity_id)))
        transition = self.transition(identity=self.identity(activity_id=self.activity_id), intent=intent)
        start = self.start_command(intent=intent, transition=transition, fence=self.runtime_fence)
        before = self.attempt_snapshot()
        with self.assertRaises(EffectAttemptStartConflict):
            self.fresh_start().execute(start, command_context=bound)
        self.assertEqual(self.attempt_snapshot(), before)


class PostgresUnpreparedWorkloadTests(_WorkloadEligibilityFixture, unittest.TestCase):
    workload_preparation = False

    def test_fresh_local_no_preparation_retains_prior_execution_owner(self):
        import asyncio
        owner, command = self.composition()
        result = asyncio.run(owner.execute_managed(command))
        self.assertEqual((result.status.value, result.effects_attempted), ("failed", 1))
        self.assertEqual(len(self.adapter.requests), 1)
        self.assertEqual(self.adapter.requests[0].secret_resolution_grants, ())
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_execution_command_invocations").fetchone()[0], 0)
        replay = asyncio.run(owner.execute_managed(command))
        self.assertEqual(replay, result)
        self.assertEqual(len(self.adapter.requests), 1)

class PostgresRemoteWorkloadHistoryTests(_WorkloadEligibilityFixture, unittest.TestCase):
    from control_plane_kit_core.secrets import SecretReference
    workload_preparation = False
    workload_runtime_authority = RemoteDockerTlsAuthority("tcp://runtime.example.test:2376",
        SecretReference("secret://runtime/ca"), SecretReference("secret://runtime/cert"),
        SecretReference("secret://runtime/key"))

    def test_inflight_missing_original_marker_never_adopts_current_local_authority(self):
        import asyncio
        import control_plane_kit_operations as operations
        from tests.test_postgres_runtime_invocation_carriage import _PreparedRuntimeCarriageFixture
        bound = self.binding(generation=self.runtime_fence.generation)
        context = self.engine.coordinator(FailIfObserver())._load_context(
            self.engine.command(generation=self.runtime_fence.generation))
        intent = _runtime_effect_intent_for_context(context, context.plan.activity(ActivityId(self.activity_id)))
        transition = self.transition(identity=self.identity(activity_id=self.activity_id), intent=intent)
        start = self.start_command(intent=intent, transition=transition, fence=self.runtime_fence)
        started = self.fresh_start().execute(start, command_context=bound)
        self.assertIs(type(started), NewlyStarted)
        with self.unit_of_work() as uow:
            marker = uow.stores.execution.command_invocation_for_slot(bound.receipt,
                activity_id=self.activity_id, kind=ExecutionInvocationKind.PREPARED_RUNTIME_DISPATCH)
        self.assertIsNotNone(marker)
        # Defensive missing-history corruption, then an actual current registration
        # replacement. Today's local selection must not repair original provenance.
        self.connection.execute("DELETE FROM cpk_execution_command_invocations WHERE invocation_id=%s",
            (marker.invocation_id,))
        _PreparedRuntimeCarriageFixture.change_runtime(self, LocalDockerSocketAuthority())
        owner, command = self.composition()
        before = self.attempt_snapshot()
        with self.assertRaises(operations.ExecutionInvocationConflict):
            asyncio.run(owner.execute_managed(command))
        self.assertEqual(self.attempt_snapshot(), before)
        self.assertEqual(self.observer.calls, [])
        self.assertEqual(self.adapter.requests, [])


class PostgresAuthoritylessUnpreparedWorkloadTests(_WorkloadEligibilityFixture, unittest.TestCase):
    workload_authority = False
    workload_preparation = False
    test_fresh_authorityless_no_preparation_retains_prior_execution_owner = (
        PostgresUnpreparedWorkloadTests.test_fresh_local_no_preparation_retains_prior_execution_owner)
