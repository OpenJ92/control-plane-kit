"""N4 original-source integrity, distinct from N6 current permission refusal."""

from dataclasses import replace
from unittest import mock

from psycopg.types.json import Jsonb

from control_plane_kit_core.receiver_identity import NodeControlAuthorityContext, receiver_node_control_audience
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
from control_plane_kit_core.wrapper_configuration import WrapperConfigurationError
from control_plane_kit_operations.health_effect_preparations import HealthEffectPreparationCodec, HealthEffectPreparationCorrupt
from tests.receiver_health_execution_fixture import ReceiverHealthExecutionFixture


class ReceiverHealthOriginalEvidenceTests(ReceiverHealthExecutionFixture):
    async def test_self_consistent_receiver_or_context_substitution_cannot_relabel_history(self):
        await self.prepare_health()
        preparation = self.start_service().execute_health(self.start_command).preparation
        codec = HealthEffectPreparationCodec()
        original = codec.encode_canonical_bytes(preparation)
        identity = preparation.identity
        keys = (identity.run_id.value, identity.activity_id, identity.attempt)
        requests = (
            replace(preparation.request, target=replace(preparation.request.target, receiver_id="c" * 32)),
            replace(preparation.request, authority_context=NodeControlAuthorityContext(
                self.workspace["current_graph_id"], self.workspace["current_realized_projection_id"])),
        )
        try:
            for request in requests:
                common = dict(target=request.target, authority_context=request.authority_context,
                    request_id=request.request_id, request_digest=request.canonical_digest())
                forged = replace(preparation, request=request,
                    transit_grant=replace(preparation.transit_grant, **common),
                    workload_grant=replace(preparation.workload_grant,
                        audience=receiver_node_control_audience(request.target), **common))
                # This is deliberately corrupted retained history, not an
                # admitted request. Nested V2 values remain internally valid;
                # original graph/binding owners must reject their association.
                self.connection.execute("UPDATE cpk_health_effect_preparations SET preimage=%s "
                    "WHERE run_id=%s AND activity_id=%s AND attempt=%s",
                    (codec.encode_canonical_bytes(forged), *keys))
                before = self.durable_snapshot(), self.receiver_snapshot()
                with self.assertRaises(HealthEffectPreparationCorrupt):
                    with self.unit_of_work() as uow:
                        uow.stores.health_effect_preparations.get(identity)
                self.assertEqual((self.durable_snapshot(), self.receiver_snapshot()), before)
        finally:
            self.connection.execute("UPDATE cpk_health_effect_preparations SET preimage=%s "
                "WHERE run_id=%s AND activity_id=%s AND attempt=%s", (original, *keys))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.health_effect_preparations.get(identity), preparation)

    async def test_original_binding_digest_must_match_the_original_common_artifact(self):
        await self.prepare_health()
        preparation = self.start_service().execute_health(self.start_command).preparation
        graph_id, projection_id = self.selected_context
        where = "workspace_id='workspace-a' AND graph_id=%s AND realized_projection_id=%s AND receiver_id=%s"
        parameters = (graph_id, projection_id, "a" * 32)
        original = self.connection.execute("SELECT selected_configuration_digest FROM cpk_graph_receiver_bindings WHERE "
            + where, parameters).fetchone()[0]
        try:
            self.connection.execute("UPDATE cpk_graph_receiver_bindings SET selected_configuration_digest=%s WHERE "
                + where, ("0" * 64, *parameters))
            before = self.durable_snapshot(), self.receiver_snapshot()
            with self.assertRaises(HealthEffectPreparationCorrupt):
                with self.unit_of_work() as uow:
                    uow.stores.health_effect_preparations.get(preparation.identity)
            self.assertEqual((self.durable_snapshot(), self.receiver_snapshot()), before)
        finally:
            self.connection.execute("UPDATE cpk_graph_receiver_bindings SET selected_configuration_digest=%s WHERE "
                + where, (original, *parameters))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.health_effect_preparations.get(preparation.identity), preparation)

    async def test_self_consistent_foreign_gateway_receiver_cannot_relabel_original_transit(self):
        await self.prepare_health()
        preparation = self.start_service().execute_health(self.start_command).preparation
        codec = HealthEffectPreparationCodec()
        original = codec.encode_canonical_bytes(preparation)
        identity = preparation.identity
        parameters = (identity.run_id.value, identity.activity_id, identity.attempt)
        changed = replace(preparation, transit_grant=replace(preparation.transit_grant,
            gateway_target=replace(preparation.transit_grant.gateway_target, receiver_id="c" * 32)))
        try:
            self.connection.execute("UPDATE cpk_health_effect_preparations SET preimage=%s "
                "WHERE run_id=%s AND activity_id=%s AND attempt=%s", (codec.encode_canonical_bytes(changed), *parameters))
            before = self.durable_snapshot(), self.receiver_snapshot()
            with self.assertRaises(HealthEffectPreparationCorrupt):
                with self.unit_of_work() as uow:
                    uow.stores.health_effect_preparations.get(identity)
            self.assertEqual((self.durable_snapshot(), self.receiver_snapshot()), before)
        finally:
            self.connection.execute("UPDATE cpk_health_effect_preparations SET preimage=%s "
                "WHERE run_id=%s AND activity_id=%s AND attempt=%s", (original, *parameters))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.health_effect_preparations.get(identity), preparation)

    async def test_malformed_original_common_artifact_cannot_be_read_as_valid_v2_history(self):
        await self.prepare_health()
        preparation = self.start_service().execute_health(self.start_command).preparation
        projection_id = self.selected_context[1]
        original = self.connection.execute("SELECT graph_descriptor FROM cpk_realized_graph_projections "
            "WHERE projection_id=%s", (projection_id,)).fetchone()[0]
        graph = DEFAULT_GRAPH_CODEC.decode(original)
        node = graph.node("api")
        artifact, = node.configuration_artifacts
        malformed = replace(node, configuration_artifacts=(replace(artifact,
            content='{"profile":"workload-node-control-configuration.v2","target":{}}'),))
        descriptor = DEFAULT_GRAPH_CODEC.encode(replace(graph, nodes={**graph.nodes, "api": malformed}))
        try:
            # Explicit corruption of immutable ORIGINAL evidence. Its existing
            # digest/source witnesses stay original; no repair/rebaseline is
            # performed to make corrupted configuration appear authored.
            self.connection.execute("UPDATE cpk_realized_graph_projections SET graph_descriptor=%s "
                "WHERE projection_id=%s", (Jsonb(descriptor), projection_id))
            before = self.durable_snapshot(), self.receiver_snapshot()
            with self.assertRaises(HealthEffectPreparationCorrupt):
                with self.unit_of_work() as uow:
                    uow.stores.health_effect_preparations.get(preparation.identity)
            self.assertEqual((self.durable_snapshot(), self.receiver_snapshot()), before)
        finally:
            self.connection.execute("UPDATE cpk_realized_graph_projections SET graph_descriptor=%s "
                "WHERE projection_id=%s", (Jsonb(original), projection_id))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.health_effect_preparations.get(preparation.identity), preparation)

    async def test_history_consumes_original_common_codec_and_preserves_its_contract_refusal(self):
        await self.prepare_health()
        preparation = self.start_service().execute_health(self.start_command).preparation
        encoded = HealthEffectPreparationCodec().encode_canonical_bytes(preparation)
        expected = self.graph.node("api").configuration_artifacts[0].content.encode()
        original = ReceiverNodeControlConfigurationCodec.decode_bytes
        seen = []
        def passthrough(codec, raw):
            if raw == expected:
                seen.append(raw)
            return original(codec, raw)
        before = self.durable_snapshot(), self.receiver_snapshot()
        with mock.patch.object(ReceiverNodeControlConfigurationCodec, "decode_bytes", passthrough):
            with self.unit_of_work() as uow:
                restored = uow.stores.health_effect_preparations.get(preparation.identity)
        self.assertGreater(len(seen), 0, "V2 history must structurally decode original common material")
        self.assertEqual(restored, preparation)
        self.assertEqual(HealthEffectPreparationCodec().encode_canonical_bytes(restored), encoded)
        refused = []
        def contract_refusal(codec, raw):
            if raw == expected:
                refused.append(raw)
                raise WrapperConfigurationError("wrapper configuration is invalid")
            return original(codec, raw)
        # A documented decoder-contract refusal on the same coherent source,
        # not a claim that malformed bytes reached this exact layer. The
        # separate literal corruption test retains all original witnesses.
        with mock.patch.object(ReceiverNodeControlConfigurationCodec, "decode_bytes", contract_refusal):
            with self.assertRaises(HealthEffectPreparationCorrupt):
                with self.unit_of_work() as uow:
                    uow.stores.health_effect_preparations.get(preparation.identity)
        self.assertGreater(len(refused), 0)
        self.assertEqual((self.durable_snapshot(), self.receiver_snapshot()), before)
