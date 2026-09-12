"""Real-store composition for #1764; no replacement persistence or planner."""
from importlib import import_module
import uuid

from control_plane_kit_operations.approvals import ApprovalCommandService
from control_plane_kit_operations.deployment_program import PrepareDeploymentProgram
from control_plane_kit_operations.deployment_program_interpreter import DeploymentProgram
from control_plane_kit_operations.planning import DesiredGraphCommandService
from control_plane_kit_operations.workflows import IdempotencyKey, OperationCommandService
from draft_catalogue_fixture import NOW, principal
from draft_selection_fixture import DraftSelectionFixture


class InterruptedPreparation(RuntimeError):
    pass


class StopBefore:
    def execute(self, command):
        raise InterruptedPreparation("physical preparation boundary")


class StopAfter:
    def __init__(self, service):
        self.service = service

    def execute(self, command):
        self.service.execute(command)
        raise InterruptedPreparation("physical preparation boundary")


class SavedPreparationFixture(DraftSelectionFixture):
    def setUp(self):
        super().setUp()
        self.values = import_module("control_plane_kit_operations.deployment_program")
        self.assertTrue(callable(getattr(self.values, "SavedDesiredTopologyRevision", None)),
                        "missing saved desired revision input")
        self.admission = import_module("control_plane_kit_operations.saved_deployment_preparation")

    def operations(self, *, uow=None, clock=None, id_factory=None):
        return OperationCommandService(uow or self.unit_of_work, clock=clock or (lambda: NOW),
                                       id_factory=id_factory or (lambda: uuid.uuid4().hex))

    def components(self, *, uow=None, operations=None):
        factory = uow or self.unit_of_work
        return (operations or self.operations(uow=factory),
                DesiredGraphCommandService(factory, clock=lambda: NOW, id_factory=lambda: uuid.uuid4().hex),
                self.planner(unit_of_work_factory=factory),
                ApprovalCommandService(factory, clock=lambda: NOW, id_factory=lambda: uuid.uuid4().hex))

    def program(self, *, uow=None, operations=None, stop=None):
        services = list(self.components(uow=uow, operations=operations))
        admission = self.admission.SavedDeploymentPreparationService(uow or self.unit_of_work, services[0])
        if stop == "admission":
            services[2] = StopBefore()
        elif stop == "plan":
            services[3] = StopBefore()
        elif stop == "approval":
            services[3] = StopAfter(services[3])
        return DeploymentProgram(*services, saved_preparations=admission)

    def selected(self, **kwargs):
        draft = self.create(**kwargs)
        self.catalogue().execute(self.select_command(draft, key="select-" + draft.draft_id))
        return draft

    def prepare_command(self, draft, *, key="prepare"):
        workspace = self.workspace()
        return PrepareDeploymentProgram(context=principal().command_context("workspace-a"),
            desired=self.values.SavedDesiredTopologyRevision(draft.draft_id, draft.revision),
            expected_current=workspace.current_lineage, expected_desired=workspace.desired_lineage,
            expected_desired_graph_revision=workspace.desired_graph_revision,
            title="Prepare saved draft", idempotency_key=IdempotencyKey(key), approval_comment="Review saved intent")

    def all_truth(self):
        return {**self.truth(), "cpk_operation_sessions": self.rows("cpk_operation_sessions"),
                "cpk_registered_products": self.rows("cpk_registered_products")}

    def frozen_truth(self):
        mutable = {"cpk_operation_sessions", "cpk_operation_actions", "cpk_activity_plans", "cpk_approval_requests"}
        return {name: rows for name, rows in self.all_truth().items() if name not in mutable}

    def prepared_session(self, result):
        with self.unit_of_work() as uow:
            plan = uow.stores.activity_history.get_plan(result.reference.plan_id)
            return uow.stores.activity_history.get_session(plan.session_id)


# Fixed protocol witnesses, independent of production metadata or helper output.
SAVED_ONLY_KEYS = frozenset({
    "deployment_prepare_saved_draft_id", "deployment_prepare_saved_revision",
    "deployment_prepare_saved_graph_id", "deployment_prepare_saved_current_graph_id",
    "deployment_prepare_saved_current_projection_id", "deployment_prepare_saved_desired_projection_id",
    "deployment_prepare_saved_desired_generation",
})
SAVED_METADATA_KEYS = SAVED_ONLY_KEYS | {"deployment_prepare_source", "deployment_prepare_intent_sha256"}


def expected_saved_metadata(command):
    import hashlib
    import json
    current = {"authored_graph_id": command.expected_current.authored_graph_id,
               "realized_projection_id": command.expected_current.realized_projection_id}
    desired = {"authored_graph_id": command.expected_desired.authored_graph_id,
               "realized_projection_id": command.expected_desired.realized_projection_id}
    intent = {"profile": "deployment-program-prepare-saved.v1",
              "workspace_id": command.context.workspace_id, "actor_id": command.context.actor_id,
              "desired": {"draft_id": command.desired.draft_id, "revision": command.desired.revision},
              "expected_current": current, "expected_desired": desired,
              "expected_desired_graph_revision": command.expected_desired_graph_revision,
              "title": command.title, "approval_comment": command.approval_comment}
    digest = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"deployment_prepare_source": "saved-revision.v1", "deployment_prepare_intent_sha256": digest,
            "deployment_prepare_saved_draft_id": command.desired.draft_id,
            "deployment_prepare_saved_revision": str(command.desired.revision),
            "deployment_prepare_saved_graph_id": command.expected_desired.authored_graph_id,
            "deployment_prepare_saved_current_graph_id": command.expected_current.authored_graph_id,
            "deployment_prepare_saved_current_projection_id": command.expected_current.realized_projection_id,
            "deployment_prepare_saved_desired_projection_id": command.expected_desired.realized_projection_id,
            "deployment_prepare_saved_desired_generation": str(command.expected_desired_graph_revision)}
