"""E4 A syntax fixtures only; commitments are never admitted transfer proof."""
from copy import deepcopy
from hashlib import sha256

from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from tests.configuration_cleanup_contract_fixture import proposal_wire


def key(identity):
    return identity["run_id"], identity["activity_id"], identity["attempt"]


def member(ref):
    codec = ConfigurationInstanceRefCodec()
    value = codec.decode(ref)
    return dict(artifact_id=value.artifact_id, allocation_id=value.allocation_id,
        ref_fingerprint=sha256(codec.encode_canonical_bytes(value)).hexdigest())


def v2_wire(original=None, *, physical=None, transferred=(), revision=0):
    original = deepcopy(original or proposal_wire())
    all_rows = original["candidates"]
    physical = {row["ref"]["allocation_id"] for row in all_rows} if physical is None else set(physical)
    transferred = set(transferred)  # (original identity tuple, artifact_id)
    candidates, witnesses, relevant, required = [], {}, set(), set()
    for row in all_rows:
        for witness in row["completion_witnesses"]:
            witnesses[key(witness["source_identity"])] = witness
        if row["ref"]["allocation_id"] not in physical:
            continue
        artifact = row["ref"]["artifact_id"]
        uses = [use for use in row["protecting_uses"] if (key(use), artifact) not in transferred]
        relevant.update(key(use) for use in uses)
        for locator in (row["seed"], row["birth"]):
            pair = key(locator["source_identity"]), artifact
            if pair in transferred:
                required.add(pair)
        candidates.append(dict(ref=row["ref"], seed=row["seed"], birth=row["birth"],
            protecting_uses=uses, proposed_closures=deepcopy(uses)))
    refs = {row["ref"]["allocation_id"]: row["ref"] for row in all_rows}
    invocations = []
    for identity in sorted(relevant):
        witness = deepcopy(witnesses[identity])
        witness["selection_members"] = sorted((member(refs[allocation])
            for allocation in witness.pop("selection_allocations")), key=lambda row: row["artifact_id"])
        for selected in witness["selection_members"]:
            if (identity, selected["artifact_id"]) in transferred:
                required.add((identity, selected["artifact_id"]))
        invocations.append(witness)
    transfers = []
    for identity, artifact in sorted(required):
        row = next(row for row in all_rows if row["ref"]["artifact_id"] == artifact)
        transfers.append(dict(source_identity=dict(run_id=identity[0], activity_id=identity[1], attempt=identity[2]),
            **member(row["ref"]), acceptance_revision=revision))
    return dict(profile="configuration-cleanup-proposal.v2", context=original["context"],
        candidates=candidates, invocations=invocations, accepted_transfers=transfers)


def v2_inspection(document):
    document = deepcopy(document)
    witnesses = {key(row["source_identity"]): row for row in document["invocations"]}
    candidates = []
    fields = ("source_identity", "original_event_id", "original_event_ordinal",
        "request_fingerprint", "selection_fingerprint", "direct_event_id",
        "direct_event_ordinal", "result_kind", "outcome_fingerprint")
    for row in document["candidates"]:
        summaries = []
        for identity in row["protecting_uses"]:
            witness = witnesses[key(identity)]
            summary = {field: witness[field] for field in fields}
            summary.update(kind="completed", attempt_status=witness["result_kind"],
                selection_count=len(witness["selection_members"]), uncovered_outstanding_count=0)
            summaries.append(summary)
        candidates.append(dict(ref=row["ref"], seed=row["seed"], birth=row["birth"],
            invocations=summaries, blockers=[]))
    accounting = [dict(source_identity=row["source_identity"], selection_members=row["selection_members"])
        for row in document["invocations"]]
    return dict(profile="configuration-cleanup-inspection.v2", context=document["context"],
        candidates=candidates, accepted_transfers=document["accepted_transfers"],
        invocation_accounting=accounting)
