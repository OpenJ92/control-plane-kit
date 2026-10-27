"""One read-only snapshot of bounded event, ingress unit and owner facts."""

from control_plane_kit_core.operations import ActivityEventKind
from control_plane_kit_core.planning import ActivityId, AllocatePublicIngress, RemovePublicIngress
from control_plane_kit_core.secrets import SecretUseIntent
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations.execution_invocations import ExecutionInvocationOutcome as Outcome
from control_plane_kit_operations.ingress_authorities import CloudflareZoneIngressAuthorityCodec, OwnedIngressResourceStatus
from control_plane_kit_operations.ingress_invocations import require, _canonical, custody_receipt_from_descriptor
from control_plane_kit_operations.records import ManagedExecutionCommandIntent
from control_plane_kit_operations.read_pages import ReadPage, ReadPageCandidate, OrdinalReadCursor, ReadCollection
from control_plane_kit_operations.read_services.errors import ReadModelError
from control_plane_kit_operations.read_services.operations_history import _OperationsHistoryReadProjection
from .execution import _activity_event
from .activity_history import _plan_record
from .graph_store import _realized_graph_projection_record
from .ingress_authority_store import _row_to_cloudflare_resource, _row_to_generated_ingress_secret_reference
from .ingress_invocation_store import IngressInvocationStore, bounded_rows

_EVENT_COLUMNS = (("event_id", "text", 2048), ("run_id", "text", 2048), ("ordinal", "int", 0),
    ("event_type", "text", 64), ("occurred_at", "time", 0), ("payload", "json", 16384))
_RESOURCE_NAMES = ("workspace_id", "runtime_id", "ingress_id", "epoch", "status", "authority_ref", "provider_kind",
    "tunnel_name", "tunnel_id", "dns_record_id", "hostname", "zone_id", "lifecycle", "created_at", "observed_at",
    "source_run_id", "source_activity_id", "source_event_id", "removed_at", "removed_by_run_id")
_RESOURCE_COLUMNS = tuple((name,
    "int" if name == "epoch" else "time?" if name == "removed_at" else "time" if name in ("created_at", "observed_at")
    else "text?" if name == "removed_by_run_id" else "text",
    128 if name in ("tunnel_name", "tunnel_id", "dns_record_id") else 253 if name == "hostname"
    else 64 if name in ("status", "provider_kind", "lifecycle") else 2048) for name in _RESOURCE_NAMES)
_SECRET_COLUMNS = (("workspace_id", "text", 2048), ("purpose", "text", 64), ("secret_ref", "text", 1024),
    ("recorded_at", "time", 0), ("source_run_id", "text", 2048), ("source_activity_id", "identifier", 200),
    ("source_event_id", "text", 2048), ("metadata", "json", 8192))


