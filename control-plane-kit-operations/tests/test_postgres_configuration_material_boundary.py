"""#1923 supplement: original replay and complete pinned material ownership."""
from dataclasses import replace
import json
import unittest

import psycopg

from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartConflict
from control_plane_kit_operations.coordinator import ExecutionCoordinatorConflict
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from tests.configuration_preparation_fixture import ConfigurationPreparationFixture
from tests import postgres_effect_attempt_coordinator_fixture as coordinator_fixture
from tests.test_postgres_configuration_evidence import _ObservedConnection, _ObservedRows
from tests.test_runtime_effect_translation import _registered_product


def _catalog_read(query, observations):
    text = str(query).lower().strip()
    if text.startswith(("select", "with")) and any(table in text for table in (
            "cpk_registered_products", "cpk_image_pull_authorities", "cpk_runtime_authorities",
            "cpk_runtime_authority_deliveries", "cpk_ingress_authorities",
            "cpk_cloudflare_ingress_resources", "cpk_generated_ingress_secret_references")):
        observations.setdefault("catalog_reads", []).append(text)


def _contains_identity(value, identities):
    if isinstance(value, dict):
        return any(_contains_identity(item, identities) for item in value.values())
    if isinstance(value, (tuple, list)):
        return any(_contains_identity(item, identities) for item in value)
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        if value in identities:
            return True
        if value.startswith(("{", "[")):
            try:
                decoded = json.loads(value)
            except ValueError:
                return False
            return _contains_identity(decoded, identities)
    return False


class _CatalogObservedRows(_ObservedRows):
    def execute(self, query, *args, **kwargs):
        _catalog_read(query, self.observations)
        return super().execute(query, *args, **kwargs)

    def _record(self, row):
        result = super()._record(row)
        if row is not None and "cpk_registered_products" in self.query.lower():
            if _contains_identity(row, self.observations.get("unrelated_identities", ())):
                self.observations["unrelated_rows"] = self.observations.get("unrelated_rows", 0) + 1
        return result


class _CatalogObservedConnection(_ObservedConnection):
    def execute(self, query, *args, **kwargs):
        _catalog_read(query, self.observations)
        self.observations["bytes"] += 256
        self.observations["statements"] = self.observations.get("statements", 0) + 1
        return _CatalogObservedRows(self.connection.execute(query, *args, **kwargs), self.observations, query)

    def cursor(self, *args, **kwargs):
        return _CatalogObservedRows(self.connection.cursor(*args, **kwargs), self.observations)


