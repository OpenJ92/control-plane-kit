"""Recorded source/protection evidence; never a supported reuse admission.

These fixtures use real canonical codecs, events, original commitments and
PostgreSQL constraints. Raw history insertion intentionally does not call guarded
production writers and does not manufacture a prepared guard or B2 receipt.
"""
from dataclasses import replace
from hashlib import sha256

import rfc8785

from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.planning import ActivityId
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.postgres.effect_attempt_store import _COLUMN_NAMES, _record_values
from tests.configuration_preparation_fixture import ConfigurationPreparationFixture


class ConfigurationEvidenceHistoryFixture(ConfigurationPreparationFixture):
    def source_reader(self, stores):
        reader = getattr(stores.effect_attempt_intents, "read_configuration_source", None)
        self.assertTrue(callable(reader), "missing #1923 compact original-source reader")
        return reader

    def allocation_reader(self, stores):
        owner = getattr(stores, "configuration_preparation", None)
        reader = getattr(owner, "read_allocation_evidence", None)
        self.assertTrue(callable(reader), "missing #1923 bounded allocation-evidence reader")
        return reader

    def seed_recorded_source(self, *, intent=None, ordinal=3, preimage=None, fingerprint=None,
                             include_attempt=True):
        intent = self.intent() if intent is None else intent
        attempt, evidence = self.intent_attempt(intent=intent, activity_id=intent.activity_id.value,
            event_id=f"historical-source-{ordinal}", ordinal=ordinal)
        document = rfc8785.dumps(intent.descriptor()) if preimage is None else preimage
        fingerprint = evidence.request_fingerprint if fingerprint is None else fingerprint
        state = replace(attempt.state, request_fingerprint=fingerprint)
        event = replace(attempt.original_start_event, evidence=self.evidence_for(state))
        attempt = EffectAttemptRecord(state, event, event)
        with self.unit_of_work() as uow:
            uow.stores.execution.add_event(event)
            connection = uow.stores.connection
            connection.execute(
                "INSERT INTO cpk_effect_attempt_intents "
                "(run_id,activity_id,attempt,workspace_id,request_id,request_fingerprint,"
                "original_event_id,original_event_run_id,original_event_ordinal,preimage) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (state.identity.run_id.value, state.identity.activity_id, state.identity.attempt,
                 intent.source.workspace_id, intent.source.request_id, fingerprint,
                 event.event_id, event.run_id, event.ordinal, document))
            if include_attempt:
                connection.execute("INSERT INTO cpk_effect_attempts (" + ",".join(_COLUMN_NAMES)
                    + ") VALUES (" + ",".join("%s" for _ in _COLUMN_NAMES) + ")", _record_values(attempt))
            uow.commit()
        return attempt, intent

    def seed_recorded_claims(self, count):
        self.assertIn(count, (1, 2, 3, 63, 64, 65))
        # Reader capability is checked before any future-schema fixture SQL.
        with self.unit_of_work() as uow:
            self.allocation_reader(uow.stores)
        self.configuration_history_count = count
        self.reset_start_truth()
        original = self.intent()
        refs = original.configuration_instances.instances
        attempts = []
        for index in range(count):
            activity_id = "start-api" if index == 0 else f"history-use-{index:03d}"
            intent = replace(original, activity_id=ActivityId(activity_id))
            attempt, _ = self.seed_recorded_source(intent=intent, ordinal=3 + index)
            attempts.append(attempt)
            with self.unit_of_work() as uow:
                connection = uow.stores.connection
                for ref in refs:
                    encoded = ConfigurationInstanceRefCodec().encode_canonical_bytes(ref)
                    connection.execute(
                        "INSERT INTO cpk_effect_configuration_refs "
                        "(run_id,activity_id,attempt,artifact_id,workspace_id,allocation_id,runtime_id,node_id,"
                        "ref_preimage,ref_digest,request_fingerprint,original_event_id,"
                        "birth_run_id,birth_activity_id,birth_attempt,birth_artifact_id,is_birth) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        ("run-a", activity_id, 1, ref.artifact_id, ref.workspace_id, ref.allocation_id,
                         ref.runtime_id, ref.node_id, encoded, sha256(encoded).hexdigest(),
                         attempt.state.request_fingerprint, attempt.original_start_event.event_id,
                         "run-a", "start-api", 1, ref.artifact_id, index == 0))
                    connection.execute(
                        "INSERT INTO cpk_configuration_claims "
                        "(run_id,activity_id,attempt,artifact_id,workspace_id,allocation_id) "
                        "VALUES (%s,%s,%s,%s,%s,%s)",
                        ("run-a", activity_id, 1, ref.artifact_id, ref.workspace_id, ref.allocation_id))
                uow.commit()
        return refs[0], tuple(attempts)

    def read_source(self, attempt, ref):
        with self.unit_of_work() as uow:
            return self.source_reader(uow.stores)(attempt.state.identity, ref)

    def read_allocation(self, ref):
        with self.unit_of_work() as uow:
            return self.allocation_reader(uow.stores)(ref)

    def assert_allocation_unavailable(self, evidence, *, state="unavailable"):
        self.assertEqual(evidence.state, state)
        self.assertIsNone(evidence.birth)
        self.assertEqual(evidence.claims, ())