class RunEventsSnapshot:
    """Read facade only. Its owner supplies the fresh repeatable-read transaction."""
    def __init__(self, connection):
        self._connection = connection
        self._ingress = IngressInvocationStore(connection)

    def _rows(self, table, columns, where, values, **options):
        return bounded_rows(self._connection, table, columns, where, values, **options)

    def _workspace(self, workspace_id):
        rows = self._rows("cpk_workspaces", (("workspace_id", "text", 2048),),
            "workspace_id=%s", (workspace_id,))
        if rows != [(workspace_id,)]:
            raise ReadModelError("missing workspace")

    def _run_scope(self, workspace_id, run_id):
        rows = self._rows("cpk_activity_runs AS r JOIN cpk_execution_requests AS q ON q.request_id=r.request_id",
            (("r.run_id", "text", 2048), ("q.workspace_id", "text", 2048)), "r.run_id=%s", (run_id,))
        if rows != [(run_id, workspace_id)]:
            raise ReadModelError("missing run in workspace")

    def run_events(self, request):
        try:
            projection = _OperationsHistoryReadProjection(self._workspace, None, execution_store=self,
                run_event_scope=self._run_scope, ingress_summary=self._summary)
            return projection.run_events(request)
        except ReadModelError:
            raise
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            raise ReadModelError("run ingress history is unavailable") from None

    def event_page(self, request):
        require(request.collection is ReadCollection.RUN_EVENTS)
        where, parameters = "run_id=%s", (request.scope.run_id,)
        if request.cursor is not None:
            where += " AND (ordinal, event_id)>(%s,%s)"
            parameters += (request.cursor.ordinal, request.cursor.item_id)
        rows = self._rows("cpk_activity_events", _EVENT_COLUMNS, where, parameters,
            limit=request.limit + 1, order="ordinal ASC, event_id ASC")
        return ReadPage.from_candidates(request, tuple(ReadPageCandidate(_activity_event(row),
            OrdinalReadCursor(ReadCollection.RUN_EVENTS, request.scope, row[2], row[0])) for row in rows))

    def _original(self, context, event):
        # No current policy, active-registration selection, provider reads or locks.
        require(event.run_id == context.command.run_id and event.event_id == context.original_event_id
            and event.activity_id == context.activity_id and event.occurred_at == context.created_at)
        registration = self._rows("cpk_ingress_authorities",
            (("registration_id", "text", 2048), ("workspace_id", "text", 2048),
             ("authority_ref", "text", 2048), ("authority", "json", 8192)),
            "registration_id=%s", (context.ingress_registration_id,))
        require(len(registration) == 1 and registration[0][:3] == (context.ingress_registration_id,
            context.workspace_id, context.ingress.authority_ref.reference_id)
            and CloudflareZoneIngressAuthorityCodec().decode(registration[0][3]) == context.authority)
        rows = self._rows("cpk_execution_command_receipts",
            (("intent_fingerprint", "hash", 64), ("managed_intent", "json", 8192),
             ("receipt_status", "text", 64), ("completed_at", "time?", 0)),
            "run_id=%s AND idempotency_key=%s", (context.command.run_id, context.command.idempotency_key))
        require(len(rows) == 1 and rows[0][0] == context.command.intent_fingerprint)
        parent = ManagedExecutionCommandIntent.from_descriptor(rows[0][1])
        require(parent.workspace_id == context.workspace_id and parent.actor.subject_id == context.custody_grant.actor_subject
            and rows[0][2] in ("incomplete", "completed")
            and (rows[0][2] == "completed") == (rows[0][3] is not None))
        linkage = self._rows("cpk_activity_runs AS r JOIN cpk_execution_requests AS q ON q.request_id=r.request_id",
            tuple((name, "text", 2048) for name in ("r.plan_id", "q.plan_id", "q.workspace_id", "q.session_id", "q.request_id")),
            "r.run_id=%s", (context.command.run_id,))
        require(len(linkage) == 1 and linkage[0][:3] == (context.plan_id, context.plan_id, context.workspace_id)
            and linkage[0][3:] == (context.custody_grant.session_id, context.custody_grant.operation_id))
        plan_names = ("plan_id", "session_id", "base_graph_id", "desired_graph_id", "base_realized_projection_id",
            "desired_realized_projection_id", "desired_graph_revision", "status", "created_at", "payload")
        plan_rows = self._rows("cpk_activity_plans", tuple((name,
            "json" if name == "payload" else "time" if name == "created_at" else "int" if name == "desired_graph_revision"
            else "text", 1048576 if name == "payload" else 64 if name == "status" else 2048) for name in plan_names),
            "plan_id=%s", (context.plan_id,))
        require(len(plan_rows) == 1)
        plan = _plan_record(plan_rows[0])
        require(plan.session_id == context.custody_grant.session_id and
            (plan.base_realized_projection_id, plan.desired_realized_projection_id) ==
                (context.base_projection_id, context.desired_projection_id))
        operation = plan.plan.activity(ActivityId(context.activity_id)).operation
        require(type(operation) is (AllocatePublicIngress if context.operation == "allocate" else RemovePublicIngress)
            and operation.target.ingress_id == context.ingress_id)
        projection_names = ("projection_id", "workspace_id", "source_authored_graph_id", "projection_kind",
            "projection_key", "projection_digest", "graph_descriptor", "created_by", "created_at")
        for projection_id, authored_id, selected in ((context.base_projection_id, plan.base_graph_id, context.operation == "remove"),
                (context.desired_projection_id, plan.desired_graph_id, context.operation == "allocate")):
            projected = self._rows("cpk_realized_graph_projections", tuple((name,
                "json" if name == "graph_descriptor" else "time" if name == "created_at" else
                "hash" if name == "projection_digest" else "text",
                1048576 if name == "graph_descriptor" else 64 if name in ("projection_digest", "projection_kind") else 2048)
                for name in projection_names), "projection_id=%s", (projection_id,))
            require(len(projected) == 1)
            projection = _realized_graph_projection_record(projected[0])
            require(projection.workspace_id == context.workspace_id and projection.source_authored_graph_id == authored_id)
            if selected:
                graph = DEFAULT_GRAPH_CODEC.decode(projection.graph_descriptor)
                require(tuple(value for value in graph.public_ingresses if value.ingress_id == context.ingress_id) == (context.ingress,))
        use_names = ("workspace_id", "secret_reference", "use_intent", "actor_subject", "operation_id", "session_id", "run_id",
            "activity_id", "effect_id", "probe_id", "authorization_kind", "requested_at")
        uses = self._rows("cpk_secret_use_authorizations", tuple((name,
            "time" if name == "requested_at" else "text?" if name == "probe_id" else "text",
            64 if name in ("use_intent", "authorization_kind") else 2048) for name in use_names),
            "authorization_id=%s", (context.authorization_id,))
        from .temporal import decode_postgres_timestamp
        require(len(uses) == 1 and uses[0][:-1] == (context.workspace_id, context.authority.api_token_ref.reference_id,
            SecretUseIntent.CLOUDFLARE_API_TOKEN.value, parent.actor.subject_id, context.custody_grant.operation_id,
            context.custody_grant.session_id, context.command.run_id, context.activity_id, context.original_event_id,
            None, "operational") and decode_postgres_timestamp(uses[0][-1]) == context.created_at)
        # A retained completed receipt is distinct from delivery to its caller.
        return rows[0][2]

    def _resource(self, context):
        if context.operation == "remove":
            where, parameters = "workspace_id=%s AND ingress_id=%s AND epoch=%s", (
                context.workspace_id, context.ingress_id, context.original_resource.epoch)
        else:
            where, parameters = "workspace_id=%s AND ingress_id=%s AND source_activity_id=%s AND source_event_id=%s", (
                context.workspace_id, context.ingress_id, context.activity_id, context.original_event_id)
        rows = self._rows("cpk_cloudflare_ingress_resources", _RESOURCE_COLUMNS, where, parameters)
        require(len(rows) <= 1)
        return None if not rows else _row_to_cloudflare_resource(rows[0])

    def _secret(self, context):
        source = context.original_secret
        run, activity, event = ((context.command.run_id, context.activity_id, context.original_event_id)
            if source is None else (source.source_run_id, source.source_activity_id, source.source_event_id))
        rows = self._rows("cpk_generated_ingress_secret_references", _SECRET_COLUMNS,
            "workspace_id=%s AND purpose='cloudflared-tunnel-token' AND source_run_id=%s "
            "AND source_activity_id=%s AND source_event_id=%s", (context.workspace_id, run, activity, event))
        require(len(rows) <= 1)
        return None if not rows else _row_to_generated_ingress_secret_reference(rows[0])

    def _fold(self, context, facts):
        resource, secret = self._resource(context), self._secret(context)
        if context.operation == "remove":
            require(resource is not None and secret == context.original_secret)
            # Immutable allocation fields cannot silently retarget a later epoch.
            from dataclasses import replace
            require(replace(resource, status=context.original_resource.status,
                source_run_id=context.original_resource.source_run_id, removed_at=None,
                removed_by_run_id=None) == context.original_resource)
            if resource.status is OwnedIngressResourceStatus.REMOVED:
                require(resource.removed_by_run_id == context.command.run_id
                    and resource.source_run_id == context.command.run_id
                    and set(facts) == {"custody-revoke-returned", "provider-removal-returned"})
                return "removed"
            require(resource.status in (OwnedIngressResourceStatus.ACTIVE, OwnedIngressResourceStatus.REMOVING,
                OwnedIngressResourceStatus.UNCERTAIN))
            return resource.status.value
        if resource is None and secret is None:
            return "not-recorded"
        require(resource is not None and secret is not None and set(facts) == {"provider-created", "custody-write-returned"})
        provider, custody = facts["provider-created"], facts["custody-write-returned"]
        receipt = custody_receipt_from_descriptor(custody["receipt"])
        require(resource.workspace_id == context.workspace_id and resource.ingress_id == context.ingress_id
            and resource.authority_ref == context.ingress.authority_ref
            and resource.hostname == context.ingress.hostname and resource.zone_id == context.authority.zone_id
            and resource.lifecycle == context.ingress.lifecycle and resource.tunnel_id == provider["tunnel_id"]
            and resource.dns_record_id == provider["dns_record_id"]
            and (secret.custody_id, secret.provider_registration_id, secret.secret_ref,
                secret.provider_version_id, secret.provider_version_number) == (receipt.custody_id,
                receipt.provider_registration_id, receipt.reference, receipt.version_id, receipt.version_number))
        rows = self._rows("cpk_secret_references", (("workspace_id", "text", 2048),
            ("secret_reference", "text", 1024), ("provider_registration_id", "identifier", 200),
            ("status", "text", 64)), "registration_id=%s", (secret.reference_registration_id,))
        require(len(rows) == 1 and rows[0][:3] == (context.workspace_id, receipt.reference.reference_id,
            receipt.provider_registration_id) and rows[0][3] in ("active", "revoked", "superseded"))
        return resource.status.value

    def _summary(self, workspace_id, event):
        if event.kind is not ActivityEventKind.STEP_STARTED:
            return None
        context = self._ingress.context_for_original_event(workspace_id, event.run_id, event.event_id)
        if context is None:
            return None
        receipt_state = self._original(context, event)
        invocations = self._ingress.invocations(context)
        results = self._ingress.results_for_context(context)
        by_id = {result.invocation_id: result for result in results}
        facts = {result.descriptor()["tag"]: result.descriptor() for result in results}
        units = []
        for record in invocations:
            value = dict(kind=record.kind.value, disposition=record.disposition.value,
                outcome=None if record.outcome is None else record.outcome.value,
                decision_at=record.decision_at, completed_at=record.completed_at)
            if record.decision.denial_code is not None:
                value["denial"] = record.decision.denial_code
            result = by_id.get(record.invocation_id)
            if result is not None:
                metadata = result.descriptor()
                # The custody receipt contains a secret path and stays private.
                if metadata["tag"] == "custody-write-returned":
                    metadata = dict(version=1, tag="custody-write-returned")
                value["result"] = metadata
                value["recorded_at"] = result.recorded_at
            units.append(value)
        summary = dict(version=1, context_id=context.context_id, operation=context.operation,
            units=units, owner_fold=self._fold(context, facts), command_receipt=receipt_state,
            callback_acknowledgement="delivery-not-established-by-history", partial_allocation_cleanup="unsupported")
        if len(invocations) < len(context.sequence):
            summary["next_unit"] = dict(kind=context.sequence[len(invocations)].value, state="not-admitted")
        if invocations and invocations[-1].outcome is Outcome.PENDING:
            summary["pending_effect"] = "outcome-unknown"
        _canonical(summary, 8192)
        return summary
