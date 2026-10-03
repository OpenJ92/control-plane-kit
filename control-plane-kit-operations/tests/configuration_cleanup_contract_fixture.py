"""#1928 syntax fixtures only: no stored provenance or producer authority."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from importlib import import_module
from importlib.util import find_spec

import rfc8785

from control_plane_kit_core.configuration_instances import (
    ConfigurationInstanceRefCodec, ConfigurationInstanceSelection,
)
from control_plane_kit_core.configuration_invocation import configuration_invocation_selection_fingerprint
from control_plane_kit_core.planning import (
    ActivityId, ActivityImpact, ActivityPlan, CleanupConfigurationInstances,
    NodeTarget, PlannedActivity, RiskLevel, StartNode,
)
from control_plane_kit_core.planning.codec import activity_operation_descriptor
from tests.configuration_instance_fixture import configuration_ref


def require_cleanup(test):
    name = "control_plane_kit_operations.configuration_cleanup"
    test.assertIsNotNone(find_spec(name), "#1928 exact cleanup value interface is missing")
    return import_module(name)


def source_identity(number=0, *, maximum=False):
    return {"run_id": ("r" * 196 + f"{number:04d}") if maximum else f"run-{number}",
            "activity_id": "a" * 200 if maximum else "start-api", "attempt": 1}


def proposal_wire(*, accepted=False, claims=1, refs=None, maximum=False, text=None):
    long_text = text or ("\U0001f680" * 512 if maximum else "record")
    ref = configuration_ref()
    if maximum:
        ref = replace(ref, allocation_id="a" * 128, workspace_id="w" * 128,
            runtime_id="r" * 128, node_id="n" * 128, artifact_id="a" * 63,
            target_path="/" + "/".join(["p" * 127] * 4))
    refs = (ref,) if refs is None else refs
    ref = refs[0]
    identities = [source_identity(i, maximum=maximum) for i in range(claims)]
    context = dict(workspace_id=ref.workspace_id, session_id=long_text,
        base_graph_id=long_text, base_realized_projection_id=long_text,
        desired_graph_id=long_text, desired_realized_projection_id=long_text,
        desired_graph_revision=9007199254740991 if maximum else 2)
    if accepted:
        occurrence = dict(kind="configuration-acceptance", workspace_id=ref.workspace_id,
            pinned_revision=9007199254740991 if maximum else 1,
            graph_id=long_text, projection_id=long_text, projection_digest="a" * 64,
            action_id=long_text, event_id=long_text, run_id="r" * 200 if maximum else "run-accepted",
            request_id=long_text, plan_id=long_text, slot_count=0, slot_digest="b" * 64)
    else:
        occurrence = dict(kind="workspace-initialization", workspace_id=ref.workspace_id,
            profile="workspace-initialization.v1", initial_graph_id=long_text,
            initial_projection_id=long_text, graph_descriptor_sha256="a" * 64,
            projection_digest="b" * 64, configuration_slot_count=0,
            created_by=long_text, creation_idempotency_key="\x00" * 200 if maximum else "create")
    context["current_occurrence"] = occurrence
    selection = ConfigurationInstanceSelection(refs)
    fingerprint = configuration_invocation_selection_fingerprint(selection)
    witnesses = [dict(source_identity=identity, effect_kind="configuration-activity.v1",
        operation=activity_operation_descriptor(StartNode(NodeTarget(ref.node_id))),
        original_event_id=long_text, original_event_ordinal=1,
        request_fingerprint=sha256(str(index).encode()).hexdigest(), selection_fingerprint=fingerprint,
        selection_allocations=[row.allocation_id for row in selection.instances],
        direct_event_id=long_text + "-direct" if not maximum else "D" + long_text[1:],
        direct_event_ordinal=2, result_kind="succeeded", outcome_fingerprint="d" * 64)
        for index, identity in enumerate(identities)]
    candidates = []
    for row in sorted(refs, key=lambda item: item.allocation_id):
        candidates.append(dict(ref=ConfigurationInstanceRefCodec().encode(row),
            seed=dict(source_identity=identities[0], artifact_id=row.artifact_id),
            birth=dict(source_identity=identities[0], artifact_id=row.artifact_id),
            protecting_uses=deepcopy(identities), completion_witnesses=deepcopy(witnesses),
            proposed_closures=deepcopy(identities)))
    return dict(profile="configuration-cleanup-proposal.v1", context=context, candidates=candidates)


def cleanup_plan(wire):
    refs = tuple(ConfigurationInstanceRefCodec().decode(row["ref"]) for row in wire["candidates"])
    return ActivityPlan((PlannedActivity(ActivityId("cleanup-configuration"),
        CleanupConfigurationInstances(refs), risk=RiskLevel.CRITICAL,
        impact=ActivityImpact.DESTRUCTIVE),))


def proposal_digest(wire):
    return sha256(b"control-plane-kit.configuration-cleanup-proposal.v1\x00" + rfc8785.dumps(wire)).hexdigest()


def inspection_wire(*, kind="completed", unselected=0):
    proposal = proposal_wire()
    candidate = proposal["candidates"][0]
    witness = candidate["completion_witnesses"][0]
    summary = {key: witness[key] for key in ("source_identity", "original_event_id",
        "original_event_ordinal", "request_fingerprint", "selection_fingerprint")}
    summary.update(kind=kind, selection_count=1 + unselected, unselected_count=unselected)
    blockers = []
    if kind != "active":
        summary.update({key: witness[key] for key in ("direct_event_id", "direct_event_ordinal",
            "result_kind", "outcome_fingerprint")})
        summary["attempt_status"] = "succeeded"
    if kind != "completed":
        blockers.append("unresolved-invocation")
    if unselected:
        blockers.append("incomplete-invocation-selection")
    return dict(profile="configuration-cleanup-inspection.v1", context=proposal["context"],
        candidates=[dict(ref=candidate["ref"], seed=candidate["seed"], birth=candidate["birth"],
            invocations=[summary], blockers=sorted(blockers))])
