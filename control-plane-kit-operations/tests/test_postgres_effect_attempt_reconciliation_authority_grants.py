from __future__ import annotations

from dataclasses import replace
import unittest
from unittest import mock

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_operations import effect_attempt_reconciliation_interpreter
from control_plane_kit_operations.effect_attempt_reconciliation import (
    EffectAttemptReconciliationConflict,
    EffectAttemptReconciliationDenied,
)
from control_plane_kit_operations.postgres.runtime_authority_store import (
    RuntimeAuthorityStore,
)
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.postgres.effect_attempt_store import (
    EffectAttemptStore,
)
from control_plane_kit_operations.runtime_authorities import (
    RegisteredRuntimeAuthorityStatus,
    RuntimeAuthorityNotFound,
    RuntimeAuthorityRegistrationError,
)
from control_plane_kit_operations.secret_providers import (
    SecretProviderAuthorizationDenied,
    SecretProviderRegistrationError,
    SecretUseAuthorizationConflict,
    SecretUseAuthorizationService,
)
from tests.postgres_effect_attempt_reconciliation_fixture import (
    AUTHORITY_ERROR,
    INVALID_TRUTH_ERROR,
    FailIfFold,
    FailIfObserver,
    PostgresEffectAttemptReconciliationFixture,
    UnitOfWorkLedger,
)


def _authorization_row_projection(row):
    return (
        row[1],
        row[2],
        row[3],
        row[4],
        row[5],
        row[6].isoformat().replace("+00:00", "Z"),
        row[7],
        row[8],
        row[9],
        row[10],
        row[11],
    )


def _authorization_command_projection(candidate):
    return (
        candidate.workspace_id,
        candidate.reference.reference_id,
        candidate.intent.value,
        candidate.actor_subject,
        candidate.correlation_id,
        candidate.requested_at,
        candidate.operation_id,
        candidate.session_id,
        candidate.run_id,
        candidate.activity_id,
        candidate.effect_id,
    )


