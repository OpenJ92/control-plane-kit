"""Authored managed values shared by teardown owner tests; no provider effects."""
from dataclasses import replace

from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductDescriptorCodec,
    ProductIdentity, ProductRuntimeContract, ProviderRuntimePort,
)
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.topology import DeploymentGraph, validate_graph
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile, derive_activity_plan
from control_plane_kit_operations.products import InlineDescriptorSource, RegisteredProduct
from tests.runtime_management_fixtures import bootstrap_management_graph


PROFILE = PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1


def managed_teardown(test_case):
    graph = bootstrap_management_graph(test_case)
    nodes, products = {}, []
    for name, node in graph.nodes.items():
        product = ContainerServerProduct(ProductIdentity("test", name, 1),
            OciImageReference("ghcr.io", "test/" + name, "sha256:" + "a" * 64),
            ProductRuntimeContract(sockets=node.sockets,
                provider_ports=tuple(ProviderRuntimePort(socket.name, 8000) for socket in node.sockets.providers),
                capabilities=node.block_spec.capabilities,
                gateway_transit=node.block_spec.gateway_transit,
                control_surfaces=node.block_spec.control_surfaces))
        registered = RegisteredProduct.from_document(workspace_id="workspace-a",
            descriptor_document=ProductDescriptorCodec().encode_document(product),
            source=InlineDescriptorSource(), imported_by="operator-a", imported_at="2026-07-22T12:00:00Z")
        products.append(registered)
        nodes[name] = replace(node, metadata={"product_identity": registered.reference.identity.key,
            "product_descriptor_digest": registered.reference.descriptor_sha256.value})
    graph = replace(graph, nodes=nodes, runtimes={"docker": replace(graph.runtimes["docker"],
        authority_ref=RuntimeAuthorityReference("local-docker"))}, public_ingresses=(replace(
            graph.public_ingresses[0], hostname="cpk-gateway-001.openj92.dev"),))
    empty = DeploymentGraph("empty")
    plan = derive_activity_plan(Deploy(validate_graph(graph), validate_graph(empty)), profile=PROFILE)
    return graph, empty, plan, tuple(products)


def seed_owned_ingress(stores, graph):
    from control_plane_kit_core.secrets import SecretProviderId, SecretProviderEndpointReference, SecretReference, SecretUseIntent
    from control_plane_kit_operations.secret_providers import RegisteredSecretProvider, RegisteredSecretReference, SecretProviderKind
    from tests.test_runtime_effect_translation import _registered_ingress_authority, _cloudflare_resource, _generated_ingress_secret
    selected = graph.public_ingresses[0]
    authority = _registered_ingress_authority()
    generated = _generated_ingress_secret()
    stores.secret_providers.register(RegisteredSecretProvider(
        registration_id=generated.provider_registration_id, workspace_id="workspace-a",
        provider_id=SecretProviderId("generated"), provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
        display_name="Fixture custody", endpoint_reference=SecretProviderEndpointReference("workspace-secrets"),
        credential_reference=SecretReference("secret://bootstrap/provider-token"),
        allowed_reference_prefixes=(SecretReference("secret://generated/ingress"),),
        allowed_intents=(SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN,),
        admitted_by="operator-a", admitted_at="2026-07-22T12:00:00Z"))
    stores.ingress_authorities.register(workspace_id="workspace-a", authority_ref=selected.authority_ref,
        authority=authority.authority, admitted_by="operator-a", admitted_at="2026-07-22T12:00:00Z")
    stores.secret_references.register(RegisteredSecretReference(
        registration_id=generated.reference_registration_id, workspace_id="workspace-a",
        reference=generated.secret_ref, provider_registration_id=generated.provider_registration_id,
        allowed_intents=(SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN,),
        admitted_by="operator-a", admitted_at="2026-07-22T12:00:00Z"))
    stores.ingress_resources.record_cloudflare(replace(_cloudflare_resource(),
        ingress_id=selected.ingress_id, authority_ref=selected.authority_ref, lifecycle=selected.lifecycle))
    stores.generated_ingress_secrets.record(generated)


