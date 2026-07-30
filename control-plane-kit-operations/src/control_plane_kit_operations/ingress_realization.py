"""Operations adapter for named public ingress realization."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Protocol

from control_plane_kit_core.operations.execution import EffectResultKind
from control_plane_kit_core.operations.lifecycle import FailureCategory
from control_plane_kit_core.planning import AllocatePublicIngress, RemovePublicIngress
from control_plane_kit_core.public_ingress import (
    NamedPublicIngress,
    PublicIngressLifecycle,
)
from control_plane_kit_core.secrets import (
    SecretCustodyGrant,
    SecretCustodyReceipt,
    SecretReference,
    SecretResolutionGrant,
    SecretUseIntent,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
from control_plane_kit_core.types import Protocol as SocketProtocol
from control_plane_kit_operations.coordinator import (
    ActivityExecutionOutcome,
    ActivityRealizationContext,
)
from control_plane_kit_operations.ingress_authorities import (
    CloudflareIngressTeardownActionKind,
    CloudflareOwnedIngressResource,
    CloudflareZoneIngressAuthority,
    GeneratedSecretPurpose,
    IngressAuthorityNotFound,
    IngressAuthorityProviderKind,
    IngressAuthorityRegistrationError,
    cloudflare_ingress_teardown_plan,
    record_generated_ingress_secret,
)
from control_plane_kit_operations.records import BoundedEvidence, FailureEvidence
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    SecretProviderRegistrationError,
    SecretUseResolutionAuthorizer,
    generated_secret_reference_candidate,
    secret_custody_correlation_for,
    secret_custody_grant_for,
    secret_use_correlation_for,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand


class IngressAllocationResult(Protocol):
    tunnel_id: str
    tunnel_name: str
    secret_custody_receipt: SecretCustodyReceipt
    dns_record_id: str
    hostname: str
    endpoint_url: str


class IngressProviderInterpreter(Protocol):
    """Provider-specific interpreter supplied at the composition boundary."""

    def create(
        self,
        ingress: NamedPublicIngress,
        *,
        authority: CloudflareZoneIngressAuthority,
        allocation_name: str,
        origin_service_url: str,
        secret_resolution_grant: SecretResolutionGrant,
        secret_custody_grant: SecretCustodyGrant,
    ) -> IngressAllocationResult: ...

    def teardown(
        self,
        *,
        authority: CloudflareZoneIngressAuthority,
        resources: CloudflareOwnedIngressResource,
        secret_resolution_grant: SecretResolutionGrant,
        secret_custody_grant: SecretCustodyGrant,
    ) -> None: ...


@dataclass(frozen=True)
class IngressRealizationAdapter:
    """Activity adapter for named public ingress effects."""

    unit_of_work_factory: Any
    interpreters: Mapping[IngressAuthorityProviderKind, IngressProviderInterpreter]
    clock: Any
    secret_use_authorizer: SecretUseResolutionAuthorizer | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.interpreters, Mapping):
            raise InvalidOperationCommand("ingress interpreters must be a mapping")
        normalized: dict[IngressAuthorityProviderKind, IngressProviderInterpreter] = {}
        for key, interpreter in self.interpreters.items():
            if not isinstance(key, IngressAuthorityProviderKind):
                raise InvalidOperationCommand(
                    "ingress interpreter keys must be provider kinds"
                )
            if not hasattr(interpreter, "create") or not hasattr(
                interpreter,
                "teardown",
            ):
                raise InvalidOperationCommand(
                    "ingress interpreter must expose create and teardown"
                )
            normalized[key] = interpreter
        object.__setattr__(self, "interpreters", normalized)
        if not callable(self.clock):
            raise InvalidOperationCommand("ingress clock must be callable")
        if (
            self.secret_use_authorizer is not None
            and not hasattr(self.secret_use_authorizer, "authorize_resolution")
        ):
            raise InvalidOperationCommand(
                "secret use authorizer must expose authorize_resolution"
            )

    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome:
        operation = context.activity.operation
        if isinstance(operation, AllocatePublicIngress):
            return self._allocate(context, operation)
        if isinstance(operation, RemovePublicIngress):
            return self._remove(context, operation)
        return ActivityExecutionOutcome.unsupported(
            FailureEvidence(
                FailureCategory.OPERATOR_REVIEW,
                "ingress.operation-unsupported",
                "ingress adapter only supports public ingress operations",
                BoundedEvidence.from_mapping(
                    {
                        "activity_id": context.activity.activity_id.value,
                        "operation": type(operation).__name__,
                    }
                ),
            )
        )

    def _allocate(
        self,
        context: ActivityRealizationContext,
        operation: AllocatePublicIngress,
    ) -> ActivityExecutionOutcome:
        try:
            graph = DEFAULT_GRAPH_CODEC.decode(context.desired_graph.graph_descriptor)
            ingress = _ingress_by_id(graph, operation.target.ingress_id)
            origin_service_url = _origin_service_url(graph, ingress)
            authority = self._active_authority(context, ingress)
            interpreter = self._interpreter(authority.provider_kind)
            with self.unit_of_work_factory() as unit_of_work:
                try:
                    unit_of_work.stores.ingress_resources.get_cloudflare(
                        context.request.identity.workspace_id,
                        ingress.ingress_id,
                    )
                except IngressAuthorityNotFound:
                    pass
                else:
                    return _unsupported(
                        context,
                        "ingress.allocate-conflict",
                        "owned ingress resource already exists",
                    )
        except (KeyError, ValueError, InvalidOperationCommand) as error:
            return _unsupported(context, "ingress.allocate-unsupported", str(error))

        resource: CloudflareOwnedIngressResource | None = None
        try:
            grant = self._authorize_api_token(context, authority.authority)
            custody_grant = self._generated_secret_custody_grant(
                context,
                authority.authority,
            )
            allocation = interpreter.create(
                ingress,
                authority=authority.authority,
                allocation_name=_allocation_name(context, ingress),
                origin_service_url=origin_service_url,
                secret_resolution_grant=grant,
                secret_custody_grant=custody_grant,
            )
        except SecretProviderRegistrationError:
            return _unsupported(
                context,
                "secret.use-not-authorized",
                "ingress authority secret use was not authorized",
            )
        except InvalidOperationCommand:
            return _unsupported(
                context,
                "secret.resolution-authorizer-invalid",
                "ingress secret authorization could not be established",
            )
        except Exception as error:  # noqa: BLE001 - provider failures are bounded.
            return _uncertain("ingress.allocate-uncertain", type(error).__name__)

        try:
            resource = CloudflareOwnedIngressResource(
                workspace_id=context.request.identity.workspace_id,
                runtime_id=graph.node(ingress.connector_node_id).runtime_id,
                ingress_id=ingress.ingress_id,
                authority_ref=ingress.authority_ref,
                provider_kind=authority.provider_kind,
                tunnel_name=allocation.tunnel_name,
                tunnel_id=allocation.tunnel_id,
                dns_record_id=allocation.dns_record_id,
                hostname=allocation.hostname,
                zone_id=authority.authority.zone_id,
                lifecycle=ingress.lifecycle,
                created_at=self.clock(),
                observed_at=self.clock(),
                source_run_id=context.run.run_id,
                source_activity_id=context.activity.activity_id.value,
                source_event_id=context.intent_event.event_id,
            )
            receipt = allocation.secret_custody_receipt
            if not isinstance(receipt, SecretCustodyReceipt) or not receipt.matches(
                custody_grant
            ):
                raise InvalidOperationCommand(
                    "ingress provider returned mismatched secret custody evidence"
                )
            reference_candidate = generated_secret_reference_candidate(
                grant=custody_grant,
                receipt=receipt,
                admitted_at=self.clock(),
            )
            secret_evidence = record_generated_ingress_secret(
                workspace_id=context.request.identity.workspace_id,
                purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                receipt=receipt,
                reference_registration_id=reference_candidate.registration_id,
                source_run_id=context.run.run_id,
                source_activity_id=context.activity.activity_id.value,
                source_event_id=context.intent_event.event_id,
                recorded_at=self.clock(),
            )
            with self.unit_of_work_factory() as unit_of_work:
                provider = unit_of_work.stores.secret_providers.require_active_registration(
                    custody_grant.workspace_id,
                    custody_grant.provider_registration_id,
                )
                if (
                    provider.endpoint_reference != custody_grant.endpoint_reference
                    or provider.credential_reference
                    != custody_grant.credential_reference
                ):
                    raise InvalidOperationCommand(
                        "secret custody provider changed before durable fold"
                    )
                unit_of_work.stores.secret_references.register(reference_candidate)
                unit_of_work.stores.ingress_resources.record_cloudflare(resource)
                unit_of_work.stores.generated_ingress_secrets.record(secret_evidence)
                unit_of_work.commit()
        except Exception as error:  # noqa: BLE001 - incomplete folding is uncertain.
            if resource is not None:
                try:
                    interpreter.teardown(
                        authority=authority.authority,
                        resources=resource,
                        secret_resolution_grant=grant,
                        secret_custody_grant=custody_grant,
                    )
                except Exception:  # noqa: BLE001 - compensation failure is bounded.
                    pass
            return _uncertain("ingress.record-uncertain", type(error).__name__)

        return ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping(
                {
                    "provider_kind": authority.provider_kind.value,
                    "ingress_id": ingress.ingress_id,
                    "runtime_id": resource.runtime_id,
                    "hostname": allocation.hostname,
                    "endpoint_url": allocation.endpoint_url,
                    "tunnel_name": allocation.tunnel_name,
                    "tunnel_id": allocation.tunnel_id,
                    "dns_record_id": allocation.dns_record_id,
                    "lifecycle": ingress.lifecycle.value,
                    "connector_material_recorded": True,
                }
            )
        )

    def _remove(
        self,
        context: ActivityRealizationContext,
        operation: RemovePublicIngress,
    ) -> ActivityExecutionOutcome:
        try:
            graph = DEFAULT_GRAPH_CODEC.decode(context.base_graph.graph_descriptor)
            ingress = _ingress_by_id(graph, operation.target.ingress_id)
            authority = self._active_authority(context, ingress)
            with self.unit_of_work_factory() as unit_of_work:
                resource = unit_of_work.stores.ingress_resources.require_active_cloudflare(
                    context.request.identity.workspace_id,
                    ingress.ingress_id,
                )
            plan = cloudflare_ingress_teardown_plan(
                authority=authority.authority,
                resource=resource,
            )
            interpreter = self._interpreter(authority.provider_kind)
            grant = self._authorize_api_token(context, authority.authority)
            with self.unit_of_work_factory() as unit_of_work:
                generated_secret = (
                    unit_of_work.stores.generated_ingress_secrets.get_by_source(
                        workspace_id=context.request.identity.workspace_id,
                        purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                        source_run_id=resource.source_run_id,
                        source_activity_id=resource.source_activity_id,
                        source_event_id=resource.source_event_id,
                    )
                )
            custody_grant = self._secret_custody_grant(
                context,
                authority.authority,
                generated_secret.secret_ref,
            )
        except SecretProviderRegistrationError:
            return _unsupported(
                context,
                "secret.use-not-authorized",
                "ingress authority secret use was not authorized",
            )
        except (KeyError, ValueError, InvalidOperationCommand) as error:
            return _unsupported(context, "ingress.remove-unsupported", str(error))

        if tuple(action.kind for action in plan.actions) == (
            CloudflareIngressTeardownActionKind.SKIP_RETAINED_OR_EXTERNAL,
        ):
            return ActivityExecutionOutcome.succeeded(
                BoundedEvidence.from_mapping(plan.descriptor())
            )
        with self.unit_of_work_factory() as unit_of_work:
            resource = unit_of_work.stores.ingress_resources.mark_removing(
                context.request.identity.workspace_id,
                ingress.ingress_id,
                source_run_id=context.run.run_id,
            )
            unit_of_work.commit()
        try:
            interpreter.teardown(
                authority=authority.authority,
                resources=resource,
                secret_resolution_grant=grant,
                secret_custody_grant=custody_grant,
            )
        except Exception as error:  # noqa: BLE001 - provider failures are bounded.
            with self.unit_of_work_factory() as unit_of_work:
                unit_of_work.stores.ingress_resources.mark_uncertain(
                    context.request.identity.workspace_id,
                    ingress.ingress_id,
                    source_run_id=context.run.run_id,
                )
                unit_of_work.commit()
            return _uncertain("ingress.remove-uncertain", type(error).__name__)
        with self.unit_of_work_factory() as unit_of_work:
            unit_of_work.stores.ingress_resources.mark_removed(
                context.request.identity.workspace_id,
                ingress.ingress_id,
                removed_at=self.clock(),
                removed_by_run_id=context.run.run_id,
            )
            unit_of_work.commit()
        return ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping(plan.descriptor())
        )

    def _active_authority(
        self,
        context: ActivityRealizationContext,
        ingress: NamedPublicIngress,
    ):
        with self.unit_of_work_factory() as unit_of_work:
            return unit_of_work.stores.ingress_authorities.require_active_for_hostname(
                context.request.identity.workspace_id,
                ingress.authority_ref,
                ingress.hostname,
            )

    def _interpreter(
        self,
        provider_kind: IngressAuthorityProviderKind,
    ) -> IngressProviderInterpreter:
        interpreter = self.interpreters.get(provider_kind)
        if interpreter is None:
            raise InvalidOperationCommand(
                f"no ingress interpreter is configured for {provider_kind.value!r}"
            )
        return interpreter

    def _authorize_api_token(
        self,
        context: ActivityRealizationContext,
        authority: CloudflareZoneIngressAuthority,
    ) -> SecretResolutionGrant:
        if self.secret_use_authorizer is None:
            raise InvalidOperationCommand(
                "ingress secret resolution requires an operations authorizer"
            )
        grant = self.secret_use_authorizer.authorize_resolution(
            AuthorizeSecretUse(
                workspace_id=context.request.identity.workspace_id,
                reference=authority.api_token_ref,
                intent=SecretUseIntent.CLOUDFLARE_API_TOKEN,
                actor_subject=context.authority.worker_id,
                correlation_id=secret_use_correlation_for(
                    workspace_id=context.request.identity.workspace_id,
                    reference=authority.api_token_ref,
                    intent=SecretUseIntent.CLOUDFLARE_API_TOKEN,
                    actor_subject=context.authority.worker_id,
                    operation_id=context.request.identity.request_id,
                    session_id=context.plan_record.session_id,
                    run_id=context.run.run_id,
                    activity_id=context.activity.activity_id.value,
                    effect_id=context.intent_event.event_id,
                ),
                requested_at=context.intent_event.occurred_at,
                actor_scopes=context.authority.scopes,
                operation_id=context.request.identity.request_id,
                session_id=context.plan_record.session_id,
                run_id=context.run.run_id,
                activity_id=context.activity.activity_id.value,
                effect_id=context.intent_event.event_id,
            )
        )
        if (
            not isinstance(grant, SecretResolutionGrant)
            or grant.workspace_id != context.request.identity.workspace_id
            or grant.effect_id != context.intent_event.event_id
            or not grant.permits(
                authority.api_token_ref,
                SecretUseIntent.CLOUDFLARE_API_TOKEN,
            )
        ):
            raise InvalidOperationCommand(
                "secret use authorizer returned an invalid ingress grant"
            )
        return grant

    def _generated_secret_custody_grant(
        self,
        context: ActivityRealizationContext,
        authority: CloudflareZoneIngressAuthority,
    ) -> SecretCustodyGrant:
        return self._secret_custody_grant(
            context,
            authority,
            _generated_secret_reference(context, authority),
        )

    def _secret_custody_grant(
        self,
        context: ActivityRealizationContext,
        authority: CloudflareZoneIngressAuthority,
        reference: SecretReference,
    ) -> SecretCustodyGrant:
        with self.unit_of_work_factory() as unit_of_work:
            provider = unit_of_work.stores.secret_providers.require_active_registration(
                context.request.identity.workspace_id,
                authority.generated_secret_provider_registration_id,
            )
        correlation_id = secret_custody_correlation_for(
            workspace_id=context.request.identity.workspace_id,
            provider_registration_id=provider.registration_id,
            reference=reference,
            intent=SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN,
            actor_subject=context.authority.worker_id,
            operation_id=context.request.identity.request_id,
            session_id=context.plan_record.session_id,
            run_id=context.run.run_id,
            activity_id=context.activity.activity_id.value,
            effect_id=context.intent_event.event_id,
        )
        return secret_custody_grant_for(
            provider=provider,
            workspace_id=context.request.identity.workspace_id,
            reference=reference,
            intent=SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN,
            actor_subject=context.authority.worker_id,
            actor_scopes=context.authority.scopes,
            correlation_id=correlation_id,
            operation_id=context.request.identity.request_id,
            session_id=context.plan_record.session_id,
            run_id=context.run.run_id,
            activity_id=context.activity.activity_id.value,
            effect_id=context.intent_event.event_id,
        )


def _ingress_by_id(graph: DeploymentGraph, ingress_id: str) -> NamedPublicIngress:
    for ingress in graph.public_ingresses:
        if ingress.ingress_id == ingress_id:
            return ingress
    raise InvalidOperationCommand("public ingress is missing from pinned graph")


def _origin_service_url(graph: DeploymentGraph, ingress: NamedPublicIngress) -> str:
    node = graph.node(ingress.target.node_id)
    endpoint = node.endpoint(ingress.target.provider_socket)
    if endpoint.protocol is not SocketProtocol.HTTP:
        raise InvalidOperationCommand("public ingress target must provide HTTP")
    url = endpoint.url
    if not url.startswith("http://"):
        raise InvalidOperationCommand(
            "public ingress origin must be an internal HTTP URL"
        )
    return url


def _allocation_name(
    context: ActivityRealizationContext,
    ingress: NamedPublicIngress,
) -> str:
    digest = hashlib.sha256(
        "|".join(
            (
                context.request.identity.workspace_id,
                ingress.ingress_id,
                context.run.run_id,
                context.activity.activity_id.value,
                context.intent_event.event_id,
            )
        ).encode("utf-8")
    ).hexdigest()[:12]
    ingress_part = "".join(
        character if character.isalnum() or character in "._:-" else "-"
        for character in ingress.ingress_id
    ).strip(".:-_")
    if not ingress_part:
        ingress_part = "ingress"
    prefix = f"cpk-{ingress_part}"
    max_prefix_length = 128 - 1 - len(digest)
    return f"{prefix[:max_prefix_length]}-{digest}"


def _generated_secret_reference(
    context: ActivityRealizationContext,
    authority: CloudflareZoneIngressAuthority,
) -> SecretReference:
    digest = hashlib.sha256(
        "|".join(
            (
                context.request.identity.workspace_id,
                GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN.value,
                context.run.run_id,
                context.activity.activity_id.value,
                context.intent_event.event_id,
            )
        ).encode("utf-8")
    ).hexdigest()
    return SecretReference(
        "/".join(
            (
                authority.generated_secret_reference_prefix.reference_id.rstrip("/"),
                GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN.value,
                digest,
            )
        )
    )


def _unsupported(
    context: ActivityRealizationContext,
    code: str,
    message: str,
) -> ActivityExecutionOutcome:
    return ActivityExecutionOutcome.unsupported(
        FailureEvidence(
            FailureCategory.OPERATOR_REVIEW,
            code,
            message,
            BoundedEvidence.from_mapping(
                {
                    "activity_id": context.activity.activity_id.value,
                    "operation": type(context.activity.operation).__name__,
                }
            ),
        )
    )


def _uncertain(code: str, exception_type: str) -> ActivityExecutionOutcome:
    return ActivityExecutionOutcome.uncertain(
        FailureEvidence(
            FailureCategory.UNCERTAIN,
            code,
            "ingress provider result is uncertain",
            BoundedEvidence.from_mapping({"exception_type": exception_type}),
        )
    )
