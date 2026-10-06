"""Immutable original refs and protective claims in the start owner's UoW."""
from __future__ import annotations

from hashlib import sha256
from contextlib import contextmanager

from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_operations.configuration_preparation import (
    ConfigurationAllocationEvidence, ConfigurationRefEvidence, _ConfigurationClaimDisposition, _ref,
)
from control_plane_kit_operations._configuration_protection import _ProtectiveConfigurationAllocation
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
_VALID += " AND c.runtime_id=r.runtime_id AND c.node_id=r.node_id"
_SELECT = "SELECT " + ",".join(f"CASE WHEN {_VALID} THEN r.{name} END" for name in _NAMES)
_SELECT += f", CASE WHEN {_VALID} THEN c.workspace_id END, CASE WHEN {_VALID} THEN c.allocation_id END"
_SELECT += " FROM cpk_effect_configuration_refs r LEFT JOIN cpk_configuration_claims c ON "
_SELECT += "(c.run_id,c.activity_id,c.attempt,c.artifact_id)=(r.run_id,r.activity_id,r.attempt,r.artifact_id)"

# C uses the same guarded nineteen-cell join and native decoder as _SELECT.
# Ordinary readers keep their existing fixed reservation and query shape.
_PHASE_REF_TABLE = "cpk_effect_configuration_refs r LEFT JOIN cpk_configuration_claims c ON " + \
    "(c.run_id,c.activity_id,c.attempt,c.artifact_id)=(r.run_id,r.activity_id,r.attempt,r.artifact_id)"
_PHASE_REF_COLUMNS = tuple((f"CASE WHEN {_VALID} THEN r.{name} END",
    "bytes" if name == "ref_preimage" else "bool" if name == "is_birth" else "int" if name in
    ("attempt", "birth_attempt") else "text", 4096 if name == "ref_preimage" else 2048) for name in _NAMES)
_PHASE_REF_COLUMNS += tuple((f"CASE WHEN {_VALID} THEN c.{name} END", "text", 128)
    for name in ("workspace_id", "allocation_id"))


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


