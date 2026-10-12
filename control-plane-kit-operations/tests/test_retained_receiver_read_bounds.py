"""Retained receiver permission stays fresh within a conservative read bound."""
from contextlib import contextmanager
from dataclasses import replace
from unittest import mock

from control_plane_kit_core.topology.graph import Endpoint, LiteralAddress
from control_plane_kit_core.planning import ReconcileNode
from control_plane_kit_operations import _configuration_preparation as support
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
from control_plane_kit_operations.coordinator import ExecutionCoordinatorConflict
from control_plane_kit_operations.postgres import configuration_preparation_store as preparation
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead
from tests import test_managed_application_chain as chain
from tests.test_managed_configuration_update_admission import APPLICATION
from tests import test_managed_configuration_update_execution as original


class RetainedReceiverReadBoundsTests(chain.ManagedApplicationFixture):
    application_artifacts = (APPLICATION,)
    complete_initial = original.ManagedConfigurationUpdateExecutionTests.complete_initial
    prepare_content = original.ManagedConfigurationUpdateExecutionTests.prepare_content
    execute_content = original.ManagedConfigurationUpdateExecutionTests.execute_content

    def authored_graph(self):
        graph, documents, registry = super().authored_graph()
        nodes = dict(graph.nodes)
        target = nodes["api"]
        declaration = chain.WorkloadNodeControlSurfaceDeclaration(
            target.block_spec.control_surfaces[0], chain.WorkloadNodeControlSurfaceDeclarationProfile.V2)
        for name, receiver in (("counter-example", "c" * 32), ("retained-example", "d" * 32)):
            wrapper = chain.artifact("workload", declaration, node=name, receiver=True, receiver_id=receiver)
            environment = chain.wrapper_environment((wrapper,))
            product = replace(documents["api"].product,
                identity=chain.ProductIdentity("test", "chain-" + name, 1),
                runtime_contract=replace(documents["api"].product.runtime_contract,
                    configuration_artifacts=(wrapper,), public_environment=environment))
            document = chain.ProductDescriptorCodec().encode_document(product)
            reference = chain.ProductReference.from_document(document)
            documents[name] = document
            nodes[name] = replace(target, node_id=name,
                block_spec=replace(target.block_spec, role_id=name),
                configuration_artifacts=(wrapper,), public_environment=environment,
                endpoints={"http": Endpoint(LiteralAddress(f"http://{name}:8000"), target.endpoint("http").protocol)},
                metadata={"product_identity": reference.identity.key,
                    "product_descriptor_digest": reference.descriptor_sha256.value})
        routes = chain.management_routes(nodes)
        gateway = nodes["gateway"]
        nodes["gateway"] = replace(gateway, configuration_artifacts=tuple(
            replace(value, content=chain.ReceiverManagementRoutesCodec().encode_bytes(routes).decode(), source_digest=None)
            if value.artifact_id == "gateway-health-targets" else value
            for value in gateway.configuration_artifacts))
        graph = replace(graph, nodes=nodes, runtimes={"docker":
            replace(graph.runtimes["docker"], children=tuple(nodes))})
        graph = self.native_before_path_graph(graph)
        chain.validate_graph(graph).require_valid()
        return graph, documents, registry

    @contextmanager
    def conservative_suffix(self):
        """Observe every prospective query, including reservations refused before SQL."""
        original_budget, original_query = preparation._ordinary_tail_budget, _EvidenceRead.query
        original_bind = preparation._OrdinaryStartReadBoundsOwner.bind
        witnesses = []

        def budget(owner, *args, **kwargs):
            result = original_budget(owner, *args, **kwargs)
            witnesses.append(dict(owner=owner, accounting=owner._accounting,
                before=owner._accounting.used, budget=result[0], peaks=[], settled=None))
            return result

        def query(read, sql, params, *, records, octets, cells, identities=1):
            issued = support._BOUND_ORDINARY_START.get()
            for witness in witnesses:
                if (issued is not None and issued.owner is witness["owner"]
                        and read.accounting is witness["accounting"]):
                    reserve = ConfigurationEvidenceFootprint(records * identities, octets, records * cells, 1)
                    witness["peaks"].append(read.used.plus(reserve))
            return original_query(read, sql, params, records=records,
                octets=octets, cells=cells, identities=identities)

        @contextmanager
        def bind(owner, *args, **kwargs):
            try:
                with original_bind(owner, *args, **kwargs) as result:
                    yield result
            finally:
                for witness in witnesses:
                    if witness["owner"] is owner:
                        witness["settled"] = witness["accounting"].used

        with mock.patch.object(preparation, "_ordinary_tail_budget", budget), \
                mock.patch.object(_EvidenceRead, "query", query), \
                mock.patch.object(preparation._OrdinaryStartReadBoundsOwner, "bind", bind):
            try:
                yield witnesses
            finally:
                self.assertTrue(witnesses, "retained update did not enter owner preparation")
                for witness in witnesses:
                    self.assertTrue(witness["peaks"])
                    self.assertIsNotNone(witness["settled"])
                    for name in ("records", "value_octets", "scalar_markers", "statements"):
                        prior = getattr(witness["before"], name)
                        for peak in witness["peaks"]:
                            self.assertLessEqual(getattr(peak, name) - prior,
                                getattr(witness["budget"].peak, name),
                                f"retained suffix prospective {name} exceeds forecast")
                        self.assertLessEqual(getattr(witness["settled"], name) - prior,
                            getattr(witness["budget"].settled, name),
                            f"retained suffix settled {name} exceeds forecast")

    async def test_four_receivers_complete_with_conservative_suffix(self):
        # Keep the original public positive law unchanged, enabling measurement
        # only after its real initial acceptance and update preparation.
        prepare = self.prepare_content
        observers = []

        async def prepared():
            result = await prepare()
            observer = self.conservative_suffix()
            observer.__enter__()
            observers.append(observer)
            return result

        try:
            with mock.patch.object(self, "prepare_content", prepared):
                await original.ManagedConfigurationUpdateExecutionTests.test_replacement_preserves_wrapper_origin_and_history_then_advances(self)
        finally:
            for observer in reversed(observers):
                observer.__exit__(None, None, None)


