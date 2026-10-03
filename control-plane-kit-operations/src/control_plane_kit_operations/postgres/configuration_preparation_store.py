"""Immutable original refs and protective claims in the start owner's UoW."""
from __future__ import annotations

from hashlib import sha256
from contextlib import contextmanager

from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_operations.configuration_preparation import (
    ConfigurationAllocationEvidence, ConfigurationRefEvidence, _ref,
)
from control_plane_kit_operations.records import OperationsRecordError
from .configuration_evidence import _Capacity, _EvidenceRead, _Unavailable
from .configuration_source import read_source


_NAMES = ("run_id", "activity_id", "attempt", "artifact_id", "workspace_id", "allocation_id",
    "runtime_id", "node_id", "ref_preimage", "ref_digest", "request_fingerprint", "original_event_id",
    "birth_run_id", "birth_activity_id", "birth_attempt", "birth_artifact_id", "is_birth")
_TEXT = tuple(name for name in _NAMES if name not in ("attempt", "birth_attempt", "is_birth", "ref_preimage"))
_VALID = " AND ".join(f"octet_length(r.{name}) BETWEEN 1 AND 2048" for name in _TEXT)
_VALID += " AND octet_length(r.ref_preimage) BETWEEN 1 AND 4096"
_VALID += " AND octet_length(c.workspace_id) BETWEEN 1 AND 128 AND octet_length(c.allocation_id) BETWEEN 1 AND 128"
_SELECT = "SELECT " + ",".join(f"CASE WHEN {_VALID} THEN r.{name} END" for name in _NAMES)
_SELECT += f", CASE WHEN {_VALID} THEN c.workspace_id END, CASE WHEN {_VALID} THEN c.allocation_id END"
_SELECT += " FROM cpk_effect_configuration_refs r LEFT JOIN cpk_configuration_claims c ON "
_SELECT += "(c.run_id,c.activity_id,c.attempt,c.artifact_id)=(r.run_id,r.activity_id,r.attempt,r.artifact_id)"


def _key(row):
    return EffectAttemptIdentity(RunId(row[0]), row[1], row[2]), row[3]


def _decode_ref(row):
    """Exact manifest material/claim, independent of requested source history."""
    if len(row) != 19 or any(value is None for value in row):
        raise _Unavailable
    identity, artifact = _key(row)
    ref = ConfigurationInstanceRefCodec().decode_canonical_bytes(row[8])
    birth = EffectAttemptIdentity(RunId(row[12]), row[13], row[14])
    if ((ref.artifact_id, ref.workspace_id, ref.allocation_id, ref.runtime_id, ref.node_id)
            != (artifact, row[4], row[5], row[6], row[7])
            or sha256(row[8]).hexdigest() != row[9]
            or (row[17], row[18]) != (ref.workspace_id, ref.allocation_id)
            or type(row[16]) is not bool
            or row[16] != ((identity, artifact) == (birth, row[15]))):
        raise _Unavailable
    return identity, ref, birth, row[15]


def _decode(row, read):
    identity, ref, birth, birth_artifact = _decode_ref(row)
    source = read_source(read.connection, identity, ref, read=read)
    if source.state == "capacity":
        raise _Capacity
    if (source.state != "complete" or source.source.request_fingerprint != row[10]
            or source.source.original_event_id != row[11]):
        raise _Unavailable
    return ConfigurationRefEvidence(identity, ref, birth, birth_artifact, source.source)


