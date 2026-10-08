"""B2 stage 1: explicit profile values/storage, not v2 cleanup permission."""
from dataclasses import fields, replace
from hashlib import sha256
import unittest

import psycopg
import rfc8785

from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_operations.configuration_cleanup import (
    ConfigurationCleanupExpectedContext, ConfigurationCleanupSourceSelector,
)
from control_plane_kit_operations.configuration_cleanup_planning import (
    InspectConfigurationCleanup, RequestConfigurationCleanupPlan, _fingerprint,
)
from control_plane_kit_operations.configuration_preparation import ConfigurationAcceptedTransferRecord
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile as Profile
from control_plane_kit_operations.postgres import install_schema, SchemaInstallationError
from control_plane_kit_operations.postgres.current_data_validation import validate_current_rows, CurrentRowDrift
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.configuration_cleanup_contract_fixture import proposal_wire
from tests.configuration_cleanup_history_fixture import ConfigurationCleanupHistoryFixture


class ConfigurationCleanupCommandProfileTests(unittest.TestCase):
    def commands(self):
        document = proposal_wire()
        context, candidate = document["context"], document["candidates"][0]
        source = candidate["seed"]["source_identity"]
        pins = ConfigurationCleanupExpectedContext(**{name: context[name] for name in (
            "base_graph_id", "base_realized_projection_id", "desired_graph_id",
            "desired_realized_projection_id", "desired_graph_revision")})
        selector = ConfigurationCleanupSourceSelector(EffectAttemptIdentity(RunId(source["run_id"]),
            source["activity_id"], source["attempt"]), candidate["seed"]["artifact_id"],
            ConfigurationInstanceRefCodec().decode(candidate["ref"]))
        query = InspectConfigurationCleanup(context["session_id"], context["workspace_id"], pins, (selector,))
        request = RequestConfigurationCleanupPlan(query.session_id, query.workspace_id,
            IdempotencyKey("cleanup-profile"), pins, query.selectors, "a" * 64)
        return query, request

    def require_profile(self, command):
        self.assertTrue(hasattr(command, "profile"), "B2 command profile selection is missing")

    def test_profile_is_closed_keyword_only_and_defaults_to_legacy(self):
        for command in self.commands():
            self.require_profile(command)
            self.assertIs(command.profile, Profile.CONFIGURATION_CLEANUP_V1)
            self.assertTrue(next(field for field in fields(command) if field.name == "profile").kw_only)
            self.assertEqual(replace(command, profile=Profile.CONFIGURATION_CLEANUP_V1), command)
            self.assertIs(replace(command, profile=Profile.CONFIGURATION_CLEANUP_V2).profile,
                Profile.CONFIGURATION_CLEANUP_V2)
            for invalid in (None, "configuration-cleanup-v2", "unknown", Profile.STRUCTURAL_V1):
                with self.subTest(kind=type(command).__name__, invalid=invalid), self.assertRaises(ValueError):
                    replace(command, profile=invalid)

    def test_v1_fingerprint_bytes_survive_and_v2_has_a_distinct_exact_domain(self):
        _, command = self.commands()
        # Independent literal historical dictionary: do not derive it from the
        # implementation's new profile branch or a freshly stored receipt.
        value = dict(profile="configuration-cleanup-command.v1", session_id=command.session_id,
            workspace_id=command.workspace_id, actor_id="operator", idempotency_key=command.idempotency_key.value,
            expected_context=command.expected_context.descriptor(),
            selectors=[selector.descriptor() for selector in command.selectors],
            expected_inspection_fingerprint=command.expected_inspection_fingerprint)
        expected = sha256(rfc8785.dumps(value)).hexdigest()
        self.assertEqual(_fingerprint(command, "operator"), expected)
        self.require_profile(command)
        self.assertEqual(_fingerprint(replace(command, profile=Profile.CONFIGURATION_CLEANUP_V1), "operator"), expected)
        v2 = replace(command, profile=Profile.CONFIGURATION_CLEANUP_V2)
        changed = sha256(rfc8785.dumps(value | {"profile": "configuration-cleanup-command.v2"})).hexdigest()
        self.assertEqual(_fingerprint(v2, "operator"), changed)
        self.assertNotEqual(changed, expected)