class PostgresEffectAttemptReconciliationAuthorityGrantTests(
    PostgresEffectAttemptReconciliationFixture,
    unittest.TestCase,
):
    def _seed_version_node_reconciliation(self):
        from control_plane_kit_core.operations import (
            EffectAttemptIdentity, EffectAttemptTransition, EffectAttemptTransitionKind, RunId,
        )
        from control_plane_kit_core.planning import StartNode
        from control_plane_kit_core.runtime_effect_observation import (
            runtime_effect_intent_fingerprint, runtime_effect_intent_for_request,
        )
        from control_plane_kit_core.secret_selections import SecretVersionSelection
        from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
        from control_plane_kit_operations.effect_attempt_start import NewlyStarted, StartEffectAttempt
        from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
        from control_plane_kit_operations.lifecycle import (
            ClaimAndOpenActivityRun, ExecutionLeaseDuration, StartActivityRun,
        )
        from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_context
        from control_plane_kit_operations.workflows import IdempotencyKey
        from tests.graph_lineage_fixture import execution_graph
        from tests import test_execution_coordinator as coordinator_fixture
        from tests.test_secret_version_dispatch import selected_context

        # Reuse real node-plan admission and lifecycle owners. The inherited
        # reconciliation fixture intentionally owns StartRuntime, not this law.
        template = selected_context()
        engine = coordinator_fixture.ExecutionCoordinatorTests()
        engine.database_url, engine.connection = self.database_url, self.connection
        engine.tracker = coordinator_fixture.TrackingUnitOfWorkFactory(self.database_url)
        engine.reset_execution_request(plan=template.plan_record.plan,
            base_graph=execution_graph("current", runtime_id="docker"),
            desired_graph=DEFAULT_GRAPH_CODEC.decode(template.desired_graph.graph_descriptor),
            product_document=template.registered_products[0].descriptor_document)
        authority = self.authority(scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE))
        opened = engine.lifecycle_with_ids("run-a", "version-open", "version-claim").execute(
            ClaimAndOpenActivityRun("request-a", authority, ExecutionLeaseDuration(600),
                IdempotencyKey("version-claim")))
        fence = ExecutionLeaseFence(opened.request.claim.worker_id, opened.request.claim.generation)
        engine.lifecycle_with_ids("version-run-start", "version-run-start-action").execute(
            StartActivityRun("run-a", authority, fence, IdempotencyKey("version-run-start")))
        with self.unit_of_work() as unit:
            stores = unit.stores
            plan = stores.activity_history.get_plan("plan-a")
            activity, = plan.plan.activities
            context = replace(template, activity=activity, plan_record=plan,
                request=stores.execution.get_request("request-a"), run=stores.execution.get_run("run-a"),
                base_graph=stores.realized_graphs.get(plan.base_realized_projection_id),
                desired_graph=stores.realized_graphs.get(plan.desired_realized_projection_id),
                registered_products=stores.registered_products.list_active("workspace-a"),
                authority=authority, fence=fence)
        intent = _runtime_effect_intent_for_context(context, activity)
        self._admit_version_reconciliation_material(intent)
        identity = EffectAttemptIdentity(RunId("run-a"), activity.activity_id.value, 1)
        transition = EffectAttemptTransition(EffectAttemptTransitionKind.STARTED,
            identity, request_fingerprint=runtime_effect_intent_fingerprint(intent))
        started = self.start_service("version-node-start").execute(StartEffectAttempt(
            "request-a", transition, intent, authority, fence))
        self.assertIs(type(started), NewlyStarted)
        current = started.attempt
        with self.unit_of_work() as unit:
            retained = unit.stores.effect_attempt_intents.get(identity)
            persisted = unit.stores.effect_attempts.get(identity)
        request = self.runtime_request(current, intent)
        material, = request.products
        self.assertIs(type(intent.operation), StartNode)
        self.assertEqual(intent.operation, activity.operation)
        self.assertEqual(intent.operation.target.node_id, material.node_id)
        self.assertEqual(material.runtime_id, "docker")
        self.assertTrue(material.secret_selections)
        self.assertTrue(all(type(value) is SecretVersionSelection for value in material.secret_selections))
        self.assertEqual(retained.intent, intent)
        self.assertEqual(persisted, current)
        self.assertEqual(retained.original_start_event, current.original_start_event)
        self.assertEqual(runtime_effect_intent_for_request(request), intent)
        self.assertEqual(current.state.request_fingerprint, runtime_effect_intent_fingerprint(intent))
        self.assertEqual(retained.request_fingerprint, current.state.request_fingerprint)
        self.assertEqual(current.original_start_event.evidence, self.evidence_for(current.state))
        return current, intent, fence

    def _admit_version_reconciliation_material(self, intent):
        from control_plane_kit_core.secrets import (
            SecretProviderEndpointReference, SecretProviderId, SecretReference, SecretUseIntent,
        )
        from control_plane_kit_operations.secret_providers import (
            RegisterSecretProviderCommand, RegisterSecretReferenceCommand,
            SecretProviderKind, SecretProviderRegistrationService,
        )
        uses = set()
        for material in intent.products:
            uses.update((selection.reference, selection.intent) for selection in material.secret_selections)
            if material.pull_authority is not None:
                uses.add((material.pull_authority.credential_reference, SecretUseIntent.OCI_PULL_CREDENTIAL))
            for check in material.product.runtime_contract.verification.checks:
                authentication = getattr(check, "authentication", None)
                if authentication is not None:
                    uses.add((authentication.password_reference, SecretUseIntent.POSTGRES_PASSWORD))
        reference = SecretReference("secret://provider-a/application/token")
        use = SecretUseIntent.APPLICATION_CONTROL_TOKEN
        self.assertEqual(uses, {(reference, use)})
        service = SecretProviderRegistrationService(self.unit_of_work)
        provider = service.register_provider(RegisterSecretProviderCommand(
            workspace_id="workspace-a", provider_id=SecretProviderId("provider-a"),
            provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
            display_name="Version recovery fixture provider",
            endpoint_reference=SecretProviderEndpointReference("provider-a"),
            credential_reference=SecretReference("secret://bootstrap/provider-a/client-token"),
            allowed_reference_prefixes=(SecretReference("secret://provider-a/application"),),
            allowed_intents=(use,), admitted_by="operator-a", admitted_at="2030-01-01T00:00:00Z",
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        service.register_reference(RegisterSecretReferenceCommand(
            workspace_id="workspace-a", reference=reference,
            provider_registration_id=provider.registration_id, allowed_intents=(use,),
            admitted_by="operator-a", admitted_at="2030-01-01T00:00:01Z",
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))

    def test_version_observation_unqualified_refuses_before_authorization_and_observer(self):
        from tests.test_secret_version_dispatch import registration
        for registered in (False, True):
            with self.subTest(explicit_registration=registered):
                current, intent, fence = self._seed_version_node_reconciliation()
                observer = FailIfObserver("unqualified version observer was invoked")
                fold = FailIfFold("unqualified observer reached fold")
                port = registration(observer, exact_version_execute=True,
                    exact_version_execute_with_authority=True) if registered else observer
                before = self.complete_reconciliation_snapshot()
                with self.assertRaises(EffectAttemptReconciliationDenied):
                    self.reconciliation_service(port, fold_service=fold).execute(
                        self.reconciliation_command(current, generation=fence.generation,
                            scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE)))
                self.assertEqual(observer.calls, [])
                self.assertEqual(fold.calls, [])
                self.assertEqual(self.authorization_rows(), ())
                self.assertEqual(self.complete_reconciliation_snapshot(), before)

    def test_version_observation_reuses_original_consumer_correlation_without_redispatch(self):
        from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
        from control_plane_kit_core.secret_installation import secret_material_selection_fingerprint
        from control_plane_kit_operations import secret_providers
        from tests.test_secret_version_dispatch import registration
        command_type = getattr(secret_providers, "AuthorizeSecretVersionUse", None)
        correlation_for = getattr(secret_providers, "secret_version_use_correlation_for", None)
        self.assertIsNotNone(command_type, "missing version-bound authorization command")
        self.assertIsNotNone(correlation_for, "missing version-bound correlation")
        from control_plane_kit_core.secret_version_grants import SecretVersionResolutionGrant
        story = self.observed_story("observed-failed")
        current, intent, fence = self._seed_version_node_reconciliation()
        command = self.reconciliation_command(current, generation=fence.generation,
            scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE))
        request = self.runtime_request(current, intent)
        selection, = request.products[0].secret_selections
        values = dict(workspace_id=intent.source.workspace_id, selection=selection,
            actor_subject=command.authority.worker_id, operation_id=intent.source.request_id,
            run_id=current.state.identity.run_id.value, activity_id=current.state.identity.activity_id,
            effect_id=current.original_start_event.event_id,
            request_fingerprint=runtime_effect_intent_fingerprint(intent),
            selection_fingerprint=secret_material_selection_fingerprint(request))
        authorized = SecretUseAuthorizationService(self.unit_of_work).authorize_version_resolution(
            command_type(**values, correlation_id=correlation_for(**values), session_id=None,
                requested_at="2030-01-01T00:00:02Z", actor_scopes=command.authority.scopes))
        observer = self.observer_for(story, current, intent)
        fold = FailIfFold("observation reached the separately gated receipt/fold boundary")
        with self.assertRaises(AssertionError) as caught:
            self.reconciliation_service(registration(observer, exact_version_observe=True),
                fold_service=fold).execute(command)
        self.assertIs(caught.exception, fold.error)
        self.assertEqual(len(observer.calls), 1)
        observed_request, authority = observer.calls[0]
        self.assertIsNone(authority)
        self.assertEqual(observed_request.intent, intent)
        consumers = [grant for grant in observed_request.runtime_request.secret_resolution_grants
            if type(grant) is SecretVersionResolutionGrant]
        self.assertEqual(consumers, [authorized])
        self.assertIsNone(consumers[0].session_id)
        self.assertEqual(observed_request.effect_id, current.original_start_event.event_id)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_secret_use_authorizations WHERE authorization_kind='consumer-version'"
        ).fetchone(), (1,))

    def test_control_active_authority_and_secret_authorization_are_total(self) -> None:
        story = self.observed_story()
        current, intent, _record, authority = self.seed_reconciliation_source(
            story,
            remote=True,
        )
        uses = self.required_secret_uses(current, intent, authority)
        self.assertGreater(len(uses), 0)
        self.assertEqual(tuple(sorted(set(uses), key=lambda value: (value[0].reference_id, value[1].value))), uses)
        self.admit_secret_uses(uses)
        service = SecretUseAuthorizationService(self.unit_of_work)
        command = self.reconciliation_command(
            current,
            scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE),
        )
        grants = tuple(
            service.authorize_resolution(
                self.authorization_command(
                    current,
                    intent,
                    reference,
                    use_intent,
                    command=command,
                )
            )
            for reference, use_intent in uses
        )
        replayed = tuple(
            service.authorize_resolution(
                self.authorization_command(
                    current,
                    intent,
                    reference,
                    use_intent,
                    command=command,
                    requested_at="2030-01-01T00:00:09Z",
                )
            )
            for reference, use_intent in uses
        )
        self.assertEqual(replayed, grants)
        self.assertEqual(len(self.authorization_rows()), len(uses))
        self.assertNotIn("secret-value-canary", repr(grants))

    def test_no_reference_is_none_and_zero_use_performs_no_authority_or_grant_io(self) -> None:
        story = self.observed_story()
        forbidden = AssertionError("zero-use local reconciliation crossed authority IO")
        with self.subTest(world="zero-use"):
            current, intent, _record, _authority = self.seed_reconciliation_source(
                story,
                authority_ref=False,
                zero_use=True,
            )
            self.assertEqual(self.required_secret_uses(current, intent, None), ())
            observer = self.observer_for(story, current, intent)
            fold = FailIfFold("zero-use control reached fold")
            lock_order = []
            original_request = PostgresExecutionStore.get_request_for_update
            original_run = PostgresExecutionStore.get_run_for_request_for_update
            original_attempt = EffectAttemptStore.get_for_update

            def request(store, request_id):
                lock_order.append("request")
                return original_request(store, request_id)

            def run(store, request_id, run_id):
                lock_order.append("run")
                return original_run(store, request_id, run_id)

            def attempt(store, identity):
                lock_order.append("attempt")
                return original_attempt(store, identity)

            with mock.patch.object(
                PostgresExecutionStore,
                "get_request_for_update",
                request,
            ), mock.patch.object(
                PostgresExecutionStore,
                "get_run_for_request_for_update",
                run,
            ), mock.patch.object(
                EffectAttemptStore,
                "get_for_update",
                attempt,
            ), mock.patch.object(
                RuntimeAuthorityStore,
                "get_active_for_update",
                side_effect=forbidden,
                create=True,
            ), mock.patch.object(
                SecretUseAuthorizationService,
                "authorize_resolution",
                side_effect=forbidden,
            ):
                with self.assertRaises(AssertionError) as caught:
                    self.reconciliation_service(
                        observer,
                        fold_service=fold,
                    ).execute(self.reconciliation_command(current))
            self.assertIs(caught.exception, fold.error)
            self.assertEqual(len(observer.calls), 1)
            self.assertIsNone(observer.calls[0][1])
            self.assertEqual(self.authorization_rows(), ())
            self.assertEqual(
                lock_order,
                ["request", "run", "attempt", "request"],
            )

        with self.subTest(secret_bearing_scope="execution-operate-only"):
            current, intent, _record, authority = self.seed_reconciliation_source(story)
            uses = self.required_secret_uses(current, intent, authority)
            self.assertGreater(len(uses), 0)
            observer = FailIfObserver("execution-only claimant reached observer")
            fold = FailIfFold("execution-only claimant reached fold")
            with self.assertRaises(EffectAttemptReconciliationDenied) as caught:
                self.reconciliation_service(observer, fold_service=fold).execute(
                    self.reconciliation_command(
                        current,
                        scopes=(PolicyScope.EXECUTION_OPERATE,),
                    )
                )
            self.assertEqual(str(caught.exception), AUTHORITY_ERROR)
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
            self.assertEqual(self.authorization_rows(), ())
            self.assertEqual(observer.calls, [])
            self.assertEqual(fold.calls, [])

        with self.subTest(world="no-reference-product"):
            current, intent, _record, _authority = self.seed_reconciliation_source(
                story,
                authority_ref=False,
            )
            uses = self.required_secret_uses(current, intent, None)
            self.assertGreater(len(uses), 0)
            self.admit_secret_uses(uses)
            observer = self.observer_for(story, current, intent)
            fold = FailIfFold("no-reference product control reached fold")
            with mock.patch.object(
                RuntimeAuthorityStore,
                "get_active_for_update",
                side_effect=forbidden,
                create=True,
            ):
                with self.assertRaises(AssertionError) as caught:
                    self.reconciliation_service(
                        observer,
                        fold_service=fold,
                    ).execute(
                        self.reconciliation_command(
                            current,
                            scopes=(
                                PolicyScope.EXECUTION_OPERATE,
                                PolicyScope.SECRET_PROVIDER_USE,
                            ),
                        )
                    )
            self.assertIs(caught.exception, fold.error)
            self.assertEqual(len(observer.calls), 1)
            self.assertIsNone(observer.calls[0][1])
            self.assertEqual(len(self.authorization_rows()), len(uses))

    def test_referenced_authority_is_exact_active_workspace_reference_and_kind(self) -> None:
        story = self.observed_story()
        for remote in (False, True):
            with self.subTest(lawful=remote):
                current, intent, _record, authority = self.seed_reconciliation_source(
                    story,
                    remote=remote,
                )
                calls = []

                def active(store, workspace_id, authority_ref):
                    calls.append((workspace_id, authority_ref))
                    return authority

                uses = self.required_secret_uses(current, intent, authority)
                self.admit_secret_uses(uses)
                with mock.patch.object(
                    RuntimeAuthorityStore,
                    "get_active_for_update",
                    active,
                    create=True,
                ):
                    with self.assertRaises(AssertionError):
                        self.reconciliation_service(
                            FailIfObserver("lawful authority reached observer"),
                            fold_service=FailIfFold(),
                        ).execute(
                            self.reconciliation_command(
                                current,
                                scopes=(
                                    PolicyScope.EXECUTION_OPERATE,
                                    PolicyScope.SECRET_PROVIDER_USE,
                                ),
                            )
                        )
                self.assertEqual(
                    calls,
                    [(intent.source.workspace_id, intent.authority_ref)],
                )
                self.assertNotIn(authority.registration_id, repr(self.reconciliation_command(current)))

        for fault in ("missing", "revoked", "foreign-workspace", "foreign-reference"):
            with self.subTest(fault=fault):
                current, intent, _record, authority = self.seed_reconciliation_source(story)
                side_effect = None
                observed = authority
                if fault == "missing":
                    side_effect = RuntimeAuthorityNotFound("authority-canary")
                elif fault == "revoked":
                    observed = replace(authority, status=RegisteredRuntimeAuthorityStatus.REVOKED)
                elif fault == "foreign-workspace":
                    observed = replace(authority, workspace_id="workspace-foreign")
                elif fault == "foreign-reference":
                    observed = replace(
                        authority,
                        authority_ref=RuntimeAuthorityReference("authority-foreign"),
                    )
                with mock.patch.object(
                    RuntimeAuthorityStore,
                    "get_active_for_update",
                    side_effect=side_effect,
                    return_value=observed,
                    create=True,
                ):
                    with self.assertRaises(EffectAttemptReconciliationDenied) as caught:
                        self.reconciliation_service(
                            FailIfObserver(),
                            fold_service=FailIfFold(),
                        ).execute(self.reconciliation_command(current))
                self.assertEqual(str(caught.exception), AUTHORITY_ERROR)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

    def test_malformed_authority_is_conflict_and_raw_selector_faults_escape(self) -> None:
        story = self.observed_story()
        expected = RuntimeAuthorityRegistrationError("row-canary")
        for error in (expected, TypeError("raw-type-canary"), RuntimeError("raw-runtime-canary")):
            with self.subTest(error=type(error).__name__):
                current, _intent, _record, _authority = self.seed_reconciliation_source(story)
                with mock.patch.object(
                    RuntimeAuthorityStore,
                    "get_active_for_update",
                    side_effect=error,
                    create=True,
                ):
                    if error is expected:
                        with self.assertRaises(EffectAttemptReconciliationConflict) as caught:
                            self.reconciliation_service(
                                FailIfObserver(),
                                fold_service=FailIfFold(),
                            ).execute(self.reconciliation_command(current))
                        self.assertEqual(str(caught.exception), INVALID_TRUTH_ERROR)
                        self.assertIsNone(caught.exception.__cause__)
                        self.assertIsNone(caught.exception.__context__)
                    else:
                        with self.assertRaises(type(error)) as caught:
                            self.reconciliation_service(
                                FailIfObserver(),
                                fold_service=FailIfFold(),
                            ).execute(self.reconciliation_command(current))
                        self.assertIs(caught.exception, error)

    def test_grants_are_exact_deduplicated_retry_stable_and_outside_observer(self) -> None:
        story = self.observed_story()
        current, intent, _record, authority = self.seed_reconciliation_source(
            story,
            remote=True,
        )
        uses = self.required_secret_uses(current, intent, authority)
        self.admit_secret_uses(uses)
        ledger = UnitOfWorkLedger(self.unit_of_work)
        observer_error = RuntimeError("observer-stop-canary")
        observer = self.observer_for(
            story,
            current,
            intent,
            ledger=ledger,
            error=observer_error,
        )
        authorization_commands = []
        original = SecretUseAuthorizationService.authorize_resolution

        def authorize(service, candidate):
            authorization_commands.append(candidate)
            return original(service, candidate)

        command = self.reconciliation_command(
            current,
            scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE),
        )
        with self.lease_observation("2030-01-01T00:00:02Z"), mock.patch.object(
            SecretUseAuthorizationService,
            "authorize_resolution",
            authorize,
        ):
            with self.assertRaises(RuntimeError) as caught:
                self.reconciliation_service(
                    observer,
                    ledger=ledger,
                    fold_service=FailIfFold(),
                ).execute(command)
        self.assertIs(caught.exception, observer_error)
        first = self.authorization_rows()
        self.assertEqual(len(first), len(uses))
        expected_commands = tuple(
            self.authorization_command(
                current,
                intent,
                reference,
                use_intent,
                command=command,
            )
            for reference, use_intent in uses
        )
        self.assertEqual(tuple(authorization_commands), expected_commands)
        self.assertTrue(
            all(
                candidate.actor_scopes == command.authority.scopes
                for candidate in expected_commands
            )
        )
        self.assertEqual(
            tuple(_authorization_row_projection(row) for row in first),
            tuple(
                _authorization_command_projection(candidate)
                for candidate in expected_commands
            ),
        )
        self.assertEqual(ledger.active, 0)
        self.assertEqual(ledger.entries, 1 + len(uses))
        self.assertEqual(ledger.entries, ledger.exits)

        later = RuntimeError("observer-retry-stop-canary")
        retry_observer = self.observer_for(
            story,
            current,
            intent,
            ledger=ledger,
            error=later,
        )
        authorization_commands.clear()
        with self.lease_observation("2030-01-01T00:00:09Z"), mock.patch.object(
            SecretUseAuthorizationService,
            "authorize_resolution",
            authorize,
        ):
            with self.assertRaises(RuntimeError) as caught:
                self.reconciliation_service(
                    retry_observer,
                    ledger=ledger,
                    fold_service=FailIfFold(),
                ).execute(command)
        self.assertIs(caught.exception, later)
        expected_retry_commands = tuple(
            self.authorization_command(
                current,
                intent,
                reference,
                use_intent,
                command=command,
                requested_at="2030-01-01T00:00:09Z",
            )
            for reference, use_intent in uses
        )
        self.assertEqual(tuple(authorization_commands), expected_retry_commands)
        self.assertEqual(self.authorization_rows(), first)
        self.assertEqual(ledger.entries, 2 * (1 + len(uses)))
        self.assertEqual(ledger.entries, ledger.exits)

    def test_partial_grant_retry_and_changed_actor_preserve_auditable_truth(self) -> None:
        story = self.observed_story()
        current, intent, _record, authority = self.seed_reconciliation_source(
            story,
            remote=True,
        )
        uses = self.required_secret_uses(current, intent, authority)
        self.assertGreaterEqual(len(uses), 2)
        self.admit_secret_uses(uses)
        original = SecretUseAuthorizationService.authorize_resolution
        calls = 0

        def fail_second(service, command):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SecretProviderRegistrationError("second-grant-canary")
            return original(service, command)

        command = self.reconciliation_command(
            current,
            scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE),
        )
        with self.lease_observation("2030-01-01T00:00:02Z"), mock.patch.object(
            SecretUseAuthorizationService,
            "authorize_resolution",
            fail_second,
        ):
            with self.assertRaises(EffectAttemptReconciliationDenied) as caught:
                self.reconciliation_service(
                    FailIfObserver(),
                    fold_service=FailIfFold(),
                ).execute(command)
        self.assertEqual(str(caught.exception), AUTHORITY_ERROR)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        committed_prefix = self.authorization_rows()
        self.assertEqual(len(committed_prefix), 1)

        observer_error = RuntimeError("resume-after-grants-canary")
        with self.lease_observation("2030-01-01T00:00:09Z"):
            with self.assertRaises(RuntimeError) as caught:
                self.reconciliation_service(
                    self.observer_for(story, current, intent, error=observer_error),
                    fold_service=FailIfFold(),
                ).execute(command)
        self.assertIs(caught.exception, observer_error)
        first_actor_rows = self.authorization_rows()
        self.assertEqual(len(first_actor_rows), len(uses))
        self.assertEqual(first_actor_rows[:1], committed_prefix)
        same_actor_commands = tuple(
            self.authorization_command(
                current,
                intent,
                reference,
                use_intent,
                command=command,
                requested_at=(
                    "2030-01-01T00:00:02Z"
                    if position == 0
                    else "2030-01-01T00:00:09Z"
                ),
            )
            for position, (reference, use_intent) in enumerate(uses)
        )
        self.assertEqual(
            tuple(_authorization_row_projection(row) for row in first_actor_rows),
            tuple(
                _authorization_command_projection(candidate)
                for candidate in same_actor_commands
            ),
        )

        self.replace_current_claim(worker_id="worker-b", generation=8)
        new_actor_error = RuntimeError("new-actor-stop-canary")
        changed_actor = self.reconciliation_command(
            current,
            worker_id="worker-b",
            generation=8,
            scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE),
        )
        with self.lease_observation("2030-01-01T00:00:10Z"):
            with self.assertRaises(RuntimeError) as caught:
                self.reconciliation_service(
                    self.observer_for(story, current, intent, error=new_actor_error),
                    fold_service=FailIfFold(),
                ).execute(changed_actor)
        self.assertIs(caught.exception, new_actor_error)
        all_rows = self.authorization_rows()
        self.assertEqual(len(all_rows), 2 * len(uses))
        self.assertEqual({row[4] for row in all_rows}, {"worker-a", "worker-b"})
        self.assertEqual(
            tuple(row for row in all_rows if row[4] == "worker-a"),
            first_actor_rows,
        )
        changed_actor_commands = tuple(
            self.authorization_command(
                current,
                intent,
                reference,
                use_intent,
                command=changed_actor,
                requested_at="2030-01-01T00:00:10Z",
            )
            for reference, use_intent in uses
        )
        self.assertTrue(
            all(
                candidate.actor_scopes == changed_actor.authority.scopes
                for candidate in changed_actor_commands
            )
        )
        self.assertEqual(
            tuple(
                _authorization_row_projection(row)
                for row in all_rows
                if row[4] == "worker-b"
            ),
            tuple(
                _authorization_command_projection(candidate)
                for candidate in changed_actor_commands
            ),
        )

    def test_expected_authorization_errors_are_fixed_and_unexpected_faults_are_raw(self) -> None:
        story = self.observed_story()
        current, intent, _record, authority = self.seed_reconciliation_source(story)
        use = self.required_secret_uses(current, intent, authority)[0]
        command = self.reconciliation_command(
            current,
            scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE),
        )
        for error in (
            SecretProviderAuthorizationDenied("denied-canary"),
            SecretProviderRegistrationError("malformed-canary"),
            SecretUseAuthorizationConflict("conflict-canary"),
        ):
            with self.subTest(expected=type(error).__name__):
                with mock.patch.object(
                    effect_attempt_reconciliation_interpreter,
                    "required_secret_uses_for_runtime_effect",
                    return_value=(use,),
                    create=True,
                ), mock.patch.object(
                    SecretUseAuthorizationService,
                    "authorize_resolution",
                    side_effect=error,
                ):
                    with self.assertRaises(EffectAttemptReconciliationDenied) as caught:
                        self.reconciliation_service(
                            FailIfObserver(),
                            fold_service=FailIfFold(),
                        ).execute(command)
                self.assertEqual(str(caught.exception), AUTHORITY_ERROR)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

        for error_type in (TypeError, RuntimeError):
            with self.subTest(raw=error_type.__name__):
                error = error_type("raw-authorizer-canary")
                with mock.patch.object(
                    effect_attempt_reconciliation_interpreter,
                    "required_secret_uses_for_runtime_effect",
                    return_value=(use,),
                    create=True,
                ), mock.patch.object(
                    SecretUseAuthorizationService,
                    "authorize_resolution",
                    side_effect=error,
                ):
                    with self.assertRaises(error_type) as caught:
                        self.reconciliation_service(
                            FailIfObserver(),
                            fold_service=FailIfFold(),
                        ).execute(command)
                self.assertIs(caught.exception, error)


if __name__ == "__main__":
    unittest.main()