class ConfigurationPreparationStore:
    def __init__(self, connection):
        self._connection = connection
        self._issued = None

    def _require_issued(self, prepared):
        if (self._issued is not prepared or prepared.stores.configuration_preparation is not self
                or prepared.stores.connection is not self._connection):
            raise OperationsRecordError("configuration start requires owner preparation")

    @contextmanager
    def _advancement_evidence(self, workspace_id, run_id):
        """One advancement ledger, including locators and original replay."""
        from control_plane_kit_operations._configuration_preparation import _configuration_accounting
        from control_plane_kit_operations.advancement import CurrentGraphAdvancementConflict
        try:
            with _configuration_accounting(("configuration-advancement", workspace_id, run_id), join=True):
                yield
        except (_Capacity, _Unavailable):
            pass
        else:
            return
        raise CurrentGraphAdvancementConflict("configuration advancement evidence is unavailable") from None

    def _configure_run(self, run_id):
        """Bounded catalog-free routing from the plan's exact pinned graphs."""
        from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
        read = _EvidenceRead(self._connection)
        rows = read.query("""
            WITH pinned AS (
              SELECT CASE WHEN p.base_realized_projection_id IS NULL THEN b.graph_descriptor
                          ELSE bp.graph_descriptor END AS base,
                     CASE WHEN p.desired_realized_projection_id IS NULL THEN d.graph_descriptor
                          ELSE dp.graph_descriptor END AS desired
              FROM cpk_activity_runs r JOIN cpk_activity_plans p ON p.plan_id=r.plan_id
              LEFT JOIN cpk_graph_versions b ON b.graph_id=p.base_graph_id
              LEFT JOIN cpk_graph_versions d ON d.graph_id=p.desired_graph_id
              LEFT JOIN cpk_realized_graph_projections bp ON bp.projection_id=p.base_realized_projection_id
              LEFT JOIN cpk_realized_graph_projections dp ON dp.projection_id=p.desired_realized_projection_id
              WHERE r.run_id=%s
            )
            SELECT jsonb_path_exists(base, '$.nodes.*.configuration_artifacts[*]')
                OR jsonb_path_exists(desired, '$.nodes.*.configuration_artifacts[*]'),
                base IS NOT NULL AND desired IS NOT NULL
            FROM pinned LIMIT 1
            """, (run_id,), records=1, octets=2, cells=2, identities=6)
        if rows and rows[0][1] is not True:
            raise _Unavailable
        _ACCOUNTING.get().active = bool(rows and rows[0][0])

    def _material(self, stores, request, run, activity, read, *, guard=None):
        from .receiver_execution_scopes import _ExecutionScopeStorage
        from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
        from control_plane_kit_operations.graph_authoring import product_references_in_graph
        from control_plane_kit_operations.runtime_effects import (
            _RuntimeEffectMaterial, _connector_ingress_for_node, _has_tunnel_token_delivery,
            _material_graph, _node_target, _registered_product_for_node,
        )
        storage = _ExecutionScopeStorage(self._connection, read)
        if guard is not None:
            storage.guard(request.identity.workspace_id, guard)
        (plan, base, desired), _ = storage.verify(request.identity)
        if activity is not None and plan.plan.activity(activity.activity_id) != activity:
            raise _Unavailable
        graphs = tuple(DEFAULT_GRAPH_CODEC.decode(value.graph_descriptor) for value in (base, desired))
        references = sorted(set(reference for graph in graphs for reference in product_references_in_graph(graph)),
            key=lambda reference: reference.descriptor_sha256.value)
        workspace = request.identity.workspace_id
        products = tuple(stores.registered_products._configuration_product(workspace, reference, read)
            for reference in references)
        runtime_refs = sorted(set(runtime.authority_ref for graph in graphs for runtime in graph.runtimes.values()
            if runtime.authority_ref is not None), key=lambda ref: ref.reference_id)
        runtimes = tuple(stores.runtime_authorities._configuration_authority(workspace, ref, read) for ref in runtime_refs)
        by_reference = {authority.authority_ref: authority for authority in runtimes}
        if any(runtime.authority_ref is not None
                and by_reference[runtime.authority_ref].runtime_kind is not runtime.kind
                for graph in graphs for runtime in graph.runtimes.values()):
            raise _Unavailable
        material = _RuntimeEffectMaterial(request, run, plan, base, desired, products, runtime_authorities=runtimes)
        if activity is None:
            return material
        graph = _material_graph(material, activity.operation)
        node_id = _node_target(activity.operation)
        if node_id is None:
            return material
        node = graph.nodes[node_id]
        product = _registered_product_for_node(products, node.metadata)
        pulls = stores.image_pull_authorities._configuration_image_authorities(
            workspace, product.descriptor_document.product.image, read)
        deliveries = tuple(stores.runtime_authority_deliveries._configuration_delivery(workspace, ref, read)
            for ref in sorted(set(delivery.authority_ref for delivery in node.runtime_authority_deliveries),
                key=lambda ref: ref.reference_id))
        ingress = _connector_ingress_for_node(graph, node_id)
        authorities, resources, secrets = (), (), ()
        if ingress is not None and not _has_tunnel_token_delivery(tuple(node.secret_deliveries)):
            resource = stores.ingress_resources._configuration_resource(workspace, ingress.ingress_id, read)
            resources = (resource,)
            authorities = (stores.ingress_authorities._configuration_authority(workspace, resource.authority_ref, read),)
            secrets = (stores.generated_ingress_secrets._configuration_source(resource, read),)
        from dataclasses import replace
        return replace(material, image_pull_authorities=pulls, runtime_authority_deliveries=deliveries,
            ingress_authorities=authorities, ingress_resources=resources, generated_ingress_secrets=secrets)

    def _snapshot(self, request, run):
        from .receiver_execution_scopes import _ExecutionScopeStorage
        read = _EvidenceRead(self._connection)
        storage = _ExecutionScopeStorage(self._connection, read)
        original, _ = storage.verify(request.identity)
        return original, storage.events(run.run_id), read

    def _proposal(self, stores, identity, material, activity, read):
        """Immutable optimistic values; the start owner repeats this under L."""
        from control_plane_kit_core.planning import StartNode, ReconcileNode
        from control_plane_kit_core.runtime_effects import RuntimeEffectKind
        from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
        from control_plane_kit_operations._configuration_preparation import _propose_configuration
        from control_plane_kit_operations.runtime_effects import _runtime_effect_intent_for_material
        if type(activity.operation) not in (StartNode, ReconcileNode):
            return None, ()
        intent = _runtime_effect_intent_for_material(material, activity)
        proposed = _propose_configuration(identity, intent)
        if proposed.kind is not RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1:
            return proposed, ()
        node_id = intent.operation.target.node_id
        base = DEFAULT_GRAPH_CODEC.decode(material.base_graph.graph_descriptor)
        if node_id not in base.nodes and not self._node_history(proposed.configuration_instances.instances[0], read):
            # The inherited never-used-slot path remains a separate B2
            # current/E7 authority obligation, with its fixture conversion.
            return proposed, ()
        acceptance = stores.configuration_acceptance
        workspace = stores.workspaces.get(intent.source.workspace_id)
        receipt = acceptance._current_manifest(workspace, read)
        if (receipt[0]["graph_id"], receipt[0]["projection_id"]) != (
                material.base_graph.source_authored_graph_id, material.base_graph.projection_id):
            raise _Unavailable
        rows = tuple(row for row in receipt[3] if row[1] == node_id)
        if node_id not in base.nodes:
            # Historical recreation requires a real accepted departure, not
            # desired-only omission or a missing membership row.
            if receipt[0]["pinned_revision"] is None or rows:
                raise _Unavailable
            return proposed, ()
        if not rows or len(rows) > 32:
            raise _Unavailable
        observed = acceptance._observed_bindings(receipt, rows, read)
        refs = tuple(binding.ref for binding in observed.bindings)
        return _propose_configuration(identity, intent, refs), observed.bindings

    def _node_history(self, ref, read):
        """Complete bounded same-runtime node candidates, before any joins."""
        columns = tuple((name, "int" if name == "attempt" else "text", 2048)
            for name in ("run_id", "activity_id", "attempt", "artifact_id", "allocation_id"))
        return read.bounded_rows("cpk_effect_configuration_refs", columns,
            "workspace_id=%s AND runtime_id=%s AND node_id=%s",
            (ref.workspace_id, ref.runtime_id, ref.node_id),
            maximum=256, point=False, order="artifact_id,run_id,activity_id,attempt")

    def _historical_protection(self, stores, refs, read):
        """Every historical allocation retains its own material and protection."""
        allocations = []
        acceptance = stores.configuration_acceptance
        candidates = self._node_history(refs[0], read)
        if len(candidates) + len(refs) > 256:
            raise _Capacity
        groups = {}
        for candidate in candidates:
            groups.setdefault(candidate[4], []).append(candidate)
        for allocation_id, rows in groups.items():
            original = acceptance._ref(read, rows[0][:4])
            _, ref, _, _ = _decode_ref(original)
            if (ref.allocation_id != allocation_id
                    or (ref.workspace_id, ref.runtime_id, ref.node_id) != (
                        refs[0].workspace_id, refs[0].runtime_id, refs[0].node_id)):
                raise _Unavailable
            allocation = self._allocation_evidence(ref, read)
            if allocation.state == "capacity":
                raise _Capacity
            if allocation.state != "complete":
                raise _Unavailable
            keys = tuple((claim.identity.run_id.value, claim.identity.activity_id,
                claim.identity.attempt, claim.ref.artifact_id) for claim in allocation.claims)
            if keys != tuple(row[:4] for row in rows):
                raise _Unavailable
            allocations.append(allocation)
            if sum(len(value.claims) for value in allocations) + len(refs) > 256:
                raise _Capacity
            for claim in allocation.claims:
                birth = claim.birth_identity
                key = (claim.identity, ref.artifact_id)
                row = (ref.runtime_id, ref.node_id, ref.artifact_id,
                    claim.identity.run_id.value, claim.identity.activity_id, claim.identity.attempt, ref.artifact_id,
                    birth.run_id.value, birth.activity_id, birth.attempt, claim.birth_artifact_id, read.refs[key][9])
                # Every neighbor must have its own original accepted successful
                # use. Unaccepted/uncertain work cannot become reuse permission.
                context = acceptance._original_use(read, row, claim.source)
                acceptance._prove_use(read, row, *context)
        return tuple(allocations)

    def _prepare(self, stores, command, request, run, plan, guard, event_kind):
        from control_plane_kit_core.operations import ActivityEventKind
        from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
        from control_plane_kit_operations._configuration_preparation import _PreparedConfigurationStart
        from control_plane_kit_operations.configuration_preparation import (
            ConfigurationEvidenceFootprint, ConfigurationCapacityDecision, configuration_preparation_capacity,
        )
        from control_plane_kit_operations.effect_attempt_intent_evidence import _encode_runtime_effect_intent
        if event_kind is not ActivityEventKind.STEP_STARTED:
            raise _Unavailable
        read = _EvidenceRead(self._connection)
        activity = plan.plan.activity(command.intent.activity_id)
        material = self._material(stores, request, run, activity, read, guard=guard)
        if material.plan_record != plan:
            raise _Unavailable
        expected, bindings = self._proposal(stores, command.transition.identity, material, activity, read)
        if expected != command.intent or runtime_effect_intent_fingerprint(expected) != command.transition.request_fingerprint:
            raise _Unavailable
        from .configuration_source import _preflight_source
        _preflight_source(read, _encode_runtime_effect_intent(expected), command.transition, expected.source, command.fence)
        refs = expected.configuration_instances.instances
        allocations = self._historical_protection(stores, refs, read)
        if any(claim.identity == command.transition.identity for value in allocations for claim in value.claims):
            # Exact originals use the earlier replay entrance. Fresh ownership
            # cannot recreate an already retained claim under the same key.
            raise _Unavailable
        by_allocation = {value.birth.ref.allocation_id: value for value in allocations}
        selected = tuple(by_allocation.get(ref.allocation_id) for ref in refs)
        if bindings:
            if (any(value is None for value in selected)
                    or tuple(value.birth for value in selected) != tuple(binding.birth for binding in bindings)):
                raise _Unavailable
        elif any(value is not None for value in selected):
            # A fresh birth must never silently adopt a historical allocation.
            raise _Unavailable
        total_claims = sum(len(value.claims) for value in allocations)
        # Fixed future envelope includes complete source context, both original
        # and direct 16KiB events, the full 8192-byte outcome, and its source links.
        count = len(refs)
        records, markers, statements = 24 * count + 264, 384 * count, 24 * count
        envelope = 192 * 1024 * count + 3 * 1024 * 1024 + 512 * 1024
        future = ConfigurationEvidenceFootprint(records,
            envelope - 128 * records - 16 * markers - 256 * statements, markers, statements)
        for index, ref in enumerate(refs):
            claim_keys = ()
            if selected[index] is not None:
                claim_keys = tuple((claim.identity, claim.ref.artifact_id) for claim in selected[index].claims)
            decision = configuration_preparation_capacity(current=read.used, reserved_future=future,
                existing_claim_keys=claim_keys, proposed_claim_key=(command.transition.identity, ref.artifact_id),
                existing_total_claims=total_claims + count - 1)
            if decision is not ConfigurationCapacityDecision.WITHIN_LIMITS:
                raise _Capacity
        births = (tuple((value.birth.identity, value.birth.ref.artifact_id) for value in selected)
            if bindings else tuple((command.transition.identity, ref.artifact_id) for ref in refs))
        prepared = _PreparedConfigurationStart(stores, guard, command.transition.identity, expected, births)
        self._issued = prepared
        return prepared

    def read_allocation_evidence(self, exact_ref):
        """Complete historical protection only; never permission to reuse/release."""
        _ref(exact_ref)
        return self._allocation_evidence(exact_ref, _EvidenceRead(self._connection, standalone=True))

    def _allocation_evidence(self, exact_ref, read):
        try:
            rows = read.query(_SELECT + " WHERE r.workspace_id=%s AND r.allocation_id=%s"
                " ORDER BY r.run_id,r.activity_id,r.attempt,r.artifact_id LIMIT 65",
                (exact_ref.workspace_id, exact_ref.allocation_id), records=65,
                octets=65 * 32768, cells=19, identities=2)
            if len(rows) > 64:
                raise _Capacity
            if not rows:
                raise _Unavailable
            claimed = read.query("SELECT "
                "CASE WHEN octet_length(run_id)<=200 THEN run_id END,"
                "CASE WHEN octet_length(activity_id)<=200 THEN activity_id END,attempt,"
                "CASE WHEN octet_length(artifact_id)<=63 THEN artifact_id END "
                "FROM cpk_configuration_claims WHERE workspace_id=%s AND allocation_id=%s "
                "ORDER BY run_id,activity_id,attempt,artifact_id LIMIT 65",
                (exact_ref.workspace_id, exact_ref.allocation_id), records=65,
                octets=65 * 480, cells=4)
            if len(claimed) > 64:
                raise _Capacity
            if tuple(tuple(row[:4]) for row in rows) != tuple(claimed):
                raise _Unavailable
            # Discover from refs, not claims: a missing reciprocal claim cannot
            # disappear from protection even inside a deferred transaction.
            for row in rows:
                read.refs[_key(row)] = row
            claims = tuple(_decode(row, read) for row in rows)
            if any(claim.ref != exact_ref for claim in claims):
                raise _Unavailable
            births = {(claim.birth_identity, claim.birth_artifact_id) for claim in claims}
            if len(births) != 1:
                raise _Unavailable
            birth_key = next(iter(births))
            # A direct root must itself occur in the complete allocation set.
            # There is no chain traversal, even when malformed foreign keys exist.
            root = read.refs.get(birth_key)
            if root is None or root[16] is not True or _key(root) != (EffectAttemptIdentity(
                    RunId(root[12]), root[13], root[14]), root[15]):
                raise _Unavailable
            birth = next(claim for claim in claims if (claim.identity, claim.ref.artifact_id) == birth_key)
            return ConfigurationAllocationEvidence("complete", birth, claims)
        except _Capacity:
            return ConfigurationAllocationEvidence("capacity")
        except (_Unavailable, ValueError, TypeError, KeyError, AttributeError, StopIteration):
            return ConfigurationAllocationEvidence("unavailable")

    def _insert_original(self, record, prepared):
        from control_plane_kit_operations._configuration_preparation import _require_prepared
        _require_prepared(prepared, self._connection, record.identity, record.intent)
        identity = record.identity
        for ref, (birth, artifact) in zip(record.intent.configuration_instances.instances, prepared.births, strict=True):
            encoded = ConfigurationInstanceRefCodec().encode_canonical_bytes(ref)
            key = (identity.run_id.value, identity.activity_id, identity.attempt, ref.artifact_id)
            birth_key = (birth.run_id.value, birth.activity_id, birth.attempt, artifact)
            self._connection.execute("INSERT INTO cpk_effect_configuration_refs (" + ",".join(_NAMES)
                + ") VALUES (" + ",".join("%s" for _ in _NAMES) + ")",
                (*key, ref.workspace_id, ref.allocation_id, ref.runtime_id, ref.node_id,
                 encoded, sha256(encoded).hexdigest(), record.request_fingerprint,
                 record.original_start_event.event_id, *birth_key, key == birth_key))
            self._connection.execute("INSERT INTO cpk_configuration_claims "
                "(run_id,activity_id,attempt,artifact_id,workspace_id,allocation_id) VALUES (%s,%s,%s,%s,%s,%s)",
                (*key, ref.workspace_id, ref.allocation_id))

    def _require_original(self, record, *, read=None):
        read = _EvidenceRead(self._connection) if read is None else read
        identity = record.identity
        rows = read.query(_SELECT + " WHERE r.run_id=%s AND r.activity_id=%s AND r.attempt=%s"
            " ORDER BY r.artifact_id LIMIT 33", (identity.run_id.value, identity.activity_id, identity.attempt),
            records=33, octets=33 * 32768, cells=19, identities=2)
        if len(rows) != len(record.intent.configuration_instances.instances):
            raise OperationsRecordError("configuration protection is unavailable")
        evidence = tuple(_decode(row, read) for row in rows)
        if (tuple(value.ref for value in evidence) != record.intent.configuration_instances.instances
                or any(value.source.request_fingerprint != record.request_fingerprint
                    or value.source.original_event_id != record.original_start_event.event_id for value in evidence)):
            raise OperationsRecordError("configuration protection is unavailable")
        for value in evidence:
            key = (value.birth_identity.run_id.value, value.birth_identity.activity_id,
                value.birth_identity.attempt, value.birth_artifact_id)
            roots = read.query(_SELECT + " WHERE (r.run_id,r.activity_id,r.attempt,r.artifact_id)=(%s,%s,%s,%s)",
                key, records=1, octets=32768, cells=19, identities=2)
            if len(roots) != 1 or roots[0][16] is not True:
                raise OperationsRecordError("configuration protection is unavailable")
            root = _decode(roots[0], read)
            if root.identity != root.birth_identity or root.ref != value.ref:
                raise OperationsRecordError("configuration protection is unavailable")