class ConfigurationCleanupReservationProfileTests(ConfigurationCleanupHistoryFixture, unittest.TestCase):
    def retained(self):
        self.retain_cleanup()
        record = self.read_retained()
        self.assertTrue(hasattr(record, "derivation_profile"), "B2 closed reservation profile is missing")
        self.assertTrue(hasattr(record, "accepted_transfers"), "B2 positive transfer representation is missing")
        return record

    def represented_transfers(self, record):
        # Pure value construction only. These rows are NOT inserted as accepted
        # transfers, and the cleanup-closed fixture has no v2 execution authority.
        completions = {value.identity: value for value in record.completions}
        return tuple(ConfigurationAcceptedTransferRecord(member.identity, member.ref, 0,
            completions[member.identity].request_fingerprint,
            completions[member.identity].selection_fingerprint,
            completions[member.identity].outcome_fingerprint)
            for member in sorted(record.members, key=lambda value: (
                value.identity.run_id.value, value.identity.activity_id, value.identity.attempt, value.ref.artifact_id)))

    def test_v2_zero_closures_require_positive_birth_values_without_relaxing_v1(self):
        record = self.retained()
        self.assertIs(record.derivation_profile, Profile.CONFIGURATION_CLEANUP_V1)
        self.assertEqual(record.accepted_transfers, ())
        transfers = self.represented_transfers(record)
        zero = replace(record, derivation_profile=Profile.CONFIGURATION_CLEANUP_V2,
            claims=(), completions=(), accepted_transfers=transfers)
        self.assertEqual(zero.members, record.members)
        self.assertEqual((zero.claims, zero.completions), ((), ()))
        for changes in (dict(claims=(), completions=()), dict(accepted_transfers=transfers),
                dict(derivation_profile="configuration-cleanup-v2"), dict(derivation_profile=Profile.STRUCTURAL_V1)):
            with self.subTest(changes=tuple(changes)), self.assertRaises(OperationsRecordError):
                replace(record, **changes)
        for changes in (dict(accepted_transfers=()), dict(accepted_transfers=transfers[:-1]),
                dict(accepted_transfers=transfers + transfers[:1]), dict(completions=record.completions),
                dict(claims=record.claims), dict(derivation_profile=Profile.CONFIGURATION_CLEANUP_V1)):
            with self.subTest(changes=tuple(changes)), self.assertRaises(OperationsRecordError):
                replace(zero, **changes)
        # Negative root premises each start from the complete valid value.
        foreign = replace(transfers[0], acceptance_revision=1,
            ref=replace(transfers[0].ref, allocation_id="foreign-allocation"))
        with self.assertRaises(OperationsRecordError):
            replace(zero, accepted_transfers=(foreign, *transfers[1:]))

    def test_v2_mixed_members_keep_disjoint_claim_and_transfer_coverage(self):
        record = self.retained()
        self.assertGreaterEqual(len(record.members), 2)
        transfers = self.represented_transfers(record)
        transfer = transfers[0]
        remaining = tuple(claim for claim in record.claims if claim.ref != transfer.ref)
        mixed = replace(record, derivation_profile=Profile.CONFIGURATION_CLEANUP_V2,
            claims=remaining, accepted_transfers=(transfer,))
        self.assertEqual(mixed.completions, record.completions)
        self.assertEqual(len(mixed.members), len(record.members))
        with self.assertRaises(OperationsRecordError):
            replace(mixed, claims=record.claims)
        with self.assertRaises(OperationsRecordError):
            replace(mixed, accepted_transfers=())
        with self.assertRaises(OperationsRecordError):
            replace(record, claims=remaining)

    def require_profile_column(self):
        self.assertEqual(self.connection.execute("SELECT data_type,is_nullable,column_default "
            "FROM information_schema.columns WHERE table_schema=current_schema() "
            "AND table_name='cpk_configuration_cleanup_reservations' AND column_name='derivation_profile'").fetchall(),
            [("text", "NO", "'configuration-cleanup-v1'::text")], "B2 closed reservation column is missing")

    def test_profiled_sql_counts_preserve_v1_and_allow_only_symmetric_v2_zero(self):
        self.require_profile_column()
        self.retain_cleanup()
        self.assertEqual(self.connection.execute("SELECT DISTINCT derivation_profile "
            "FROM cpk_configuration_cleanup_reservations").fetchall(), [("configuration-cleanup-v1",)])
        before = self.cleanup_snapshot()
        cases = (("configuration-cleanup-v1", 1, 0, 0, False),
            ("configuration-cleanup-v1", 2, 1, 1, False),
            ("configuration-cleanup-v1", 2, 1, 2, True),
            ("configuration-cleanup-v2", 2, 0, 0, True),
            ("configuration-cleanup-v2", 2, 1, 1, True),
            ("configuration-cleanup-v2", 1, 0, 1, False),
            ("configuration-cleanup-v2", 1, 1, 0, False),
            ("configuration-cleanup-v2", 0, 0, 0, False),
            ("configuration-cleanup-v2", 33, 0, 0, False),
            ("configuration-cleanup-v2", 1, 257, 257, False),
            ("unknown", 1, 1, 1, False))
        for profile, members, invocations, claims, allowed in cases:
            with self.subTest(profile=profile, counts=(members, invocations, claims)), self.unit_of_work() as uow:
                args = (profile, members, invocations, claims)
                sql = "UPDATE cpk_configuration_cleanup_reservations SET derivation_profile=%s," \
                    "candidate_count=%s,invocation_count=%s,claim_count=%s"
                if allowed:
                    uow.stores.connection.execute(sql, args)
                else:
                    with self.assertRaises(psycopg.errors.CheckViolation):
                        uow.stores.connection.execute(sql, args)
                # Never commit these count-only representation premises.
        self.assertEqual(self.cleanup_snapshot(), before)

    def test_profile_cannot_relabel_original_v1_plan_or_weaken_current_verification(self):
        self.require_profile_column()
        identity = self.retain_cleanup()
        before = self.cleanup_snapshot()
        install_schema(self.connection)
        self.assertEqual(self.cleanup_snapshot(), before)
        with self.unit_of_work() as uow:
            uow.stores.connection.execute("UPDATE cpk_configuration_cleanup_reservations "
                "SET derivation_profile='configuration-cleanup-v2'")
            with self.assertRaises(OperationsRecordError):
                self.ownership(uow.stores).get(identity)
            with self.assertRaises(CurrentRowDrift):
                validate_current_rows(uow.stores.connection)
        self.assertEqual(self.cleanup_snapshot(), before)
        with self.unit_of_work() as uow:
            uow.stores.connection.execute("ALTER TABLE cpk_configuration_cleanup_reservations "
                "DROP CONSTRAINT cpk_cleanup_reservations_counts_check")
            with self.assertRaises(SchemaInstallationError):
                install_schema(uow.stores.connection)
        self.assertEqual(self.cleanup_snapshot(), before)
        install_schema(self.connection)


if __name__ == "__main__":
    unittest.main()
