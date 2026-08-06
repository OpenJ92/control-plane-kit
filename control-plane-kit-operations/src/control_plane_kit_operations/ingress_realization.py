"""Operations adapter for named public ingress realization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
from typing import Any, Mapping, Protocol

from control_plane_kit_core.operations.execution import EffectResultKind
from control_plane_kit_core.operations.lifecycle import FailureCategory
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.planning import (
    AllocatePublicIngress,
    PublicIngressReservationTarget,
    ReleasePublicIngressReservation,
    RemovePublicIngress,
    WaitForPublicIngressReady,
)
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    LiteralEndpointMaterial,
    ProbeKind,
    ProbeOutcome,
    RuntimeEndpointObservation,
)
from control_plane_kit_core.public_ingress import (
    NamedPublicIngress,
    PublicIngressLifecycle,
    PublicIngressObservation,
    PublicIngressObservationStatus,
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
from control_plane_kit_core.verification import HttpCheck
from control_plane_kit_operations.coordinator import (
    ActivityExecutionOutcome,
    ActivityRealizationContext,
)
from control_plane_kit_operations.ingress_authorities import (
    CloudflareOwnedHostnameReservation,
    CloudflareOwnedIngressResource,
    CloudflareZoneIngressAuthority,
    GeneratedSecretPurpose,
    IngressAuthorityNotFound,
    IngressAuthorityProviderKind,
    IngressAuthorityRegistrationError,
    OwnedHostnameReservationStatus,
    RegisteredIngressAuthority,
    cloudflare_ingress_teardown_plan,
    record_generated_ingress_secret,
)
from control_plane_kit_operations.records import (
    BoundedEvidence,
    FailureEvidence,
    ObservationRecord,
    ObservationStatus,
)
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


class IngressOwnedResourceCoordinates(Protocol):
    """Exact provider coordinates accepted by provider teardown."""

    tunnel_id: str
    tunnel_name: str
    dns_record_id: str
    hostname: str


@dataclass(frozen=True)
class IngressReservationCoordinates:
    """Exact provider coordinates projected from durable reservation truth."""

    dns_record_id: str
    hostname: str
    expected_tunnel_id: str

    def __post_init__(self) -> None:
        _exact_coordinate(self.dns_record_id, "dns_record_id")
        _exact_hostname(self.hostname)
        _exact_coordinate(self.expected_tunnel_id, "expected_tunnel_id")


class IngressResourcePresence(StrEnum):
    """Closed exact-resource presence returned by an ingress interpreter."""

    PRESENT = "present"
    ABSENT = "absent"


@dataclass(frozen=True)
class IngressReservationObservation:
    """Secret-free postcondition for one exact retained DNS record."""

    dns_record_id: str
    hostname: str
    presence: IngressResourcePresence
    tunnel_id: str | None = None

    def __post_init__(self) -> None:
        _exact_coordinate(self.dns_record_id, "dns_record_id")
        _exact_hostname(self.hostname)
        if not isinstance(self.presence, IngressResourcePresence):
            raise InvalidOperationCommand("reservation presence must be closed")
        if self.presence is IngressResourcePresence.PRESENT:
            if self.tunnel_id is None:
                raise InvalidOperationCommand(
                    "present reservation observation requires tunnel_id"
                )
            _exact_coordinate(self.tunnel_id, "tunnel_id")
        elif self.tunnel_id is not None:
            raise InvalidOperationCommand(
                "absent reservation observation cannot carry tunnel_id"
            )


@dataclass(frozen=True)
class IngressTunnelObservation:
    """Secret-free postcondition for one exact tunnel epoch."""

    tunnel_id: str
    presence: IngressResourcePresence

    def __post_init__(self) -> None:
        _exact_coordinate(self.tunnel_id, "tunnel_id")
        if not isinstance(self.presence, IngressResourcePresence):
            raise InvalidOperationCommand("tunnel presence must be closed")


@dataclass(frozen=True)
class RetainedIngressDeactivationResult:
    """Verified retained-off postconditions returned by an interpreter."""

    reservation: IngressReservationObservation
    tunnel: IngressTunnelObservation

    def __post_init__(self) -> None:
        if not isinstance(self.reservation, IngressReservationObservation):
            raise InvalidOperationCommand(
                "retained deactivation reservation observation is malformed"
            )
        if not isinstance(self.tunnel, IngressTunnelObservation):
            raise InvalidOperationCommand(
                "retained deactivation tunnel observation is malformed"
            )


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
        resources: IngressOwnedResourceCoordinates,
        secret_resolution_grant: SecretResolutionGrant,
        secret_custody_grant: SecretCustodyGrant,
    ) -> None: ...

    def rebind(
        self,
        ingress: NamedPublicIngress,
        *,
        authority: CloudflareZoneIngressAuthority,
        reservation: IngressReservationCoordinates,
        allocation_name: str,
        origin_service_url: str,
        secret_resolution_grant: SecretResolutionGrant,
        secret_custody_grant: SecretCustodyGrant,
    ) -> IngressAllocationResult: ...

    def deactivate_preserving_reservation(
        self,
        *,
        authority: CloudflareZoneIngressAuthority,
        reservation: IngressReservationCoordinates,
        resources: IngressOwnedResourceCoordinates,
        secret_resolution_grant: SecretResolutionGrant,
        secret_custody_grant: SecretCustodyGrant,
    ) -> RetainedIngressDeactivationResult: ...

    def release_reservation(
        self,
        *,
        authority: CloudflareZoneIngressAuthority,
        reservation: IngressReservationCoordinates,
        secret_resolution_grant: SecretResolutionGrant,
    ) -> IngressReservationObservation: ...


class PublicIngressReadinessVerifier(Protocol):
    """Observe one graph-derived public HTTP endpoint outside operations IO."""

    def observe(
        self,
        *,
        ingress: NamedPublicIngress,
        check: HttpCheck,
        endpoint: RuntimeEndpointObservation,
        attempt_timeout_seconds: float,
    ) -> PublicIngressObservation: ...


@dataclass(frozen=True)
class IngressRealizationAdapter:
    """Activity adapter for named public ingress effects."""

    unit_of_work_factory: Any
    interpreters: Mapping[IngressAuthorityProviderKind, IngressProviderInterpreter]
    clock: Any
    secret_use_authorizer: SecretUseResolutionAuthorizer | None = None
    readiness_verifier: PublicIngressReadinessVerifier | None = None

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
        if self.readiness_verifier is not None and not hasattr(
            self.readiness_verifier,
            "observe",
        ):
            raise InvalidOperationCommand(
                "public ingress readiness verifier must expose observe"
            )

    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome:
        operation = context.activity.operation
        if isinstance(operation, AllocatePublicIngress):
            return self._allocate(context, operation)
        if isinstance(operation, WaitForPublicIngressReady):
            return self._wait_until_ready(context, operation)
        if isinstance(operation, RemovePublicIngress):
            return self._remove(context, operation)
        if isinstance(operation, ReleasePublicIngressReservation):
            return self._release(context, operation)
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

    def _wait_until_ready(
        self,
        context: ActivityRealizationContext,
        operation: WaitForPublicIngressReady,
    ) -> ActivityExecutionOutcome:
        if self.readiness_verifier is None:
            return _unsupported(
                context,
                "ingress.readiness-verifier-missing",
                "no public ingress readiness verifier is configured",
            )
        try:
            graph = DEFAULT_GRAPH_CODEC.decode(context.desired_graph.graph_descriptor)
            ingress = _ingress_by_id(graph, operation.target.ingress_id)
            check = _public_ingress_http_check(graph, ingress)
            endpoint = RuntimeEndpointObservation(
                subject_id=ingress.target.node_id,
                socket_name=ingress.target.provider_socket,
                graph_id=context.desired_graph.source_authored_graph_id,
                protocol=SocketProtocol.HTTP,
                context=EndpointContext.PUBLIC,
                address=LiteralEndpointMaterial(
                    f"https://{ingress.hostname}:443"
                ),
            )
            attempted_at = _utc_timestamp(self.clock(), "ingress convergence clock")
            started_at = _utc_timestamp(
                context.intent_event.occurred_at,
                "ingress convergence start",
            )
            deadline = started_at + timedelta(
                seconds=ingress.convergence.maximum_elapsed_seconds
            )
        except (KeyError, TypeError, ValueError, InvalidOperationCommand) as error:
            return _unsupported(
                context,
                "ingress.readiness-contract-unsupported",
                str(error),
            )

        if attempted_at < started_at:
            return _unsupported(
                context,
                "ingress.convergence-clock-invalid",
                "ingress convergence clock precedes durable step intent",
            )
        if attempted_at >= deadline:
            return _convergence_timeout(context, ingress, deadline)

        try:
            observation = self.readiness_verifier.observe(
                ingress=ingress,
                check=check,
                endpoint=endpoint,
                attempt_timeout_seconds=(
                    ingress.convergence.attempt_timeout_seconds
                ),
            )
            _require_matching_readiness_observation(observation, ingress)
        except (TypeError, ValueError, InvalidOperationCommand):
            return _unsupported(
                context,
                "ingress.readiness-result-unsupported",
                "public ingress readiness evidence is invalid",
            )
        except Exception as error:  # noqa: BLE001 - observation failure is bounded.
            finished_at = _utc_timestamp(
                self.clock(),
                "ingress convergence clock",
            )
            if finished_at >= deadline:
                return _convergence_timeout(
                    context,
                    ingress,
                    deadline,
                    details={"exception_type": type(error).__name__},
                )
            return _convergence_progress(
                ingress,
                finished_at,
                deadline,
                details={"exception_type": type(error).__name__},
            )

        record = _readiness_observation_record(context, observation)
        evidence = BoundedEvidence.from_mapping(
            {"public_ingress_observation": observation.descriptor()}
        )
        finished_at = _utc_timestamp(
            self.clock(),
            "ingress convergence clock",
        )
        if finished_at >= deadline:
            return _convergence_timeout(
                context,
                ingress,
                deadline,
                details={"public_ingress_observation": observation.descriptor()},
                observations=(record,),
            )
        if observation.status is PublicIngressObservationStatus.READY:
            return ActivityExecutionOutcome.succeeded(
                evidence,
                observations=(record,),
            )
        return _convergence_progress(
            ingress,
            finished_at,
            deadline,
            details={"public_ingress_observation": observation.descriptor()},
            observations=(record,),
        )

    def _allocate(
        self,
        context: ActivityRealizationContext,
        operation: AllocatePublicIngress,
    ) -> ActivityExecutionOutcome:
        existing_reservation: CloudflareOwnedHostnameReservation | None = None
        removed_resource: CloudflareOwnedIngressResource | None = None
        rebind_coordinates_valid = False
        try:
            graph = DEFAULT_GRAPH_CODEC.decode(context.desired_graph.graph_descriptor)
            ingress = _ingress_by_id(graph, operation.target.ingress_id)
            if ingress.lifecycle is PublicIngressLifecycle.EXTERNAL:
                return _external_noop(context, ingress, "allocate")
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
                if ingress.lifecycle is PublicIngressLifecycle.RETAINED:
                    try:
                        existing_reservation = (
                            unit_of_work.stores.ingress_reservations
                            .require_live_cloudflare_for_ingress(
                                context.request.identity.workspace_id,
                                ingress.ingress_id,
                            )
                        )
                    except IngressAuthorityNotFound:
                        pass
                    if existing_reservation is not None:
                        _require_rebindable_reservation(
                            existing_reservation,
                            ingress,
                            authority.authority,
                        )
                        removed_resource = (
                            unit_of_work.stores.ingress_resources
                            .require_latest_removed_cloudflare(
                                context.request.identity.workspace_id,
                                ingress.ingress_id,
                                existing_reservation.reservation_id,
                            )
                        )
                        _require_reservation_resource_agreement(
                            existing_reservation,
                            removed_resource,
                        )
        except (KeyError, ValueError, InvalidOperationCommand) as error:
            return _unsupported(context, "ingress.allocate-unsupported", str(error))

        recorded_at = context.intent_event.occurred_at
        try:
            grant = self._authorize_api_token(context, authority.authority)
            custody_grant = self._generated_secret_custody_grant(
                context,
                authority.authority,
            )
            recorded_at = self.clock()
            _validate_fold_timestamp(recorded_at)
            if existing_reservation is None:
                allocation = interpreter.create(
                    ingress,
                    authority=authority.authority,
                    allocation_name=_allocation_name(context, ingress),
                    origin_service_url=origin_service_url,
                    secret_resolution_grant=grant,
                    secret_custody_grant=custody_grant,
                )
            else:
                assert removed_resource is not None
                allocation = interpreter.rebind(
                    ingress,
                    authority=authority.authority,
                    reservation=_reservation_coordinates(
                        existing_reservation,
                        removed_resource,
                    ),
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
            if existing_reservation is not None:
                uncertainty_recorded = self._mark_reservation_uncertain(
                    context,
                    existing_reservation,
                    transitioned_at=recorded_at,
                )
                return _retained_uncertain(
                    "ingress.rebind-uncertain",
                    "retained ingress rebind result is uncertain",
                    existing_reservation,
                    exception_type=type(error).__name__,
                    uncertainty_recorded=uncertainty_recorded,
                )
            return _uncertain("ingress.allocate-uncertain", type(error).__name__)

        try:
            reservation_to_record = None
            reservation_id = (
                None
                if existing_reservation is None
                else existing_reservation.reservation_id
            )
            if (
                ingress.lifecycle is PublicIngressLifecycle.RETAINED
                and existing_reservation is None
            ):
                reservation_id = _reservation_id(context, ingress)
                reservation_to_record = CloudflareOwnedHostnameReservation(
                    reservation_id=reservation_id,
                    workspace_id=context.request.identity.workspace_id,
                    ingress_id=ingress.ingress_id,
                    authority_ref=ingress.authority_ref,
                    provider_kind=authority.provider_kind,
                    dns_record_id=allocation.dns_record_id,
                    hostname=allocation.hostname,
                    zone_id=authority.authority.zone_id,
                    lifecycle=ingress.lifecycle,
                    status=OwnedHostnameReservationStatus.BOUND,
                    created_at=recorded_at,
                    observed_at=recorded_at,
                    source_run_id=context.run.run_id,
                    source_activity_id=context.activity.activity_id.value,
                    source_event_id=context.intent_event.event_id,
                )
            if existing_reservation is not None:
                assert removed_resource is not None
                _require_rebind_allocation(
                    allocation,
                    existing_reservation,
                    removed_resource,
                )
                rebind_coordinates_valid = True
            resource = CloudflareOwnedIngressResource(
                workspace_id=context.request.identity.workspace_id,
                runtime_id=graph.node(ingress.connector_node_id).runtime_id,
                ingress_id=ingress.ingress_id,
                reservation_id=reservation_id,
                authority_ref=ingress.authority_ref,
                provider_kind=authority.provider_kind,
                tunnel_name=allocation.tunnel_name,
                tunnel_id=allocation.tunnel_id,
                dns_record_id=allocation.dns_record_id,
                hostname=allocation.hostname,
                zone_id=authority.authority.zone_id,
                lifecycle=ingress.lifecycle,
                created_at=recorded_at,
                observed_at=recorded_at,
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
                admitted_at=recorded_at,
            )
            secret_evidence = record_generated_ingress_secret(
                workspace_id=context.request.identity.workspace_id,
                purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                receipt=receipt,
                reference_registration_id=reference_candidate.registration_id,
                source_run_id=context.run.run_id,
                source_activity_id=context.activity.activity_id.value,
                source_event_id=context.intent_event.event_id,
                recorded_at=recorded_at,
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
                if reservation_to_record is not None:
                    unit_of_work.stores.ingress_reservations.record_cloudflare(
                        reservation_to_record
                    )
                unit_of_work.stores.ingress_resources.record_cloudflare(resource)
                unit_of_work.stores.generated_ingress_secrets.record(secret_evidence)
                if existing_reservation is not None:
                    unit_of_work.stores.ingress_reservations.mark_bound(
                        context.request.identity.workspace_id,
                        existing_reservation.reservation_id,
                        expected_version=existing_reservation.version,
                        transitioned_at=recorded_at,
                        source_run_id=context.run.run_id,
                        source_activity_id=context.activity.activity_id.value,
                        source_event_id=context.intent_event.event_id,
                    )
                unit_of_work.commit()
        except Exception as error:  # noqa: BLE001 - compensate exact provider effect.
            if existing_reservation is not None:
                uncertainty_recorded = self._mark_reservation_uncertain(
                    context,
                    existing_reservation,
                    transitioned_at=recorded_at,
                )
                return _retained_uncertain(
                    "ingress.rebind-fold-uncertain",
                    "retained ingress rebind could not be folded durably",
                    existing_reservation,
                    exception_type=type(error).__name__,
                    uncertainty_recorded=uncertainty_recorded,
                    tunnel_id=(
                        allocation.tunnel_id
                        if rebind_coordinates_valid
                        else None
                    ),
                )
            try:
                interpreter.teardown(
                    authority=authority.authority,
                    resources=allocation,
                    secret_resolution_grant=grant,
                    secret_custody_grant=custody_grant,
                )
            except Exception as compensation_error:  # noqa: BLE001
                return _compensation_uncertain(
                    allocation,
                    fold_exception_type=type(error).__name__,
                    compensation_exception_type=type(compensation_error).__name__,
                )
            return _failed_after_compensation(type(error).__name__)

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
                    "reservation_id": resource.reservation_id,
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
        reservation: CloudflareOwnedHostnameReservation | None = None
        try:
            graph = DEFAULT_GRAPH_CODEC.decode(context.base_graph.graph_descriptor)
            ingress = _ingress_by_id(graph, operation.target.ingress_id)
            if ingress.lifecycle is PublicIngressLifecycle.EXTERNAL:
                return _external_noop(context, ingress, "remove")
            authority = self._active_authority(context, ingress)
            with self.unit_of_work_factory() as unit_of_work:
                resource = unit_of_work.stores.ingress_resources.require_active_cloudflare(
                    context.request.identity.workspace_id,
                    ingress.ingress_id,
                )
                if ingress.lifecycle is PublicIngressLifecycle.RETAINED:
                    if resource.reservation_id is None:
                        raise InvalidOperationCommand(
                            "retained realization is missing reservation identity"
                        )
                    reservation = (
                        unit_of_work.stores.ingress_reservations.require_cloudflare(
                            context.request.identity.workspace_id,
                            resource.reservation_id,
                        )
                    )
                    _require_bound_reservation(
                        reservation,
                        ingress,
                        authority.authority,
                    )
                    _require_reservation_resource_agreement(
                        reservation,
                        resource,
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
            transitioned_at = self.clock()
            _validate_fold_timestamp(transitioned_at)
        except SecretProviderRegistrationError:
            return _unsupported(
                context,
                "secret.use-not-authorized",
                "ingress authority secret use was not authorized",
            )
        except (KeyError, ValueError, InvalidOperationCommand) as error:
            return _unsupported(context, "ingress.remove-unsupported", str(error))

        with self.unit_of_work_factory() as unit_of_work:
            resource = unit_of_work.stores.ingress_resources.mark_removing(
                context.request.identity.workspace_id,
                ingress.ingress_id,
                expected_epoch=resource.epoch,
            )
            unit_of_work.commit()
        try:
            if reservation is None:
                interpreter.teardown(
                    authority=authority.authority,
                    resources=resource,
                    secret_resolution_grant=grant,
                    secret_custody_grant=custody_grant,
                )
            else:
                result = interpreter.deactivate_preserving_reservation(
                    authority=authority.authority,
                    reservation=_reservation_coordinates(
                        reservation,
                        resource,
                    ),
                    resources=resource,
                    secret_resolution_grant=grant,
                    secret_custody_grant=custody_grant,
                )
                _require_retained_deactivation(result, reservation, resource)
        except Exception as error:  # noqa: BLE001 - provider failures are bounded.
            if reservation is not None:
                uncertainty_recorded = self._mark_retained_off_uncertain(
                    context,
                    reservation,
                    transitioned_at=transitioned_at,
                    expected_epoch=resource.epoch,
                )
                return _retained_uncertain(
                    "ingress.deactivate-uncertain",
                    "retained ingress deactivation result is uncertain",
                    reservation,
                    exception_type=type(error).__name__,
                    uncertainty_recorded=uncertainty_recorded,
                    tunnel_id=resource.tunnel_id,
                )
            with self.unit_of_work_factory() as unit_of_work:
                unit_of_work.stores.ingress_resources.mark_uncertain(
                    context.request.identity.workspace_id,
                    ingress.ingress_id,
                    expected_epoch=resource.epoch,
                )
                unit_of_work.commit()
            return _uncertain("ingress.remove-uncertain", type(error).__name__)
        try:
            with self.unit_of_work_factory() as unit_of_work:
                unit_of_work.stores.ingress_resources.mark_removed(
                    context.request.identity.workspace_id,
                    ingress.ingress_id,
                    removed_at=transitioned_at,
                    removed_by_run_id=context.run.run_id,
                    expected_epoch=resource.epoch,
                )
                if reservation is not None:
                    unit_of_work.stores.ingress_reservations.mark_reserved(
                        context.request.identity.workspace_id,
                        reservation.reservation_id,
                        expected_version=reservation.version,
                        transitioned_at=transitioned_at,
                        source_run_id=context.run.run_id,
                        source_activity_id=context.activity.activity_id.value,
                        source_event_id=context.intent_event.event_id,
                    )
                unit_of_work.commit()
        except Exception as error:  # noqa: BLE001 - provider effect already occurred.
            if reservation is None:
                raise
            uncertainty_recorded = self._mark_retained_off_uncertain(
                context,
                reservation,
                transitioned_at=transitioned_at,
                expected_epoch=resource.epoch,
            )
            return _retained_uncertain(
                "ingress.deactivate-fold-uncertain",
                "retained ingress deactivation could not be folded durably",
                reservation,
                exception_type=type(error).__name__,
                uncertainty_recorded=uncertainty_recorded,
                tunnel_id=resource.tunnel_id,
            )
        if reservation is not None:
            return ActivityExecutionOutcome.succeeded(
                BoundedEvidence.from_mapping(
                    {
                        "provider_kind": authority.provider_kind.value,
                        "ingress_id": ingress.ingress_id,
                        "reservation_id": reservation.reservation_id,
                        "dns_record_id": reservation.dns_record_id,
                        "hostname": reservation.hostname,
                        "removed_tunnel_id": resource.tunnel_id,
                        "reservation_status": (
                            OwnedHostnameReservationStatus.RESERVED.value
                        ),
                    }
                )
            )
        return ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping(plan.descriptor())
        )

    def _release(
        self,
        context: ActivityRealizationContext,
        operation: ReleasePublicIngressReservation,
    ) -> ActivityExecutionOutcome:
        target = operation.target
        workspace_id = context.request.identity.workspace_id
        try:
            _require_release_worker(context)
            graph = DEFAULT_GRAPH_CODEC.decode(context.desired_graph.graph_descriptor)
            with self.unit_of_work_factory() as unit_of_work:
                reservation = (
                    unit_of_work.stores.ingress_reservations.require_cloudflare(
                        workspace_id,
                        target.reservation_id,
                    )
                )
                _require_exact_releasable_reservation(reservation, target)
                _require_reservation_absent_from_graph(graph, reservation)
                try:
                    unit_of_work.stores.ingress_resources.get_cloudflare(
                        workspace_id,
                        target.ingress_id,
                    )
                except IngressAuthorityNotFound:
                    pass
                else:
                    raise InvalidOperationCommand(
                        "hostname reservation cannot be released while a realization "
                        "remains"
                    )
                removed_resource = (
                    unit_of_work.stores.ingress_resources
                    .require_latest_removed_cloudflare(
                        workspace_id,
                        target.ingress_id,
                        target.reservation_id,
                    )
                )
                _require_reservation_resource_agreement(
                    reservation,
                    removed_resource,
                )
                authority = (
                    unit_of_work.stores.ingress_authorities
                    .require_active_for_hostname(
                        workspace_id,
                        reservation.authority_ref,
                        reservation.hostname,
                    )
                )
                _require_reservation_authority_agreement(reservation, authority)
            interpreter = self._interpreter(authority.provider_kind)
            grant = self._authorize_api_token(context, authority.authority)
            transitioned_at = self.clock()
            _validate_fold_timestamp(transitioned_at)
        except SecretProviderRegistrationError:
            return _unsupported(
                context,
                "secret.use-not-authorized",
                "ingress authority secret use was not authorized",
            )
        except (KeyError, ValueError, InvalidOperationCommand) as error:
            return _unsupported(context, "ingress.release-unsupported", str(error))

        try:
            with self.unit_of_work_factory() as unit_of_work:
                current = (
                    unit_of_work.stores.ingress_reservations
                    .require_cloudflare_for_update(
                        workspace_id,
                        target.reservation_id,
                    )
                )
                _require_exact_releasable_reservation(current, target)
                try:
                    unit_of_work.stores.ingress_resources.get_cloudflare(
                        workspace_id,
                        target.ingress_id,
                    )
                except IngressAuthorityNotFound:
                    pass
                else:
                    raise InvalidOperationCommand(
                        "hostname reservation cannot be released while a realization "
                        "remains"
                    )
                removed_resource = (
                    unit_of_work.stores.ingress_resources
                    .require_latest_removed_cloudflare(
                        workspace_id,
                        target.ingress_id,
                        target.reservation_id,
                    )
                )
                _require_reservation_resource_agreement(current, removed_resource)
                releasing = unit_of_work.stores.ingress_reservations.mark_releasing(
                    workspace_id,
                    target.reservation_id,
                    expected_version=target.reservation_version,
                    transitioned_at=transitioned_at,
                    source_run_id=context.run.run_id,
                    source_activity_id=context.activity.activity_id.value,
                    source_event_id=context.intent_event.event_id,
                )
                unit_of_work.commit()
        except (KeyError, ValueError, InvalidOperationCommand) as error:
            return _unsupported(context, "ingress.release-unsupported", str(error))

        try:
            result = interpreter.release_reservation(
                authority=authority.authority,
                reservation=_reservation_coordinates(reservation, removed_resource),
                secret_resolution_grant=grant,
            )
            _require_released_reservation_observation(result, reservation)
        except Exception as error:  # noqa: BLE001 - provider failures are bounded.
            uncertainty_recorded = self._mark_reservation_uncertain(
                context,
                releasing,
                transitioned_at=transitioned_at,
            )
            return _retained_uncertain(
                "ingress.release-uncertain",
                "retained ingress reservation release result is uncertain",
                releasing,
                exception_type=type(error).__name__,
                uncertainty_recorded=uncertainty_recorded,
            )

        try:
            with self.unit_of_work_factory() as unit_of_work:
                released = unit_of_work.stores.ingress_reservations.mark_released(
                    workspace_id,
                    target.reservation_id,
                    expected_version=releasing.version,
                    transitioned_at=transitioned_at,
                    source_run_id=context.run.run_id,
                    source_activity_id=context.activity.activity_id.value,
                    source_event_id=context.intent_event.event_id,
                    released_by_run_id=context.run.run_id,
                )
                unit_of_work.commit()
        except Exception as error:  # noqa: BLE001 - provider effect already occurred.
            uncertainty_recorded = self._mark_reservation_uncertain(
                context,
                releasing,
                transitioned_at=transitioned_at,
            )
            return _retained_uncertain(
                "ingress.release-fold-uncertain",
                "retained ingress reservation release could not be folded durably",
                releasing,
                exception_type=type(error).__name__,
                uncertainty_recorded=uncertainty_recorded,
            )

        return ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping(
                {
                    "provider_kind": authority.provider_kind.value,
                    "ingress_id": released.ingress_id,
                    "reservation_id": released.reservation_id,
                    "dns_record_id": released.dns_record_id,
                    "hostname": released.hostname,
                    "reservation_status": released.status.value,
                }
            )
        )

    def _mark_reservation_uncertain(
        self,
        context: ActivityRealizationContext,
        reservation: CloudflareOwnedHostnameReservation,
        *,
        transitioned_at: str,
    ) -> bool:
        try:
            with self.unit_of_work_factory() as unit_of_work:
                current = (
                    unit_of_work.stores.ingress_reservations
                    .require_cloudflare_for_update(
                        context.request.identity.workspace_id,
                        reservation.reservation_id,
                    )
                )
                if current.status is not OwnedHostnameReservationStatus.UNCERTAIN:
                    if current.version != reservation.version:
                        return False
                    unit_of_work.stores.ingress_reservations.mark_uncertain(
                        context.request.identity.workspace_id,
                        reservation.reservation_id,
                        expected_version=reservation.version,
                        transitioned_at=transitioned_at,
                        source_run_id=context.run.run_id,
                        source_activity_id=context.activity.activity_id.value,
                        source_event_id=context.intent_event.event_id,
                    )
                unit_of_work.commit()
        except Exception:  # noqa: BLE001 - activity evidence retains uncertainty.
            return False
        return True

    def _mark_retained_off_uncertain(
        self,
        context: ActivityRealizationContext,
        reservation: CloudflareOwnedHostnameReservation,
        *,
        transitioned_at: str,
        expected_epoch: int,
    ) -> bool:
        try:
            with self.unit_of_work_factory() as unit_of_work:
                unit_of_work.stores.ingress_resources.mark_uncertain(
                    context.request.identity.workspace_id,
                    reservation.ingress_id,
                    expected_epoch=expected_epoch,
                )
                current = (
                    unit_of_work.stores.ingress_reservations
                    .require_cloudflare_for_update(
                        context.request.identity.workspace_id,
                        reservation.reservation_id,
                    )
                )
                if current.status is not OwnedHostnameReservationStatus.UNCERTAIN:
                    if current.version != reservation.version:
                        return False
                    unit_of_work.stores.ingress_reservations.mark_uncertain(
                        context.request.identity.workspace_id,
                        reservation.reservation_id,
                        expected_version=reservation.version,
                        transitioned_at=transitioned_at,
                        source_run_id=context.run.run_id,
                        source_activity_id=context.activity.activity_id.value,
                        source_event_id=context.intent_event.event_id,
                    )
                unit_of_work.commit()
        except Exception:  # noqa: BLE001 - activity evidence retains uncertainty.
            return False
        return True

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


def _require_rebindable_reservation(
    reservation: CloudflareOwnedHostnameReservation,
    ingress: NamedPublicIngress,
    authority: CloudflareZoneIngressAuthority,
) -> None:
    if reservation.status is not OwnedHostnameReservationStatus.RESERVED:
        raise InvalidOperationCommand(
            "retained ingress reservation is not reserved for rebind"
        )
    _require_reservation_ingress_authority(reservation, ingress, authority)


def _require_bound_reservation(
    reservation: CloudflareOwnedHostnameReservation,
    ingress: NamedPublicIngress,
    authority: CloudflareZoneIngressAuthority,
) -> None:
    if reservation.status is not OwnedHostnameReservationStatus.BOUND:
        raise InvalidOperationCommand(
            "retained ingress reservation is not bound for deactivation"
        )
    _require_reservation_ingress_authority(reservation, ingress, authority)


def _require_exact_releasable_reservation(
    reservation: CloudflareOwnedHostnameReservation,
    target: PublicIngressReservationTarget,
) -> None:
    if (
        reservation.ingress_id != target.ingress_id
        or reservation.reservation_id != target.reservation_id
        or reservation.version != target.reservation_version
        or reservation.status is not OwnedHostnameReservationStatus.RESERVED
        or reservation.lifecycle is not PublicIngressLifecycle.RETAINED
    ):
        raise InvalidOperationCommand(
            "exact retained hostname reservation is not releasable"
        )


def _require_release_worker(context: ActivityRealizationContext) -> None:
    claim = context.request.claim
    if (
        PolicyScope.EXECUTION_OPERATE not in context.authority.scopes
        or claim is None
        or claim.worker_id != context.authority.worker_id
    ):
        raise InvalidOperationCommand(
            "reservation release requires the admitted execution worker"
        )


def _require_reservation_absent_from_graph(
    graph: DeploymentGraph,
    reservation: CloudflareOwnedHostnameReservation,
) -> None:
    if any(
        ingress.ingress_id == reservation.ingress_id
        or ingress.hostname == reservation.hostname
        for ingress in graph.public_ingresses
    ):
        raise InvalidOperationCommand(
            "hostname reservation remains visible in accepted graph truth"
        )


def _require_reservation_authority_agreement(
    reservation: CloudflareOwnedHostnameReservation,
    authority: RegisteredIngressAuthority,
) -> None:
    if (
        authority.workspace_id != reservation.workspace_id
        or authority.authority_ref != reservation.authority_ref
        or authority.provider_kind is not reservation.provider_kind
        or authority.authority.zone_id != reservation.zone_id
    ):
        raise InvalidOperationCommand(
            "retained hostname reservation disagrees with active authority"
        )


def _require_reservation_ingress_authority(
    reservation: CloudflareOwnedHostnameReservation,
    ingress: NamedPublicIngress,
    authority: CloudflareZoneIngressAuthority,
) -> None:
    if (
        reservation.ingress_id != ingress.ingress_id
        or reservation.authority_ref != ingress.authority_ref
        or reservation.hostname != ingress.hostname
        or reservation.zone_id != authority.zone_id
        or reservation.lifecycle is not PublicIngressLifecycle.RETAINED
        or ingress.lifecycle is not PublicIngressLifecycle.RETAINED
    ):
        raise InvalidOperationCommand(
            "retained ingress reservation disagrees with desired authority"
        )


def _require_reservation_resource_agreement(
    reservation: CloudflareOwnedHostnameReservation,
    resource: CloudflareOwnedIngressResource,
) -> None:
    if (
        resource.workspace_id != reservation.workspace_id
        or resource.ingress_id != reservation.ingress_id
        or resource.reservation_id != reservation.reservation_id
        or resource.authority_ref != reservation.authority_ref
        or resource.provider_kind is not reservation.provider_kind
        or resource.dns_record_id != reservation.dns_record_id
        or resource.hostname != reservation.hostname
        or resource.zone_id != reservation.zone_id
        or resource.lifecycle is not PublicIngressLifecycle.RETAINED
    ):
        raise InvalidOperationCommand(
            "retained reservation and realization coordinates disagree"
        )


def _reservation_coordinates(
    reservation: CloudflareOwnedHostnameReservation,
    resource: CloudflareOwnedIngressResource,
) -> IngressReservationCoordinates:
    _require_reservation_resource_agreement(reservation, resource)
    return IngressReservationCoordinates(
        dns_record_id=reservation.dns_record_id,
        hostname=reservation.hostname,
        expected_tunnel_id=resource.tunnel_id,
    )


def _require_rebind_allocation(
    allocation: IngressAllocationResult,
    reservation: CloudflareOwnedHostnameReservation,
    removed_resource: CloudflareOwnedIngressResource,
) -> None:
    if (
        allocation.dns_record_id != reservation.dns_record_id
        or allocation.hostname != reservation.hostname
        or allocation.tunnel_id == removed_resource.tunnel_id
    ):
        raise InvalidOperationCommand(
            "retained rebind result contradicts exact reservation truth"
        )


def _require_retained_deactivation(
    result: object,
    reservation: CloudflareOwnedHostnameReservation,
    resource: CloudflareOwnedIngressResource,
) -> None:
    if not isinstance(result, RetainedIngressDeactivationResult):
        raise InvalidOperationCommand(
            "retained deactivation result must be operations-owned evidence"
        )
    if (
        result.reservation.dns_record_id != reservation.dns_record_id
        or result.reservation.hostname != reservation.hostname
        or result.reservation.presence is not IngressResourcePresence.PRESENT
        or result.reservation.tunnel_id != resource.tunnel_id
        or result.tunnel.tunnel_id != resource.tunnel_id
        or result.tunnel.presence is not IngressResourcePresence.ABSENT
    ):
        raise InvalidOperationCommand(
            "retained deactivation result contradicts exact durable truth"
        )


def _require_released_reservation_observation(
    result: object,
    reservation: CloudflareOwnedHostnameReservation,
) -> None:
    if not isinstance(result, IngressReservationObservation):
        raise InvalidOperationCommand(
            "reservation release result must be operations-owned evidence"
        )
    if (
        result.dns_record_id != reservation.dns_record_id
        or result.hostname != reservation.hostname
        or result.presence is not IngressResourcePresence.ABSENT
        or result.tunnel_id is not None
    ):
        raise InvalidOperationCommand(
            "reservation release result contradicts exact durable truth"
        )


def _ingress_by_id(graph: DeploymentGraph, ingress_id: str) -> NamedPublicIngress:
    for ingress in graph.public_ingresses:
        if ingress.ingress_id == ingress_id:
            return ingress
    raise InvalidOperationCommand("public ingress is missing from pinned graph")


def _public_ingress_http_check(
    graph: DeploymentGraph,
    ingress: NamedPublicIngress,
) -> HttpCheck:
    node = graph.node(ingress.target.node_id)
    provider = node.sockets.provider(ingress.target.provider_socket)
    if provider.protocol is not SocketProtocol.HTTP:
        raise InvalidOperationCommand(
            "public ingress readiness target must provide HTTP"
        )
    checks = tuple(
        check
        for check in node.block_spec.verification.checks
        if check.check_id == ingress.readiness_check_id
    )
    if len(checks) != 1:
        raise InvalidOperationCommand(
            "public ingress readiness check is missing from the pinned graph"
        )
    check = checks[0]
    if not isinstance(check, HttpCheck):
        raise InvalidOperationCommand(
            "public ingress readiness check must be HTTP"
        )
    if check.provider_socket != ingress.target.provider_socket:
        raise InvalidOperationCommand(
            "public ingress readiness check must use the exposed provider socket"
        )
    return check


def _require_matching_readiness_observation(
    observation: object,
    ingress: NamedPublicIngress,
) -> None:
    if not isinstance(observation, PublicIngressObservation):
        raise InvalidOperationCommand(
            "public ingress readiness verifier returned untyped evidence"
        )
    if (
        observation.ingress_id != ingress.ingress_id
        or observation.hostname != ingress.hostname
        or observation.url != f"https://{ingress.hostname}"
        or observation.target != ingress.target
    ):
        raise InvalidOperationCommand(
            "public ingress readiness evidence does not match pinned graph"
        )


def _readiness_observation_record(
    context: ActivityRealizationContext,
    observation: PublicIngressObservation,
) -> ObservationRecord:
    status_by_ingress_status = {
        PublicIngressObservationStatus.READY: ObservationStatus.VERIFIED,
        PublicIngressObservationStatus.UNREADY: ObservationStatus.VERIFICATION_FAILED,
        PublicIngressObservationStatus.UNKNOWN: ObservationStatus.UNKNOWN,
    }
    outcome_by_ingress_status = {
        PublicIngressObservationStatus.READY: ProbeOutcome.READY,
        PublicIngressObservationStatus.UNREADY: ProbeOutcome.NOT_READY,
        PublicIngressObservationStatus.UNKNOWN: ProbeOutcome.UNKNOWN,
    }
    return ObservationRecord(
        observation_id=(
            f"{context.intent_event.event_id}:public-ingress-readiness"
        ),
        workspace_id=context.request.identity.workspace_id,
        subject_id=observation.ingress_id,
        status=status_by_ingress_status[observation.status],
        observed_at=observation.observed_at,
        evidence=BoundedEvidence.from_mapping(
            {"public_ingress_observation": observation.descriptor()}
        ),
        graph_id=context.desired_graph.source_authored_graph_id,
        probe_kind=ProbeKind.READINESS,
        probe_outcome=outcome_by_ingress_status[observation.status],
    )


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


def _reservation_id(
    context: ActivityRealizationContext,
    ingress: NamedPublicIngress,
) -> str:
    digest = hashlib.sha256(
        "|".join(
            (
                context.request.identity.workspace_id,
                ingress.ingress_id,
                context.intent_event.event_id,
            )
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"reservation-{digest}"


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


def _convergence_progress(
    ingress: NamedPublicIngress,
    attempted_at: datetime,
    deadline: datetime,
    *,
    details: Mapping[str, object],
    observations: tuple[ObservationRecord, ...] = (),
) -> ActivityExecutionOutcome:
    next_attempt = min(
        attempted_at
        + timedelta(seconds=ingress.convergence.retry_interval_seconds),
        deadline,
    )
    return ActivityExecutionOutcome.progress(
        progress_kind="public-ingress-convergence",
        next_attempt_not_before=_utc_text(next_attempt),
        deadline=_utc_text(deadline),
        evidence=BoundedEvidence.from_mapping(
            {
                "ingress_id": ingress.ingress_id,
                **details,
            }
        ),
        observations=observations,
    )


def _convergence_timeout(
    context: ActivityRealizationContext,
    ingress: NamedPublicIngress,
    deadline: datetime,
    *,
    details: Mapping[str, object] | None = None,
    observations: tuple[ObservationRecord, ...] = (),
) -> ActivityExecutionOutcome:
    return ActivityExecutionOutcome.failed(
        FailureEvidence(
            FailureCategory.TERMINAL,
            "ingress.convergence-timeout",
            "public ingress did not become ready within its convergence window",
            BoundedEvidence.from_mapping(
                {
                    "activity_id": context.activity.activity_id.value,
                    "ingress_id": ingress.ingress_id,
                    "deadline": _utc_text(deadline),
                    **dict(details or {}),
                }
            ),
        ),
        observations=observations,
    )


def _utc_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{field} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidOperationCommand(
            f"{field} must be an ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise InvalidOperationCommand(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _retained_uncertain(
    code: str,
    message: str,
    reservation: CloudflareOwnedHostnameReservation,
    *,
    exception_type: str,
    uncertainty_recorded: bool,
    tunnel_id: object | None = None,
) -> ActivityExecutionOutcome:
    details: dict[str, object] = {
        "reservation_id": reservation.reservation_id,
        "ingress_id": reservation.ingress_id,
        "dns_record_id": reservation.dns_record_id,
        "hostname": reservation.hostname,
        "exception_type": exception_type,
        "uncertainty_recorded": uncertainty_recorded,
    }
    if isinstance(tunnel_id, str) and tunnel_id.strip():
        details["tunnel_id"] = tunnel_id
    return ActivityExecutionOutcome.uncertain(
        FailureEvidence(
            FailureCategory.UNCERTAIN,
            code,
            message,
            BoundedEvidence.from_mapping(details),
        )
    )


def _external_noop(
    context: ActivityRealizationContext,
    ingress: NamedPublicIngress,
    operation: str,
) -> ActivityExecutionOutcome:
    return ActivityExecutionOutcome.succeeded(
        BoundedEvidence.from_mapping(
            {
                "activity_id": context.activity.activity_id.value,
                "ingress_id": ingress.ingress_id,
                "lifecycle": PublicIngressLifecycle.EXTERNAL.value,
                "owned_effect": False,
                "operation": operation,
            }
        )
    )


def _exact_coordinate(value: object, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise InvalidOperationCommand(f"{field} must be an exact provider identifier")


def _exact_hostname(value: object) -> None:
    if (
        not isinstance(value, str)
        or value != value.strip().lower()
        or "." not in value
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise InvalidOperationCommand("hostname must be an exact lowercase DNS name")


def _validate_fold_timestamp(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(ord(character) < 32 for character in value)
    ):
        raise InvalidOperationCommand(
            "ingress allocation fold timestamp must be nonempty and bounded"
        )


def _failed_after_compensation(exception_type: str) -> ActivityExecutionOutcome:
    return ActivityExecutionOutcome.failed(
        FailureEvidence(
            FailureCategory.RETRYABLE,
            "ingress.record-failed-compensated",
            "ingress allocation was compensated after durable recording failed",
            BoundedEvidence.from_mapping({"exception_type": exception_type}),
        )
    )


def _compensation_uncertain(
    allocation: IngressOwnedResourceCoordinates,
    *,
    fold_exception_type: str,
    compensation_exception_type: str,
) -> ActivityExecutionOutcome:
    return ActivityExecutionOutcome.uncertain(
        FailureEvidence(
            FailureCategory.UNCERTAIN,
            "ingress.compensation-uncertain",
            "ingress allocation compensation is uncertain",
            BoundedEvidence.from_mapping(
                {
                    "fold_exception_type": fold_exception_type,
                    "compensation_exception_type": compensation_exception_type,
                    "tunnel_id": allocation.tunnel_id,
                    "tunnel_name": allocation.tunnel_name,
                    "dns_record_id": allocation.dns_record_id,
                    "hostname": allocation.hostname,
                }
            ),
        )
    )