def _paired_disposition(read, key, ref, *, expected=None, protective=False, allow_absent=False):
    """Fresh bounded structural correspondence, below full history proofs."""
    shape = " AND ".join(
        f"(({side}.cleanup_run_id IS NULL AND {side}.cleanup_activity_id IS NULL AND {side}.cleanup_attempt IS NULL) OR "
        f"({side}.cleanup_run_id COLLATE \"C\" ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{{0,199}}$' AND "
        f"{side}.cleanup_activity_id COLLATE \"C\" ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{{0,199}}$' AND {side}.cleanup_attempt>0)) "
        f"AND ({side}.accepted_revision IS NULL OR ({side}.accepted_revision BETWEEN 0 AND 9007199254740991 "
        f"AND {side}.cleanup_run_id IS NULL AND {side}.cleanup_activity_id IS NULL AND {side}.cleanup_attempt IS NULL))"
        for side in ("r", "c"))
    valid = " AND ".join((shape,
        "(r.workspace_id,r.allocation_id,r.runtime_id,r.node_id,r.ref_digest)=(%s,%s,%s,%s,%s)",
        "(c.workspace_id,c.allocation_id,c.runtime_id,c.node_id)=(r.workspace_id,r.allocation_id,r.runtime_id,r.node_id)",
        "r.disposition_kind=c.disposition_kind",
        *(f"{side}.protective=({side}.cleanup_run_id IS NULL AND {side}.cleanup_activity_id IS NULL "
          f"AND {side}.cleanup_attempt IS NULL AND {side}.accepted_revision IS NULL) AND "
          f"{side}.disposition_kind=(CASE WHEN {side}.accepted_revision IS NOT NULL THEN 'accepted-current' "
          f"WHEN {side}.cleanup_run_id IS NOT NULL THEN 'cleanup-closed' ELSE 'outstanding' END)"
          for side in ("r", "c")),
    ))
    # Invalid or oversized metadata is represented only as NULL/false.
    projections = [f"CASE WHEN {shape} THEN {side}.{name} END"
        for side in ("r", "c") for name in ("cleanup_run_id", "cleanup_activity_id", "cleanup_attempt")]
    projections += [f"CASE WHEN {shape} THEN {side}.accepted_revision END" for side in ("r", "c")]
    rows = read.query("SELECT " + ",".join(projections) + ",r.protective,c.protective,(" + valid + ") "
        "FROM (SELECT * FROM cpk_effect_configuration_refs WHERE "
        "(run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)) r FULL JOIN "
        "(SELECT * FROM cpk_configuration_claims WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)) c ON "
        "(c.run_id,c.activity_id,c.attempt,c.artifact_id)=(r.run_id,r.activity_id,r.attempt,r.artifact_id) "
        "",
        (ref.workspace_id, ref.allocation_id, ref.runtime_id, ref.node_id,
         sha256(ConfigurationInstanceRefCodec().encode_canonical_bytes(ref)).hexdigest(), *key, *key),
        records=1, octets=855, cells=11, identities=2)
    if not rows and allow_absent:
        return None
    if len(rows) != 1:
        raise _Unavailable
    row = rows[0]
    locator = row[:3]
    revision = row[6]
    if (row[10] is not True or locator != row[3:6] or revision != row[7]
            or type(row[8]) is not bool or type(row[9]) is not bool or row[8] != row[9]
            or row[8] != (locator == (None, None, None) and revision is None)
            or (protective and row[8] is not True)
            or (expected is not None and (locator != expected or revision is not None))):
        raise _Unavailable
    if row[8]:
        return _ConfigurationClaimDisposition("outstanding")
    if revision is not None:
        # Four exact relational identities, not successful-completion proof.
        accepted = read.query("SELECT 1 FROM cpk_configuration_claim_transfers t "
            "JOIN cpk_configuration_invocation_completions d ON "
            "(d.run_id,d.activity_id,d.attempt,d.workspace_id,d.request_fingerprint,d.selection_fingerprint,d.outcome_fingerprint)="
            "(t.run_id,t.activity_id,t.attempt,t.workspace_id,t.request_fingerprint,t.selection_fingerprint,t.outcome_fingerprint) "
            "JOIN cpk_configuration_acceptances h ON (h.workspace_id,h.pinned_revision,h.run_id)="
            "(t.workspace_id,t.acceptance_revision,t.run_id) "
            "JOIN cpk_configuration_accepted_slots s ON "
            "(s.workspace_id,s.pinned_revision,s.runtime_id,s.node_id,s.artifact_id,s.source_run_id,"
            "s.source_activity_id,s.source_attempt,s.source_artifact_id,s.full_ref_digest)="
            "(t.workspace_id,t.acceptance_revision,t.runtime_id,t.node_id,t.artifact_id,t.run_id,"
            "t.activity_id,t.attempt,t.artifact_id,t.ref_digest) "
            "WHERE (t.run_id,t.activity_id,t.attempt,t.artifact_id,t.workspace_id,t.allocation_id,"
            "t.runtime_id,t.node_id,t.ref_digest,t.acceptance_revision)=(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (*key, ref.workspace_id, ref.allocation_id, ref.runtime_id, ref.node_id,
             sha256(ConfigurationInstanceRefCodec().encode_canonical_bytes(ref)).hexdigest(), revision),
            records=1, octets=1, cells=1, identities=4)
        if accepted != [(1,)]:
            raise _Unavailable
        return _ConfigurationClaimDisposition("accepted-current", acceptance_revision=revision)
    if any(value is None for value in locator):
        raise _Unavailable
    # Exact key proof only. No D1/outcome/reservation recursion here.
    closed = read.query("SELECT 1 FROM cpk_configuration_claim_closures c "
        "JOIN cpk_configuration_invocation_closures i ON "
        "(i.run_id,i.activity_id,i.attempt,i.cleanup_run_id,i.cleanup_activity_id,i.cleanup_attempt,i.workspace_id)="
        "(c.run_id,c.activity_id,c.attempt,c.cleanup_run_id,c.cleanup_activity_id,c.cleanup_attempt,c.workspace_id) "
        "JOIN cpk_configuration_cleanup_members m ON "
        "(m.cleanup_run_id,m.cleanup_activity_id,m.cleanup_attempt,m.workspace_id,m.allocation_id)="
        "(c.cleanup_run_id,c.cleanup_activity_id,c.cleanup_attempt,c.workspace_id,c.allocation_id) "
        "WHERE (c.run_id,c.activity_id,c.attempt,c.artifact_id,c.cleanup_run_id,c.cleanup_activity_id,"
        "c.cleanup_attempt,c.workspace_id,c.allocation_id)=(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (*key, *locator, ref.workspace_id, ref.allocation_id), records=1, octets=1, cells=1, identities=3)
    if closed != [(1,)]:
        raise _Unavailable
    return _ConfigurationClaimDisposition("cleanup-closed",
        cleanup_identity=EffectAttemptIdentity(RunId(locator[0]), locator[1], locator[2]))


def _require_unreserved(read, ref):
    if read.query("SELECT 1 FROM cpk_configuration_cleanup_members "
            "WHERE (workspace_id,allocation_id)=(%s,%s) LIMIT 1",
            (ref.workspace_id, ref.allocation_id), records=1, octets=1, cells=1):
        raise _Unavailable


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

    def _configure_run(self, run_id, *, replay_request_id=None, replay_activity_id=None):
        """Bounded catalog-free routing from the plan's exact pinned graphs."""
        from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
        read = _EvidenceRead(self._connection)
        if replay_request_id is not None or replay_activity_id is not None:
            if type(replay_request_id) is not str or type(replay_activity_id) is not str:
                return "unavailable"
            # Select one original plan, not current material or lifetime history.
            # LIMIT 2 bounds matching output; the scan is of this one stored plan.
            rows = read.query("""
                WITH pinned AS (
                  SELECT CASE WHEN p.base_realized_projection_id IS NULL THEN b.graph_descriptor
                              ELSE bp.graph_descriptor END AS base,
                         CASE WHEN p.desired_realized_projection_id IS NULL THEN d.graph_descriptor
                              ELSE dp.graph_descriptor END AS desired, p.payload
                  FROM cpk_activity_runs r JOIN cpk_activity_plans p ON p.plan_id=r.plan_id
                  LEFT JOIN cpk_graph_versions b ON b.graph_id=p.base_graph_id
                  LEFT JOIN cpk_graph_versions d ON d.graph_id=p.desired_graph_id
                  LEFT JOIN cpk_realized_graph_projections bp ON bp.projection_id=p.base_realized_projection_id
                  LEFT JOIN cpk_realized_graph_projections dp ON dp.projection_id=p.desired_realized_projection_id
                  WHERE r.run_id=%s AND r.request_id=%s
                ), shaped AS (
                  SELECT *, CASE
                    WHEN jsonb_typeof(payload)='object'
                      AND payload->>'schema'='control-plane-kit.activity-plan'
                      AND jsonb_typeof(payload->'version')='number' AND payload->>'version'='1'
                    THEN payload
                    WHEN jsonb_typeof(payload)='object'
                      AND payload->>'schema'='control-plane-kit.operations.activity-plan-record'
                      AND jsonb_typeof(payload->'version')='number'
                      AND ((payload->>'version'='1' AND payload->>'derivation_profile'
                            IN ('structural-v1','management-graph-pair-v1'))
                        OR (payload->>'version'='2' AND payload->>'derivation_profile'='configuration-cleanup-v1'))
                    THEN payload->'plan'
                    ELSE NULL END AS plan
                  FROM pinned
                )
                SELECT jsonb_path_exists(base, '$.nodes.*.configuration_artifacts[*]')
                    OR jsonb_path_exists(desired, '$.nodes.*.configuration_artifacts[*]'),
                    base IS NOT NULL AND desired IS NOT NULL,
                    coalesce(payload->>'derivation_profile'='configuration-cleanup-v1', false)
                    OR payload ? 'cleanup_proposal' OR payload ? 'cleanup_proposal_fingerprint'
                    OR (payload->>'schema'='control-plane-kit.operations.activity-plan-record'
                        AND payload->'version'='2'::jsonb)
                    OR jsonb_path_exists(payload,
                        '$.**.operation ? (@.kind == "cleanup-configuration-instances")'),
                    coalesce(jsonb_typeof(plan)='object'
                      AND plan->>'schema'='control-plane-kit.activity-plan'
                      AND jsonb_typeof(plan->'version')='number' AND plan->>'version'='1'
                      AND jsonb_typeof(plan->'activities')='array'
                      AND (SELECT count(*)=1 AND bool_and(
                            jsonb_typeof(item->'operation')='object'
                            AND jsonb_typeof(item->'operation'->'kind')='string'
                            AND item->'operation'->>'kind'<>'')
                           FROM (SELECT item FROM jsonb_array_elements(
                             CASE WHEN jsonb_typeof(plan->'activities')='array'
                                  THEN plan->'activities' ELSE '[]'::jsonb END) AS elements(item)
                             WHERE jsonb_typeof(item)='object'
                               AND jsonb_typeof(item->'activity_id')='string'
                               AND item->>'activity_id'=%s LIMIT 2) matches), false)
                FROM shaped LIMIT 1
                """, (run_id, replay_request_id, replay_activity_id),
                records=1, octets=4, cells=4, identities=6)
            accounting = _ACCOUNTING.get()
            if rows and (rows[0][0] is True or rows[0][2] is True):
                accounting.active = True
            if (len(rows) != 1 or any(type(value) is not bool for value in rows[0])
                    or rows[0][1] is not True or rows[0][3] is not True):
                return "unavailable"
            return "cleanup" if rows[0][2] else "ordinary"
        rows = read.query("""
            WITH pinned AS (
              SELECT CASE WHEN p.base_realized_projection_id IS NULL THEN b.graph_descriptor
                          ELSE bp.graph_descriptor END AS base,
                     CASE WHEN p.desired_realized_projection_id IS NULL THEN d.graph_descriptor
                          ELSE dp.graph_descriptor END AS desired,
                     p.payload
              FROM cpk_activity_runs r JOIN cpk_activity_plans p ON p.plan_id=r.plan_id
              LEFT JOIN cpk_graph_versions b ON b.graph_id=p.base_graph_id
              LEFT JOIN cpk_graph_versions d ON d.graph_id=p.desired_graph_id
              LEFT JOIN cpk_realized_graph_projections bp ON bp.projection_id=p.base_realized_projection_id
              LEFT JOIN cpk_realized_graph_projections dp ON dp.projection_id=p.desired_realized_projection_id
              WHERE r.run_id=%s
            )
            SELECT jsonb_path_exists(base, '$.nodes.*.configuration_artifacts[*]')
                OR jsonb_path_exists(desired, '$.nodes.*.configuration_artifacts[*]'),
                base IS NOT NULL AND desired IS NOT NULL,
                coalesce(payload->>'derivation_profile'='configuration-cleanup-v1', false)
                OR payload ? 'cleanup_proposal' OR payload ? 'cleanup_proposal_fingerprint'
                OR jsonb_path_exists(payload,
                    '$.**.operation ? (@.kind == "cleanup-configuration-instances")')
            FROM pinned LIMIT 1
            """, (run_id,), records=1, octets=3, cells=3, identities=6)
        if rows and rows[0][1] is not True:
            raise _Unavailable
        # Any cleanup marker keeps accounting active even when departure left
        # no artifacts. This routing bit never validates or authorizes a plan.
        _ACCOUNTING.get().active = bool(rows and (rows[0][0] or rows[0][2]))

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
        # No historical refs is not authority: every fresh selection needs
        # the exact accepted current occurrence or verified empty E7 origin.
        acceptance = stores.configuration_acceptance
        workspace = stores.workspaces.get(intent.source.workspace_id)
        receipt = acceptance._current_manifest(workspace, read)
        if (receipt[0]["graph_id"], receipt[0]["projection_id"]) != (
                material.base_graph.source_authored_graph_id, material.base_graph.projection_id):
            raise _Unavailable
        rows = tuple(row for row in receipt[3] if row[1] == node_id)
        if node_id not in base.nodes:
            # Historical recreation requires a real accepted departure, not
            # desired-only omission or a missing membership row. E7 can only
            # admit a never-used node after its current/base proof above.
            if rows or (receipt[0]["pinned_revision"] is None
                    and self._node_history(proposed.configuration_instances.instances[0], read)):
                raise _Unavailable
            return proposed, ()
        if not rows or len(rows) > 32:
            raise _Unavailable
        observed = acceptance._observed_bindings(receipt, rows, read)
        refs = tuple(binding.ref for binding in observed.bindings)
        return _propose_configuration(identity, intent, refs), observed.bindings

    def _node_history(self, ref, read):
        """Discover both indexed sides before proving any source or pairing."""
        candidates = self._protective_candidates(read,
            "workspace_id=%s AND runtime_id=%s AND node_id=%s",
            (ref.workspace_id, ref.runtime_id, ref.node_id), maximum=256,
            order="artifact_id,run_id,activity_id,attempt")
        roots = {}
        for candidate in candidates:
            claim = self._paired_protective_ref(read, candidate)
            if (claim.ref.workspace_id, claim.ref.runtime_id, claim.ref.node_id) != (
                    ref.workspace_id, ref.runtime_id, ref.node_id):
                raise _Unavailable
            if claim.ref.allocation_id not in roots:
                roots[claim.ref.allocation_id] = self._protective_root(claim.ref, read)
            self._require_direct_root(claim, roots[claim.ref.allocation_id])
        return tuple(row[:5] for row in candidates)

    def _protective_candidates(self, read, where, params, *, maximum,
            order="run_id,activity_id,attempt,artifact_id"):
        # Scope is carried by each independent driver. Do not hide an orphan or
        # routing mismatch behind an inner join, cache hit, or truncation.
        names = ("run_id", "activity_id", "attempt", "artifact_id", "allocation_id",
            "workspace_id", "runtime_id", "node_id")
        columns = tuple((name, "int" if name == "attempt" else "text", cap)
            for name, cap in zip(names, (2048, 2048, 12, 63, 128, 128, 128, 128)))
        sides = tuple(read.bounded_rows(table, columns, "protective AND (" + where + ")", params,
            maximum=maximum, point=False, order=order)
            for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"))
        if any(len(rows) > maximum for rows in sides):
            raise _Capacity
        if any(any(value is None for value in row) for rows in sides for row in rows):
            raise _Unavailable
        keys = set(row[:4] for rows in sides for row in rows)
        if len(keys) > maximum:
            raise _Capacity
        if sides[0] != sides[1]:
            raise _Unavailable
        return sides[0]

    def _paired_protective_ref(self, read, candidate):
        rows = read.query(_SELECT + " WHERE (r.run_id,r.activity_id,r.attempt,r.artifact_id)=(%s,%s,%s,%s)",
            candidate[:4], records=1, octets=32768, cells=19, identities=2)
        if len(rows) != 1:
            raise _Unavailable
        row = rows[0]
        claim = _decode(row, read)
        if (*row[:4], row[5], row[4], row[6], row[7]) != tuple(candidate):
            raise _Unavailable
        _paired_disposition(read, candidate[:4], claim.ref, protective=True)
        # Only a freshly checked pair may populate the immutable material cache.
        read.refs[_key(row)] = row
        return claim

    def _protective_root(self, exact_ref, read):
        roots = read.query(_SELECT + " WHERE r.workspace_id=%s AND r.allocation_id=%s AND r.is_birth LIMIT 2",
            (exact_ref.workspace_id, exact_ref.allocation_id), records=2, octets=2 * 32768,
            cells=19, identities=2)
        if len(roots) != 1 or roots[0][16] is not True:
            raise _Unavailable
        birth = _decode(roots[0], read)
        if birth.identity != birth.birth_identity or birth.ref != exact_ref:
            raise _Unavailable
        _paired_disposition(read, tuple(roots[0][:4]), birth.ref)
        read.refs[_key(roots[0])] = roots[0]
        return birth

    @staticmethod
    def _require_direct_root(claim, birth):
        if (claim.ref != birth.ref or claim.birth_identity != birth.identity
                or claim.birth_artifact_id != birth.ref.artifact_id):
            raise _Unavailable

    def _protective_allocation_evidence(self, exact_ref, read):
        """Fresh protection and independent direct root; never reuse authority."""
        try:
            _ref(exact_ref)
            birth = self._protective_root(exact_ref, read)
            candidates = self._protective_candidates(read, "workspace_id=%s AND allocation_id=%s",
                (exact_ref.workspace_id, exact_ref.allocation_id), maximum=64)
            claims = tuple(self._paired_protective_ref(read, row) for row in candidates)
            for claim in claims:
                self._require_direct_root(claim, birth)
            return _ProtectiveConfigurationAllocation(birth, claims)
        except (_Capacity, _Unavailable):
            raise
        except (ValueError, TypeError, KeyError, AttributeError):
            raise _Unavailable from None

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
            allocation = self._protective_allocation_evidence(ref, read)
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
        for ref, allocation in zip(refs, selected, strict=True):
            _require_unreserved(read, ref)
            if allocation is not None:
                root = allocation.birth
                _paired_disposition(read, (root.identity.run_id.value, root.identity.activity_id,
                    root.identity.attempt, root.ref.artifact_id), ref, protective=True)
        total_claims = sum(len(value.claims) for value in allocations)
        # Fixed future envelope includes complete source context, both original
        # and direct 16KiB events, the full 8192-byte outcome, and its source links.
        count = len(refs)
        records, markers, statements = 24 * count + 264, 384 * count, 24 * count
        envelope = 192 * 1024 * count + 3 * 1024 * 1024 + 512 * 1024
        future = ConfigurationEvidenceFootprint(records,
            envelope - 128 * records - 16 * markers - 256 * statements, markers, statements)
        future = future.plus(ConfigurationEvidenceFootprint(13 * count, 4118 * count, 48 * count, 8 * count))
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

    def _require_current(self, prepared):
        """Selected allocation permission is never an issued-value cache."""
        read = _EvidenceRead(self._connection)
        try:
            for ref, (birth, artifact) in zip(prepared.intent.configuration_instances.instances,
                    prepared.births, strict=True):
                _require_unreserved(read, ref)
                # Absence is expected only for this new birth before insertion.
                # Once either side exists, its complete pair must be protective.
                _paired_disposition(read, (birth.run_id.value, birth.activity_id, birth.attempt, artifact),
                    ref, protective=True, allow_absent=birth == prepared.identity)
        except (_Capacity, _Unavailable):
            raise OperationsRecordError("configuration start requires current allocation permission") from None

    def read_allocation_evidence(self, exact_ref):
        """Complete historical protection only; never permission to reuse/release."""
        _ref(exact_ref)
        return self._allocation_evidence(exact_ref, _EvidenceRead(self._connection, standalone=True))

    def _allocation_evidence(self, exact_ref, read):
        try:
            from .configuration_cleanup_phase_read_bounds import _phase_rows, _phase_context
            _phase_context(self._connection, read=read)
            key = (exact_ref.workspace_id, exact_ref.allocation_id)
            # Bound claims before the reciprocal join can turn a changed key
            # into a NULL ref. Both complete sets and pairing are still proved.
            claimed = _phase_rows(read, "allocation-claims", key)
            rows = _phase_rows(read, "allocation-refs", key)
            if rows is None:
                rows = read.query(_SELECT + " WHERE r.workspace_id=%s AND r.allocation_id=%s"
                " ORDER BY r.run_id,r.activity_id,r.attempt,r.artifact_id LIMIT 65",
                (exact_ref.workspace_id, exact_ref.allocation_id), records=65,
                octets=65 * 32768, cells=19, identities=2)
            if len(rows) > 64:
                raise _Capacity
            if not rows:
                raise _Unavailable
            if claimed is None:
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
            for row, claim in zip(rows, claims, strict=True):
                _paired_disposition(read, tuple(row[:4]), claim.ref)
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
                "(run_id,activity_id,attempt,artifact_id,workspace_id,allocation_id,runtime_id,node_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (*key, ref.workspace_id, ref.allocation_id, ref.runtime_id, ref.node_id))

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
            _paired_disposition(read, (value.identity.run_id.value, value.identity.activity_id,
                value.identity.attempt, value.ref.artifact_id), value.ref)
            key = (value.birth_identity.run_id.value, value.birth_identity.activity_id,
                value.birth_identity.attempt, value.birth_artifact_id)
            roots = read.query(_SELECT + " WHERE (r.run_id,r.activity_id,r.attempt,r.artifact_id)=(%s,%s,%s,%s)",
                key, records=1, octets=32768, cells=19, identities=2)
            if len(roots) != 1 or roots[0][16] is not True:
                raise OperationsRecordError("configuration protection is unavailable")
            root = _decode(roots[0], read)
            _paired_disposition(read, key, root.ref)
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
                _paired_disposition(read, tuple(row[:4]), evidence.ref)
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
