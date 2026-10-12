"""Retained receiver permission stays fresh within a conservative read bound."""
from dataclasses import replace
from unittest import mock

from control_plane_kit_core.topology.graph import Endpoint, LiteralAddress
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_core.planning import ReconcileNode
from control_plane_kit_operations.coordinator import ExecutionCoordinatorConflict
from control_plane_kit_operations import effect_attempt_start_interpreter as start
from control_plane_kit_operations import receiver_lifecycle
from control_plane_kit_operations.postgres import configuration_preparation_store as preparation
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from tests.retained_receiver_read_bounds_fixture import conservative_suffix
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

    conservative_suffix = conservative_suffix

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

    async def late_refusal(self, change, snapshot, *, after_insert=False):
        await self.prepare_content()
        method = "_insert_original" if after_insert else "_prepare"
        original = getattr(preparation.ConfigurationPreparationStore, method)
        permission = start._require_fresh_effect_receiver_permission
        entered = []
        refused_final_permission = []
        before = snapshot()
        runtime_before = tuple(self.runtime.calls)
        protected = {name: self.rows(name) for name in
            ("cpk_effect_configuration_refs", "cpk_configuration_claims")}

        def mutate(store, *args, **kwargs):
            result = original(store, *args, **kwargs)
            entered.append(result)
            self.assertEqual(change(store._connection).rowcount, 1)
            return result

        def fresh(*args, **kwargs):
            try:
                return permission(*args, **kwargs)
            except Exception:
                if entered and after_insert:
                    refused_final_permission.append(True)
                raise

        with mock.patch.object(preparation.ConfigurationPreparationStore, method, mutate), \
                mock.patch.object(start, "_require_fresh_effect_receiver_permission", fresh), \
                self.assertRaises(ExecutionCoordinatorConflict):
            await self.execute_content()
        self.assertEqual(len(entered), 1)
        if after_insert:
            self.assertEqual(refused_final_permission, [True])
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

    async def test_final_permission_rechecks_current_binding_after_start_insert(self):
        await self.late_refusal(lambda connection: connection.execute(
            "DELETE FROM cpk_graph_receiver_bindings WHERE graph_id=%s AND node_id='api'",
            (self.before_update.current_graph_id,)), lambda: self.rows("cpk_graph_receiver_bindings"),
            after_insert=True)

    async def assert_final_permission_tuple_refusal(self, side, fault):
        await self.prepare_content()
        target, = (activity for activity in self.record.plan.activities
            if type(activity.operation) is ReconcileNode)
        protected = {name: self.rows(name) for name in
            ("cpk_effect_configuration_refs", "cpk_configuration_claims", "cpk_graph_receiver_bindings")}
        runtime_before = tuple(self.runtime.calls)
        material = receiver_lifecycle._retained_receiver_material
        execution_material = PostgresExecutionStore._receiver_execution_material
        validate_receiver = receiver_lifecycle._validate_receiver_execution
        insert = preparation.ConfigurationPreparationStore._insert_original
        permission = start._require_fresh_effect_receiver_permission
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
        pairs = ((workspace.current_graph_id, workspace.current_realized_projection_id),
            (workspace.desired_graph_id, workspace.desired_realized_projection_id))
        self.assertNotEqual(pairs[0], pairs[1])
        inserted, changed, refused, removed, receiver_refused = [], [], [], [], []
        false_empty = fault in ("false-empty-originals", "false-empty-missing-binding")

        def inserted_original(*args, **kwargs):
            result = insert(*args, **kwargs)
            inserted.append(True)
            return result

        def altered_material(stores, workspace_id, graph_id, projection_id):
            result = material(stores, workspace_id, graph_id, projection_id)
            if inserted and not false_empty and (graph_id, projection_id) == pairs[side]:
                self.assertGreaterEqual(len(result), 2)
                changed.append(True)
                if fault == "order":
                    return tuple(reversed(result))
                if fault == "duplicate":
                    return result + result[:1]
                other = pairs[1 - side]
                return tuple(replace(value, graph_id=other[0], realized_projection_id=other[1])
                    for value in result)
            return result

        def altered_originals(store, *args, **kwargs):
            original, derived = execution_material(store, *args, **kwargs)
            if inserted and false_empty:
                # Verify actual original truth first, then fault only the value
                # consumed by permission. Raw membership must still be read.
                if fault == "false-empty-missing-binding" and not removed:
                    cursor = store._connection.execute(
                        "DELETE FROM cpk_graph_receiver_bindings WHERE graph_id=%s AND node_id='api'",
                        (pairs[0][0],))
                    self.assertEqual(cursor.rowcount, 1)
                    removed.append(True)
                original = (original[0], *(type(value).from_graph(
                    projection_id=value.projection_id, workspace_id=value.workspace_id,
                    source_authored_graph_id=value.source_authored_graph_id,
                    projection_kind=value.projection_kind, projection_key=value.projection_key,
                    graph=DeploymentGraph("false-empty"), created_by=value.created_by,
                    created_at=value.created_at)
                    for value in original[1:]))
                changed.append(True)
            return original, derived

        def validated_receiver(*args, **kwargs):
            try:
                return validate_receiver(*args, **kwargs)
            except Exception:
                if false_empty and changed:
                    receiver_refused.append(True)
                raise

        def fresh(*args, **kwargs):
            try:
                return permission(*args, **kwargs)
            except Exception:
                if inserted:
                    refused.append(True)
                raise

        with mock.patch.object(preparation.ConfigurationPreparationStore, "_insert_original", inserted_original), \
                mock.patch.object(receiver_lifecycle, "_retained_receiver_material", altered_material), \
                mock.patch.object(PostgresExecutionStore, "_receiver_execution_material", altered_originals), \
                mock.patch.object(receiver_lifecycle, "_validate_receiver_execution", validated_receiver), \
                mock.patch.object(start, "_require_fresh_effect_receiver_permission", fresh), \
                self.assertRaises(ExecutionCoordinatorConflict):
            await self.execute_content()
        self.assertEqual(inserted, [True])
        self.assertTrue(changed)
        self.assertEqual(removed, [True] if fault == "false-empty-missing-binding" else [])
        self.assertEqual(receiver_refused, [True] if false_empty else [])
        self.assertEqual(refused, [True])
        self.assertEqual(tuple(self.runtime.calls), runtime_before)
        for name, rows in protected.items():
            self.assertEqual(self.rows(name), rows)
        for name in ("cpk_effect_attempts", "cpk_effect_attempt_intents", "cpk_effect_attempt_outcomes"):
            self.assertEqual(self.connection.execute(
                "SELECT count(*) FROM " + name + " WHERE run_id=%s AND activity_id=%s",
                (self.update_run, target.activity_id.value)).fetchone()[0], 0)

    async def test_final_permission_rejects_current_order(self):
        await self.assert_final_permission_tuple_refusal(0, "order")

    async def test_final_permission_rejects_desired_order(self):
        await self.assert_final_permission_tuple_refusal(1, "order")

    async def test_final_permission_rejects_current_duplicate(self):
        await self.assert_final_permission_tuple_refusal(0, "duplicate")

    async def test_final_permission_rejects_desired_duplicate(self):
        await self.assert_final_permission_tuple_refusal(1, "duplicate")

    async def test_final_permission_rejects_current_graph_side(self):
        await self.assert_final_permission_tuple_refusal(0, "graph-side")

    async def test_final_permission_rejects_desired_graph_side(self):
        await self.assert_final_permission_tuple_refusal(1, "graph-side")

    async def test_final_permission_rejects_false_empty_originals(self):
        await self.assert_final_permission_tuple_refusal(0, "false-empty-originals")

    async def test_final_permission_rejects_false_empty_originals_with_missing_binding(self):
        await self.assert_final_permission_tuple_refusal(0, "false-empty-missing-binding")