class RetainedReceiverFreshnessTests(chain.ManagedApplicationFixture):
    application_artifacts = (APPLICATION,)
    complete_initial = original.ManagedConfigurationUpdateExecutionTests.complete_initial
    prepare_content = original.ManagedConfigurationUpdateExecutionTests.prepare_content
    execute_content = original.ManagedConfigurationUpdateExecutionTests.execute_content

    async def late_refusal(self, change, snapshot):
        await self.prepare_content()
        original = preparation.ConfigurationPreparationStore._prepare
        entered = []
        before = snapshot()
        runtime_before = tuple(self.runtime.calls)
        protected = {name: self.rows(name) for name in
            ("cpk_effect_configuration_refs", "cpk_configuration_claims")}

        def mutate(store, *args, **kwargs):
            result = original(store, *args, **kwargs)
            entered.append(result)
            self.assertEqual(change(store._connection).rowcount, 1)
            return result

        with mock.patch.object(preparation.ConfigurationPreparationStore, "_prepare", mutate), \
                self.assertRaises(ExecutionCoordinatorConflict):
            await self.execute_content()
        self.assertEqual(len(entered), 1)
        self.assertEqual(snapshot(), before)
        self.assertEqual(tuple(self.runtime.calls), runtime_before)
        for name, rows in protected.items():
            self.assertEqual(self.rows(name), rows)
        target, = (activity for activity in self.record.plan.activities
            if type(activity.operation) is ReconcileNode)
        for name in ("cpk_effect_attempts", "cpk_effect_attempt_intents", "cpk_effect_attempt_outcomes"):
            self.assertEqual(self.connection.execute(
                "SELECT count(*) FROM " + name + " WHERE run_id=%s AND activity_id=%s",
                (self.update_run, target.activity_id.value)).fetchone()[0], 0)

    async def test_retained_update_rechecks_approval_after_preparation(self):
        await self.late_refusal(lambda connection: connection.execute(
            "UPDATE cpk_approval_decisions SET decision='rejected' WHERE request_id="
            "(SELECT approval_request_id FROM cpk_execution_requests WHERE request_id="
            "(SELECT request_id FROM cpk_activity_runs WHERE run_id=%s))", (self.update_run,)),
            lambda: self.rows("cpk_approval_decisions"))

    async def test_retained_update_rechecks_complete_current_bindings_after_preparation(self):
        await self.late_refusal(lambda connection: connection.execute(
            "DELETE FROM cpk_graph_receiver_bindings WHERE graph_id=%s AND node_id='api'",
            (self.before_update.current_graph_id,)), lambda: self.rows("cpk_graph_receiver_bindings"))