class PostgresConfigurationMaterialBoundaryTests(ConfigurationPreparationFixture, unittest.TestCase):
    coordinator_command = coordinator_fixture.PostgresEffectAttemptCoordinatorFixture.coordinator_command
    coordinator_harness = coordinator_fixture.PostgresEffectAttemptCoordinatorFixture.coordinator_harness

    def test_original_coordinator_replay_after_catalog_revocation_performs_no_fresh_selection(self):
        command = self.configuration_command()
        original = self.start_service("configuration-original").execute(command)
        with self.unit_of_work() as uow:
            uow.stores.registered_products.revoke("workspace-a", self.configuration_product.reference)
            uow.commit()
        before = self.complete_start_snapshot()
        observed = {"rows": 0, "bytes": 0, "largest_cell": 0, "catalog_reads": []}
        original_uow = self.unit_of_work
        self.unit_of_work = lambda: PostgresUnitOfWork(lambda: _CatalogObservedConnection(
            psycopg.connect(self.database_url), observed))
        adapter = coordinator_fixture.RecordingRuntimeAdapter()
        harness = self.coordinator_harness(adapter=adapter)
        boundary = RuntimeError("original observation handoff")

        def observation_only():
            raise boundary

        harness.reconciliation.before_execute = observation_only
        caught = None
        try:
            harness.coordinator.execute(self.coordinator_command())
        except RuntimeError as error:
            caught = error
        finally:
            self.unit_of_work = original_uow
        self.assertEqual(observed["catalog_reads"], [], "original replay selected a fresh catalog")
        self.assertIs(caught, boundary)
        self.assertEqual(harness.start.commands[0].intent, command.intent)
        self.assertEqual(harness.reconciliation.commands[0].identity, original.attempt.state.identity)
        self.assertEqual(adapter.runtime_calls, [])
        self.assertEqual(harness.lifecycle.commands, [])
        self.assertEqual(harness.start_ids.calls, [])
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_self_consistent_non_configuration_material_drift_refuses_before_ids_or_writes(self):
        original = self.intent()
        material = original.products[0]
        changed_product = replace(material.product, image=replace(material.product.image, digest="sha256:" + "b" * 64))
        forged = replace(original, products=(replace(material, product=changed_product),))
        self.assertEqual(forged.configuration_instances, original.configuration_instances)
        before = self.complete_start_snapshot()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.assertRaises(EffectAttemptStartConflict):
            service.execute(self.configuration_command(forged))
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_fresh_proposal_does_not_transport_unrelated_active_products(self):
        unrelated = set()
        for index in range(8):
            name = f"unrelated-b1-product-{index}"
            product = _registered_product(name=name)
            with self.unit_of_work() as uow:
                registered = uow.stores.registered_products.register(workspace_id="workspace-a",
                    descriptor_document=product.descriptor_document, source=product.source,
                    imported_by=product.imported_by, imported_at=product.imported_at)
                uow.commit()
            unrelated.update((name, registered.registration_id, registered.reference.descriptor_sha256.value))
        before = self.complete_start_snapshot()
        observed = {"rows": 0, "bytes": 0, "largest_cell": 0, "unrelated_rows": 0,
            "unrelated_identities": unrelated}
        original_uow = self.unit_of_work
        self.unit_of_work = lambda: PostgresUnitOfWork(lambda: _CatalogObservedConnection(
            psycopg.connect(self.database_url), observed))
        harness = self.coordinator_harness()
        boundary = RuntimeError("proposal reached existing start owner")

        def inspect_proposal():
            raise boundary

        harness.start.before_execute = inspect_proposal
        try:
            with self.assertRaises(RuntimeError) as caught:
                harness.coordinator.execute(self.coordinator_command())
        finally:
            self.unit_of_work = original_uow
        self.assertIs(caught.exception, boundary)
        self.assertEqual(observed["unrelated_rows"], 0)
        self.assertGreater(observed["rows"], 0)
        self.assertLessEqual(observed["rows"], 4096)
        self.assertLessEqual(observed["bytes"], 16 * 1024 * 1024)
        self.assertEqual(harness.lifecycle.commands, [])
        self.assertEqual(harness.start_ids.calls, [])
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_fresh_owner_rechecks_catalog_after_optimistic_proposal(self):
        harness = self.coordinator_harness()
        after_revocation = []

        def revoke_after_proposal():
            with self.unit_of_work() as uow:
                uow.stores.registered_products.revoke("workspace-a", self.configuration_product.reference)
                uow.commit()
            after_revocation.append(self.complete_start_snapshot())

        harness.start.before_execute = revoke_after_proposal
        with self.assertRaises(ExecutionCoordinatorConflict):
            harness.coordinator.execute(self.coordinator_command())
        self.assertEqual(len(harness.start.commands), 1)
        self.assertEqual(harness.start_ids.calls, [])
        self.assertEqual(harness.adapter.runtime_calls, [])
        self.assertEqual(harness.lifecycle.commands, [])
        self.assertEqual(len(after_revocation), 1)
        self.assertEqual(self.complete_start_snapshot(), after_revocation[0])
        with self.unit_of_work() as uow:
            with self.assertRaises(KeyError):
                uow.stores.effect_attempts.get(self.identity(activity_id="start-api"))
