from __future__ import annotations

import unittest

from control_plane_kit_core.operations import (
    ContractEnforcementOwner,
    DurableStoreKind,
    FailureVisibilityPolicy,
    InvalidPersistenceBoundaryContract,
    MutationPhaseKind,
    MutationSubjectKind,
    PersistenceBoundaryContractSet,
    PersistenceHandoffKind,
    StoreOrderingPolicy,
    canonical_persistence_boundary_contract_set,
)


class PersistenceBoundaryContractTests(unittest.TestCase):
    def test_canonical_contract_covers_stores_mutations_and_handoffs(self) -> None:
        contract = canonical_persistence_boundary_contract_set()

        self.assertEqual({store.store for store in contract.stores}, set(DurableStoreKind))
        self.assertEqual(
            {handoff.kind for handoff in contract.handoffs},
            set(PersistenceHandoffKind),
        )
        self.assertEqual(
            {(mutation.subject, mutation.phase) for mutation in contract.mutations},
            {
                (subject, phase)
                for subject in MutationSubjectKind
                for phase in MutationPhaseKind
            },
        )

    def test_descriptor_round_trips_strictly_and_rejects_open_values(self) -> None:
        contract = canonical_persistence_boundary_contract_set()
        descriptor = contract.descriptor()

        self.assertEqual(PersistenceBoundaryContractSet.from_descriptor(descriptor), contract)

        with self.assertRaises(InvalidPersistenceBoundaryContract):
            PersistenceBoundaryContractSet.from_descriptor(
                {**descriptor, "repository": "PostgresStore"}
            )

        broken = {
            **descriptor,
            "stores": [
                {**descriptor["stores"][0], "store": "mystery-store"},
                *descriptor["stores"][1:],
            ],
        }
        with self.assertRaises(InvalidPersistenceBoundaryContract):
            PersistenceBoundaryContractSet.from_descriptor(broken)

    def test_store_contracts_name_durable_roles_without_database_implementation(self) -> None:
        contract = canonical_persistence_boundary_contract_set()
        ordering = {store.store: store.ordering_policy for store in contract.stores}

        self.assertIs(
            ordering[DurableStoreKind.ACTIVITY_HISTORY],
            StoreOrderingPolicy.APPEND_ONLY_ORDINAL,
        )
        self.assertIs(
            ordering[DurableStoreKind.OBSERVED_STATE],
            StoreOrderingPolicy.LATEST_BY_TIME_THEN_ID,
        )
        self.assertIs(
            ordering[DurableStoreKind.OPERATION_LEDGER],
            StoreOrderingPolicy.UNIQUE_SCOPED_IDEMPOTENCY,
        )

        rendered = repr(contract.descriptor()).lower()
        self.assertNotIn("psycopg", rendered)
        self.assertNotIn("sqlalchemy", rendered)

        for store in contract.stores:
            with self.subTest(store=store.store):
                self.assertFalse(store.accepts_secret_values)
                self.assertTrue(store.stores_never_commit)
                self.assertIs(
                    store.enforcement_owner,
                    ContractEnforcementOwner.OPERATIONS,
                )

    def test_handoffs_keep_uow_schema_and_repositories_outside_core(self) -> None:
        contract = canonical_persistence_boundary_contract_set()

        for handoff in contract.handoffs:
            with self.subTest(handoff=handoff.kind):
                self.assertTrue(handoff.requires_unit_of_work)
                self.assertTrue(handoff.requires_caller_owned_transaction)
                self.assertFalse(handoff.allows_core_database_driver)
                self.assertFalse(handoff.allows_core_schema_ddl)
                self.assertIs(
                    handoff.enforcement_owner,
                    ContractEnforcementOwner.OPERATIONS,
                )

    def test_mutation_contracts_preserve_candidate_identity_without_values(self) -> None:
        contract = canonical_persistence_boundary_contract_set()
        by_phase = {
            mutation.phase: mutation
            for mutation in contract.mutations
            if mutation.subject is MutationSubjectKind.DERIVED_RESOURCE
        }

        self.assertTrue(by_phase[MutationPhaseKind.PUBLISH].requires_candidate)
        self.assertTrue(
            by_phase[MutationPhaseKind.CLEANUP_SUPERSEDED].requires_candidate
        )
        self.assertFalse(by_phase[MutationPhaseKind.PREPARE_CANDIDATE].publishes_values)
        self.assertIs(
            by_phase[MutationPhaseKind.CLEANUP_SUPERSEDED].failure_visibility,
            FailureVisibilityPolicy.OPERATOR_VISIBLE_UNCERTAINTY,
        )

        for mutation in contract.mutations:
            with self.subTest(subject=mutation.subject, phase=mutation.phase):
                self.assertFalse(mutation.publishes_values)
                self.assertIs(
                    mutation.enforcement_owner,
                    ContractEnforcementOwner.OPERATIONS,
                )

    def test_invalid_contracts_fail_before_mutable_holder_behavior_enters_core(self) -> None:
        descriptor = canonical_persistence_boundary_contract_set().descriptor()

        store = dict(descriptor["stores"][0])
        store["accepts_secret_values"] = True
        with self.assertRaises(InvalidPersistenceBoundaryContract):
            PersistenceBoundaryContractSet.from_descriptor(
                {**descriptor, "stores": [store, *descriptor["stores"][1:]]}
            )

        handoff = dict(descriptor["handoffs"][0])
        handoff["allows_core_database_driver"] = True
        with self.assertRaises(InvalidPersistenceBoundaryContract):
            PersistenceBoundaryContractSet.from_descriptor(
                {**descriptor, "handoffs": [handoff, *descriptor["handoffs"][1:]]}
            )

        mutation = dict(descriptor["mutations"][0])
        mutation["publishes_values"] = True
        with self.assertRaises(InvalidPersistenceBoundaryContract):
            PersistenceBoundaryContractSet.from_descriptor(
                {**descriptor, "mutations": [mutation, *descriptor["mutations"][1:]]}
            )


if __name__ == "__main__":
    unittest.main()