def prepare_recorded_managed_origin(case, current, desired, teardown_plan, products):
    """Accept owner-produced runtime/allocation history, then prepare teardown.

    Recording ports simulate runtime/ingress effects through the real owners.
    Native/health completions remain recorded premises, not health conformance.
    Completion and original acceptance use real owners; no accepted pointer,
    receipt/header, refs or claims are fabricated.
    """
    import asyncio
    from hashlib import sha256
    from itertools import count
    from types import MethodType
    from control_plane_kit_core.planning import ObserveManagementBootstrap, ObserveNodeHealth, StartNode, StartRuntime
    from control_plane_kit_core.policies import PolicyScope
    from control_plane_kit_core.secrets import SecretProviderEndpointReference, SecretReference, SecretUseIntent
    from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
    from control_plane_kit_operations.admission import ExecutionAdmissionCommandService, RequestPlanExecution
    from control_plane_kit_operations.advancement import AdvanceCurrentGraph, CurrentGraphAdvancementCommandService
    from control_plane_kit_operations.approvals import ApprovalCommandService, RequestApproval, DecideApproval
    from control_plane_kit_operations.coordinator import (
        ActivityExecutionDispatcher, ExecuteActivityRun, ExecuteManagedActivityRun,
        ExecutionCoordinator, RuntimeInterpreterDispatcher,
    )
    from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
    from control_plane_kit_operations.effect_attempt_fold_interpreter import EffectAttemptFoldService
    from control_plane_kit_operations.effect_attempt_reconciliation_interpreter import EffectAttemptReconciliationService
    from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
    from control_plane_kit_operations.health_signing_authority import HealthSigningAuthorityReloadService
    from control_plane_kit_operations.ingress_authorities import IngressAuthorityProviderKind
    from control_plane_kit_operations.ingress_realization import IngressRealizationAdapter
    from control_plane_kit_operations.lifecycle import (
        ClaimAndOpenActivityRun, CompleteActivityRun, ExecutionLeaseDuration, RunLifecycleCommandService, StartActivityRun,
    )
    from control_plane_kit_operations.records import (
        ActivityPlanRecord, ActivityPlanStatus, ApprovalDecisionKind,
        GraphVersionRecord, OperationSessionRecord, OperationSessionStatus,
    )
    from control_plane_kit_operations.runtime_adapter_registration import RuntimeAdapterRegistration
    from control_plane_kit_operations.runtime_authorities import LocalDockerSocketAuthority
    from control_plane_kit_operations.secret_providers import (
        RegisterSecretProviderCommand, RegisterSecretReferenceCommand,
        SecretProviderKind, SecretProviderRegistrationService, SecretUseAuthorizationService,
    )
    from control_plane_kit_operations.workflows import IdempotencyKey
    from control_plane_kit_operations.workspaces import CreateWorkspace, WorkspaceCommandService
    from tests.health_effect_start_fixture import HEALTH_SCOPES, trusted_health_context
    from tests.postgres_effect_attempt_reconciliation_fixture import FailIfObserver
    from tests.receiver_fresh_execution_fixture import load_execution_context
    from tests.receiver_recorded_completion_fixture import retain_completion_inputs
    from tests.test_ingress_realization import RecordingIngressInterpreter
    from tests.test_managed_application_chain import ManagedApplicationFixture, RecordingManagedHealth, RecordingRuntime
    from tests.test_runtime_effect_translation import _registered_ingress_authority

    case.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
    case.ids = type(case.ids)()
    ids = count(1)
    origin_id = lambda: f"managed-origin-{next(ids)}"
    clock = lambda: "2026-07-22T12:00:15Z"
    created = WorkspaceCommandService(case.unit_of_work,
        clock=lambda: "2026-07-22T10:00:00Z", id_factory=lambda: "managed-origin-graph").create(
            CreateWorkspace("workspace-a", "Workspace A", "operator-a", IdempotencyKey("managed-origin-create")))
    initial = DEFAULT_GRAPH_CODEC.decode(created.current_graph.graph_descriptor)
    bootstrap = derive_activity_plan(Deploy(validate_graph(initial), validate_graph(current)), profile=PROFILE)
    with case.unit_of_work() as uow:
        stores = uow.stores
        for product in products:
            stores.registered_products.register(workspace_id="workspace-a", descriptor_document=product.descriptor_document,
                source=product.source, imported_by=product.imported_by, imported_at="2026-07-22T10:00:00Z")
        stores.runtime_authorities.register(workspace_id="workspace-a", authority_ref=current.runtimes["docker"].authority_ref,
            runtime_kind=current.runtimes["docker"].kind, authority=LocalDockerSocketAuthority(),
            admitted_by="operator-a", admitted_at="2026-07-22T10:00:00Z")
        stores.graphs.save(GraphVersionRecord.from_graph(graph_id="graph-current", workspace_id="workspace-a",
            version=stores.graphs.next_version_for_workspace("workspace-a"), graph=current,
            created_by="operator-a", created_at="2026-07-22T10:00:30Z"))
        pinned = stores.workspaces.set_desired_graph("workspace-a", "graph-current")
        stores.activity_history.add_session(OperationSessionRecord("managed-origin-session", "workspace-a", "operator-a",
            "Recorded managed predecessor", OperationSessionStatus.OPEN, clock()))
        stores.activity_history.add_plan(ActivityPlanRecord("managed-origin-plan", "managed-origin-session",
            pinned.current_graph_id, pinned.desired_graph_id, ActivityPlanStatus.PLANNED, clock(), bootstrap,
            base_realized_projection_id=pinned.current_realized_projection_id,
            desired_realized_projection_id=pinned.desired_realized_projection_id,
            derivation_profile=PROFILE, desired_graph_revision=pinned.desired_graph_revision))
        uow.commit()
    # Register authority only: the real allocation creates custody and history.
    authority = _registered_ingress_authority().authority
    registration = SecretProviderRegistrationService(case.unit_of_work)
    for reference, intent in (
        (authority.api_token_ref, SecretUseIntent.CLOUDFLARE_API_TOKEN),
        (authority.generated_secret_reference_prefix, SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN),
    ):
        provider = registration.register_provider(RegisterSecretProviderCommand(
            workspace_id="workspace-a", provider_id=reference.provider_id,
            provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
            display_name="Managed origin authority",
            endpoint_reference=SecretProviderEndpointReference("workspace-secrets"),
            credential_reference=SecretReference("secret://bootstrap/provider-token"),
            allowed_reference_prefixes=(reference,), allowed_intents=(intent,),
            admitted_by="operator-a", admitted_at=clock(),
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        if intent is SecretUseIntent.CLOUDFLARE_API_TOKEN:
            registration.register_reference(RegisterSecretReferenceCommand(
                workspace_id="workspace-a", reference=reference,
                provider_registration_id=provider.registration_id, allowed_intents=(intent,),
                admitted_by="operator-a", admitted_at=clock(),
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)))
        else:
            authority = replace(authority, generated_secret_provider_registration_id=provider.registration_id)
    with case.unit_of_work() as uow:
        uow.stores.ingress_authorities.register(workspace_id="workspace-a",
            authority_ref=current.public_ingresses[0].authority_ref, authority=authority,
            admitted_by="operator-a", admitted_at=clock())
        uow.commit()
    approvals = ApprovalCommandService(case.unit_of_work, clock=clock, id_factory=origin_id)
    approval = approvals.execute(RequestApproval("managed-origin-session", "managed-origin-plan", "operator-a",
        (PolicyScope.PLAN_REQUEST,), IdempotencyKey("managed-origin-approval"))).request
    approvals.execute(DecideApproval("managed-origin-session", approval.request_id, "manager-a",
        (approval.required_scope,), ApprovalDecisionKind.APPROVED, IdempotencyKey("managed-origin-decision")))
    admitted = ExecutionAdmissionCommandService(case.unit_of_work, clock=clock, id_factory=origin_id).execute(
        RequestPlanExecution("workspace-a", "managed-origin-session", "managed-origin-plan", approval.request_id,
            "operator-a", (PolicyScope.PLAN_EXECUTE, PolicyScope.RUNTIME_AUTHORITY_USE, PolicyScope.INGRESS_AUTHORITY_USE),
            IdempotencyKey("managed-origin-admit")))
    lifecycle = RunLifecycleCommandService(case.unit_of_work, clock=clock, id_factory=origin_id)
    opened = lifecycle.execute(ClaimAndOpenActivityRun(admitted.request.identity.request_id, case.authority(),
        ExecutionLeaseDuration(600), IdempotencyKey("managed-origin-claim")))
    run_id = opened.run.run_id
    fence = ExecutionLeaseFence(opened.request.claim.worker_id, opened.request.claim.generation)
    lifecycle.execute(StartActivityRun(run_id, case.authority(), fence, IdempotencyKey("managed-origin-start")))
    # Explicit inputs for the existing recording ports; no second fixture setup.
    case.now = clock
    case.documents = tuple(product.descriptor_document for product in products)
    case.rows = MethodType(ManagedApplicationFixture.rows, case)
    case.effect_order = []
    case.health = RecordingManagedHealth(case)
    case.runtime = RecordingRuntime(case)
    ingress = RecordingIngressInterpreter(case.tracker)
    authorizer = SecretUseAuthorizationService(case.unit_of_work)
    fold = EffectAttemptFoldService(case.unit_of_work, id_factory=origin_id)
    coordinator = ExecutionCoordinator(case.unit_of_work, lifecycle=lifecycle,
        adapter=ActivityExecutionDispatcher(
            RuntimeInterpreterDispatcher({current.runtimes["docker"].kind:
                RuntimeAdapterRegistration(case.runtime, exact_version_execute_with_authority=True)}, authorizer),
            IngressRealizationAdapter(case.unit_of_work,
                {IngressAuthorityProviderKind.CLOUDFLARE: ingress}, clock, authorizer)),
        start_service=EffectAttemptStartService(case.unit_of_work, id_factory=origin_id),
        fold_service=fold,
        reconciliation_service=EffectAttemptReconciliationService(case.unit_of_work, FailIfObserver(), fold),
        clock=clock, id_factory=origin_id, managed_health=case.health,
        health_signing_authority=HealthSigningAuthorityReloadService(case.unit_of_work))
    scopes = (PolicyScope.EXECUTION_OPERATE, *HEALTH_SCOPES)
    for activity in bootstrap.activities:
        suffix = sha256((run_id + "\0" + activity.activity_id.value).encode()).hexdigest()
        command = ExecuteActivityRun(run_id, case.authority(scopes=scopes), fence,
            IdempotencyKey("managed-origin-" + suffix), max_effects=1)
        context = load_execution_context(coordinator, command)
        case.assertTrue(context.schedule.ready)
        case.assertEqual(context.schedule.ready[0].activity_id, activity.activity_id)
        if type(activity.operation) in (ObserveManagementBootstrap, ObserveNodeHealth):
            # These are explicit recorded native/health premises only.
            retain_completion_inputs(case, context, activities=(activity,))
        else:
            result = asyncio.run(coordinator.execute_managed(ExecuteManagedActivityRun(command,
                trusted_health_context(actor=command.authority.worker_id,
                    workspace="workspace-a", scopes=scopes))))
            case.assertEqual((result.status.value, result.effects_attempted), ("progressed", 1))
    case.assertCountEqual([request.activity_id for request in case.runtime.calls],
        [activity.activity_id for activity in bootstrap.activities
            if type(activity.operation) in (StartRuntime, StartNode)])
    case.assertEqual(ingress.create_active_counts, [0])
    case.assertEqual(case.health.native_reads, [])
    case.assertEqual(case.health.signed_reads, [])
    case.assertEqual(case.tracker.active, 0)
    lifecycle.execute(CompleteActivityRun(run_id, case.authority(), fence, IdempotencyKey("managed-origin-complete")))
    CurrentGraphAdvancementCommandService(case.unit_of_work, clock=clock, id_factory=origin_id).execute(
        AdvanceCurrentGraph("workspace-a", run_id, "managed-origin-plan", pinned.current_graph_id,
            pinned.current_realized_projection_id, pinned.desired_graph_id, pinned.desired_realized_projection_id,
            pinned.desired_graph_revision, case.authority(), fence, IdempotencyKey("managed-origin-advance")))
    with case.unit_of_work() as uow:
        stores = uow.stores
        observed = stores.configuration_acceptance.read_current_configuration("workspace-a")
        case.assertEqual((observed.state, observed.graph_id, observed.projection_id, observed.manifest_slot_count,
            observed.bindings), ("complete", "graph-current", pinned.desired_realized_projection_id, 0, ()))
        stores.graphs.save(GraphVersionRecord.from_graph(graph_id="graph-desired", workspace_id="workspace-a",
            version=stores.graphs.next_version_for_workspace("workspace-a"), graph=desired,
            created_by="operator-a", created_at="2026-07-22T12:00:30Z"))
        workspace = stores.workspaces.set_desired_graph("workspace-a", "graph-desired")
        stores.activity_history.add_session(OperationSessionRecord("session-a", "workspace-a", "operator-a",
            "Deploy", OperationSessionStatus.OPEN, "2026-07-22T12:01:00Z"))
        stores.activity_history.add_plan(ActivityPlanRecord("plan-a", "session-a", "graph-current", "graph-desired",
            ActivityPlanStatus.PLANNED, "2026-07-22T12:02:00Z", teardown_plan,
            base_realized_projection_id=workspace.current_realized_projection_id,
            desired_realized_projection_id=workspace.desired_realized_projection_id,
            derivation_profile=PROFILE, desired_graph_revision=workspace.desired_graph_revision))
        uow.commit()
    return workspace
