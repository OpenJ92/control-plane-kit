"""Shared accounting preserves the receiver reader's declared cardinalities."""

import unittest

from control_plane_kit_core.planning import NodeTarget, StartNode
from control_plane_kit_operations._configuration_preparation import _configuration_accounting
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead
from control_plane_kit_operations.postgres.receiver_execution_scopes import _ExecutionScopeStorage
from tests.receiver_execution_scope_fixture import ReceiverExecutionScopeFixture
from tests.postgres_effect_outcome_store_fixture import PostgresEffectOutcomeStoreFixture, store_module


class ReceiverSharedOutcomeEvidenceTests(ReceiverExecutionScopeFixture, unittest.TestCase):
    def test_multiple_real_outcomes_match_standalone_and_missing_outcome_still_refuses(self):
        module = self.require_scopes()
        self.admit_operations("shared-outcomes", StartNode(NodeTarget("app")), StartNode(NodeTarget("app")))
        claimed, completed = self.execute_effects("shared-outcomes")
        self.assertEqual(completed.run.status.value, "succeeded")
        scope = (module.ExecutionReceiverScope("docker", "app"),)
        with self.unit_of_work() as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            standalone = uow.stores.execution.receiver_scope_evidence("workspace-a", scope, guard)
            self.assertEqual(standalone.state, "complete")
            self.assertEqual(len(standalone.requests[0].runs[0].outcomes), 2)
            with _configuration_accounting("shared-outcomes"):
                shared = uow.stores.execution.receiver_scope_evidence("workspace-a", scope, guard)
            self.assertEqual(shared, standalone)
            self.assertEqual(module.classify_receiver_scope_evidence(shared).disposition, "conflict")
            # Deliberate corruption is rolled back with this UoW. Transporting
            # up to N outcomes does not excuse missing exact attempt coverage.
            self.assertEqual(uow.stores.connection.execute(
                "DELETE FROM cpk_effect_attempt_outcomes WHERE run_id=%s AND activity_id="
                "(SELECT min(activity_id) FROM cpk_effect_attempt_outcomes WHERE run_id=%s)",
                (claimed.run.run_id, claimed.run.run_id)).rowcount, 1)
            for accounting in (False, True):
                with _configuration_accounting("missing-outcome", active=accounting):
                    evidence = uow.stores.execution.receiver_scope_evidence("workspace-a", scope, guard)
                self.assertEqual(evidence.state, "unavailable")
        self.assertEqual(self.evidence(*scope).disposition, "conflict")


class ReceiverSharedMembershipEvidenceTests(PostgresEffectOutcomeStoreFixture, unittest.TestCase):
    def test_multiple_and_empty_memberships_match_standalone_and_declared_maximum_is_exact(self):
        # Existing outcome-store specimens are retained history, not live
        # provider evidence. Exercise the same receiver membership decoder.
        populated = self.record_for(self.story_named("execution-succeeded"))
        empty = self.indexed_empty_record(1)
        self.assertEqual(len(populated.endpoint_observations), 2)
        self.assertEqual(empty.endpoint_observations, ())
        for record in (populated, empty):
            self.persist_outcome(record)
            identity = record.attempt.state.identity
            row = self.connection.execute("SELECT " + ",".join(store_module._COLUMN_NAMES)
                + " FROM cpk_effect_attempt_outcomes WHERE run_id=%s AND activity_id=%s AND attempt=%s",
                (identity.run_id.value, identity.activity_id, identity.attempt)).fetchone()
            standalone = _ExecutionScopeStorage(self.connection).outcome_memberships(row, store_module)
            self.assertEqual(len(standalone), len(record.endpoint_observations))
            with _configuration_accounting("shared-memberships"):
                shared = _ExecutionScopeStorage(self.connection, _EvidenceRead(self.connection)).outcome_memberships(
                    row, store_module)
            self.assertEqual(shared, standalone)
            if record is populated:
                for declared in (0, 1, 3):
                    # Too-small and too-large header cardinalities both refuse;
                    # never reinterpret a bounded prefix as complete membership.
                    wrong = (*row[:21], declared, *row[22:])
                    for accounting in (False, True):
                        with _configuration_accounting("wrong-memberships", active=accounting):
                            read = _EvidenceRead(self.connection) if accounting else None
                            with self.assertRaises(ValueError):
                                _ExecutionScopeStorage(self.connection, read).outcome_memberships(wrong, store_module)