def _validate_current_rows(connection):
    """Bounded full current verification; never migration, repair or backfill."""
    cursor = ("", "", 0, "")
    while True:
        read = _EvidenceRead(connection, standalone=True)
        rows = read.query(_SELECT + " WHERE (r.run_id,r.activity_id,r.attempt,r.artifact_id)>(%s,%s,%s,%s)"
            " ORDER BY r.run_id,r.activity_id,r.attempt,r.artifact_id LIMIT 32", cursor,
            records=32, octets=32 * 32768, cells=19, identities=2)
        if not rows:
            return
        try:
            for row in rows:
                evidence = _decode(row, read)
                root_key = (evidence.birth_identity.run_id.value, evidence.birth_identity.activity_id,
                    evidence.birth_identity.attempt, evidence.birth_artifact_id)
                roots = read.query(_SELECT + " WHERE (r.run_id,r.activity_id,r.attempt,r.artifact_id)=(%s,%s,%s,%s)",
                    root_key, records=1, octets=32768, cells=19, identities=2)
                if len(roots) != 1:
                    raise _Unavailable
                root = _decode(roots[0], read)
                if (root.identity != root.birth_identity or roots[0][16] is not True
                        or root.ref != evidence.ref):
                    raise _Unavailable
                cursor = tuple(row[:4])
        except (_Unavailable, _Capacity, TypeError, ValueError, KeyError):
            raise OperationsRecordError("configuration protection is unavailable") from None
