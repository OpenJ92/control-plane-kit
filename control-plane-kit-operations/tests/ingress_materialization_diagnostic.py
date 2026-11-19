"""Read-only diagnostic of retained next-activity input; not execution authority."""
import re
import traceback

from control_plane_kit_operations._configuration_preparation import _configuration_accounting, _ACCOUNTING
from control_plane_kit_operations._configuration_acceptance import _PUBLICATION_SCOPE
from control_plane_kit_operations.coordinator import ExecuteActivityRun, ExecutionCoordinator
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.postgres.unit_of_work import PostgresUnitOfWork
from control_plane_kit_operations.workflows import IdempotencyKey


def inspect_materialization(connection_factory, *, workspace_id, request_id, run_id, plan_id, command_key, expected_plan):
    report = {"diagnostic_only": True, "fresh_accounting": True, "stage": "open-read-only-snapshot"}
    try:
        if _PUBLICATION_SCOPE.get() is not None or _ACCOUNTING.get() is not None:
            raise ValueError("fresh diagnostic context required")
        with PostgresUnitOfWork(connection_factory)._read_snapshot() as stores, _configuration_accounting() as accounting:
            mode = stores.connection.execute("SHOW transaction_read_only").fetchone()[0]
            isolation = stores.connection.execute("SHOW transaction_isolation").fetchone()[0]
            if mode != "on" or isolation != "repeatable read":
                raise ValueError("read-only snapshot required")
            report["transaction_read_only"] = True
            report["stage"] = "retained-coordinates"
            request = stores.execution.get_request(request_id)
            run = stores.execution.get_run(run_id)
            receipt = stores.execution.command_receipt_for_idempotency(run_id, command_key, for_update=False)
            if (request.identity.workspace_id != workspace_id or request.identity.plan_id != plan_id
                    or run.admission.request_id != request_id or run.plan_id != plan_id or receipt is None
                    or request.claim is None or receipt.worker_id != request.claim.worker_id
                    or receipt.claim_generation != request.claim.generation):
                raise ValueError("retained coordinates changed")
            plan = stores.activity_history.get_plan(plan_id)
            fields = ("base_graph_id", "base_realized_projection_id", "desired_graph_id",
                "desired_realized_projection_id", "desired_graph_revision", "derivation_profile")
            if (set(expected_plan) != set(fields)
                    or expected_plan["derivation_profile"] not in ("management-graph-pair-v1", "existing-runtime-bootstrap-v1")
                    or any(getattr(plan, name) != expected_plan[name] for name in fields)):
                raise ValueError("pinned materialization plan changed")
            command = ExecuteActivityRun(run_id,
                ExecutionWorkerAuthority(receipt.worker_id, receipt.authority_scopes),
                request.claim.fence, IdempotencyKey(command_key), max_effects=receipt.max_effects)
            report["stage"] = "configuration-context"
            # The method uses only its supplied stores/values. Construction bypasses
            # effect-port composition; execute/admit/start/fold are never entered.
            owner = object.__new__(ExecutionCoordinator)
            context = owner._configuration_context(stores, command, request, run)
            report["configuration_context_ready"] = True
            report["planned_activity_count"] = len(context.plan_record.plan.activities)
            report["ready_activity_count"] = len(context.schedule.ready)
            report["selected_configuration_intent"] = context.configuration_intent is not None
            report["accounted_records"] = accounting.used.records
            report["accounted_bytes"] = accounting.used.accounted_bytes
            report["stage"] = "read-only-materialization-complete"
    except Exception as error:
        frames = []
        for frame in traceback.extract_tb(error.__traceback__):
            for package in ("control_plane_kit_operations", "control_plane_kit_core"):
                marker = "/" + package + "/"
                if marker in frame.filename:
                    module = package + "/" + frame.filename.split(marker, 1)[1]
                    if re.fullmatch(r"[A-Za-z0-9_./-]{1,240}", module):
                        frames.append({"module": module, "line": frame.lineno})
                    break
        report["error_class"] = type(error).__name__[:80]
        report["owner_frames"] = frames[-12:]
        report["configuration_context_ready"] = False
    report["limitation"] = "Current retained inputs and fresh accounting only; not original elapsed-time/cumulative-budget replay, execution approval, or recovery."
    return report
