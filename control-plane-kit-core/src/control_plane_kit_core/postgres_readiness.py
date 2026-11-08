"""Original SQL/request congruence; never installation or execution authority.

Owners must load committed records and qualify the observer, bytes and physical
recipient independently. These supplied values do not attest those facts. The
existing V2 envelope is unchanged; no SQL result is inferred from generic health.
"""
from dataclasses import dataclass
from hashlib import sha256

from control_plane_kit_core._node_control_public_wire import canonical_json_bytes, reference_violation
from control_plane_kit_core.configuration_installation import ConfigurationInstallationDisposition
from control_plane_kit_core.node_control import NodeHealthReadKind
from control_plane_kit_core.node_health_read_results import NodeHealthReadOutcome
from control_plane_kit_core.operations import EffectAttemptIdentity
from control_plane_kit_core.planning import NodeTarget, StartNode, WaitForHealthy
from control_plane_kit_core.products import ProductReference, ProductReferenceCodec, OciImageReference, OciImageReferenceCodec
from control_plane_kit_core.receiver_configuration import (
    ReceiverNodeControlConfiguration, ReceiverNodeControlConfigurationCodec,
    select_receiver_node_control_configuration_artifact,
)
from control_plane_kit_core.receiver_health_reads import ReceiverHealthReadRequest
from control_plane_kit_core.receiver_health_read_results import ReceiverHealthReadResult
from control_plane_kit_core.receiver_identity import NodeControlAuthorityContext
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_for_request, runtime_effect_intent_fingerprint
from control_plane_kit_core.runtime_effects import RuntimeEffectKind, RuntimeEffectRequest
from control_plane_kit_core.secret_installation import (
    SecretInstallationReceipt, secret_installation_correlation_for_request, secret_material_selection_fingerprint,
)
from control_plane_kit_core.secret_selections import SecretVersionSelection
from control_plane_kit_core.secrets import SecretFileDelivery, SecretFileMode, SecretFilePathBinding, SecretUseIntent, SecretValue
from control_plane_kit_core.types import Protocol, RuntimeKind
from control_plane_kit_core.verification import PostgresQueryCheck, PostgresVerificationOperation, verification_check_from_descriptor

_ERROR = "PostgreSQL readiness binding is unavailable"


class PostgresReadinessUnavailable(ValueError):
    """Fixed refusal without candidate or secret material."""


def _require(condition):
    if condition is not True:
        raise ValueError


def _checked(action):
    try:
        return action()
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, OverflowError, RecursionError):
        pass
    raise PostgresReadinessUnavailable(_ERROR)


@dataclass(frozen=True, slots=True, repr=False)
class PostgresReadinessObserver:
    """Expected qualified variant supplied by its owner, not qualification proof."""
    product: ProductReference
    image: OciImageReference
    provider_socket: str
    database: str
    username: str
    password_path: str
    password_environment_name: str

    def __post_init__(self):
        self.descriptor()

    def descriptor(self):
        def document():
            _require(type(self.product) is ProductReference and type(self.image) is OciImageReference)
            product = ProductReferenceCodec().encode(self.product)
            image = OciImageReferenceCodec().encode(self.image)
            for value in (self.provider_socket, self.database, self.username):
                _require(type(value) is str and reference_violation(value) is None)
            _require(type(self.password_path) is str and self.password_path.startswith("/"))
            SecretFilePathBinding(self.password_environment_name)
            return dict(product=product, image=image, provider_socket=self.provider_socket,
                database=self.database, username=self.username, password_path=self.password_path,
                password_environment_name=self.password_environment_name)
        return _checked(document)


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class PostgresReadinessBinding:
    """Supplied original values only; replay identity does not authorize a resend.

    started_at_epoch_ms must be the retained original attempt start. It is not
    a dispatch clock. There is no caller-selected deadline or random new nonce:
    the retained effect identity distinguishes requests and reproduces replay.
    """
    wait: RuntimeEffectRequest
    attempt: EffectAttemptIdentity
    installation: RuntimeEffectRequest
    receipt: SecretInstallationReceipt
    observer: PostgresReadinessObserver
    receiver: ReceiverNodeControlConfiguration
    base_projection_id: str
    desired_projection_id: str
    check_id: str
    started_at_epoch_ms: int

    def __post_init__(self):
        self.descriptor()

    def descriptor(self):
        return _checked(lambda: _document(self))

    @property
    def deadline_epoch_ms(self):
        return self.descriptor()["deadline_epoch_ms"]

    def request(self):
        document = self.descriptor()
        identity = "sql-" + sha256(canonical_json_bytes(document)).hexdigest()
        return ReceiverHealthReadRequest(self.receiver.target,
            NodeControlAuthorityContext(self.wait.source.desired_graph_id, self.desired_projection_id),
            NodeHealthReadKind.READINESS, self.receiver.declaration.identity(), identity)


def _document(value):
    wait, install = value.wait, value.installation
    _require(type(wait) is type(install) is RuntimeEffectRequest
        and wait.secret_resolution_grants == install.secret_resolution_grants == ())
    wait_intent, install_intent = runtime_effect_intent_for_request(wait), runtime_effect_intent_for_request(install)
    _require(wait.kind is RuntimeEffectKind.REALIZE_ACTIVITY
        and install.kind in (RuntimeEffectKind.REALIZE_ACTIVITY, RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1)
        and wait.runtime_kind is install.runtime_kind is RuntimeKind.DOCKER
        and type(wait.operation) is WaitForHealthy and type(install.operation) is StartNode
        and type(wait.operation.target) is type(install.operation.target) is NodeTarget
        and wait.operation.target == install.operation.target
        and len(wait.products) == len(install.products) == 1
        and wait.products == install.products and wait.authority_ref == install.authority_ref)
    for name in ("workspace_id", "request_id", "run_id", "plan_id", "base_graph_id", "desired_graph_id"):
        _require(getattr(wait.source, name) == getattr(install.source, name))
    _require(type(value.attempt) is EffectAttemptIdentity)
    EffectAttemptIdentity.__post_init__(value.attempt)
    _require(value.attempt.run_id == wait.source.run_id and value.attempt.activity_id == wait.activity_id.value)
    material = wait.products[0]
    contract = material.product.runtime_contract
    _require(material.node_id == wait.operation.target.node_id)
    _require(type(value.observer) is PostgresReadinessObserver)
    observer = value.observer.descriptor()
    _require(material.reference == value.observer.product and material.product.image == value.observer.image)
    checks = tuple(check for check in contract.verification.checks if check.check_id == value.check_id)
    _require(len(checks) == 1 and type(checks[0]) is PostgresQueryCheck)
    check, = checks
    _require(verification_check_from_descriptor(check.descriptor()) == check
        and check.operation is PostgresVerificationOperation.SELECT_ONE and check.authentication is not None
        and check.provider_socket == value.observer.provider_socket
        and check.authentication.database == value.observer.database
        and check.authentication.username == value.observer.username
        and check.policy.maximum_attempts == 1 and 0 < check.policy.timeout_seconds <= 5)
    _require(contract.sockets.provider(check.provider_socket).protocol is Protocol.POSTGRES
        and any(port.provider_socket == check.provider_socket and port.container_port == 5432
            for port in contract.provider_ports))
    deliveries = tuple(delivery for delivery in contract.secret_deliveries
        if type(delivery) is SecretFileDelivery and delivery.reference == check.authentication.password_reference
        and delivery.intent is SecretUseIntent.POSTGRES_PASSWORD
        and delivery.target_path == value.observer.password_path)
    selections = tuple(selection for selection in material.secret_selections
        if selection.reference == check.authentication.password_reference and selection.intent is SecretUseIntent.POSTGRES_PASSWORD)
    _require(len(deliveries) == len(selections) == 1 and type(selections[0]) is SecretVersionSelection)
    _require(deliveries[0].file_mode is SecretFileMode.OWNER_READ_ONLY
        and type(deliveries[0].path_binding) is SecretFilePathBinding
        and deliveries[0].path_binding.environment_name == value.observer.password_environment_name)
    actual_environment = material.public_environment + material.socket_environment
    _require(not any(item.name == value.observer.password_environment_name for item in actual_environment))
    context = secret_installation_correlation_for_request(install)
    _require(type(value.receipt) is SecretInstallationReceipt)
    SecretInstallationReceipt.__post_init__(value.receipt)
    _require(value.receipt.disposition is ConfigurationInstallationDisposition.CREATED
        and value.receipt.request_fingerprint == context.request_fingerprint
        and value.receipt.selection_fingerprint == secret_material_selection_fingerprint(install))
    _require(type(value.receiver) is ReceiverNodeControlConfiguration)
    ReceiverNodeControlConfigurationCodec().encode(value.receiver)
    declared = select_receiver_node_control_configuration_artifact(artifacts=contract.configuration_artifacts,
        environment=contract.public_environment, control_surfaces=contract.control_surfaces)
    artifact = select_receiver_node_control_configuration_artifact(artifacts=contract.configuration_artifacts,
        environment=actual_environment, control_surfaces=contract.control_surfaces)
    _require(artifact == declared)
    _require(ReceiverNodeControlConfigurationCodec().decode_bytes(artifact.content.encode()) == value.receiver)
    target = value.receiver.target
    _require((target.workspace_id.value, target.runtime_id.value, target.node_id.value)
        == (wait.source.workspace_id, material.runtime_id, material.node_id)
        and NodeHealthReadKind.READINESS in value.receiver.declaration.surface.health_reads)
    for projection in (value.base_projection_id, value.desired_projection_id):
        _require(type(projection) is str and reference_violation(projection) is None)
    _require(type(value.started_at_epoch_ms) is int and 0 <= value.started_at_epoch_ms <= 9007199254735000)
    deadline = value.started_at_epoch_ms + int(check.policy.timeout_seconds * 1000)
    _require(deadline > value.started_at_epoch_ms)
    return dict(domain="cpk.original-postgres-readiness.v1", check=check.descriptor(), observer=observer,
        receiver=value.receiver.target.descriptor(), declaration_identity=value.receiver.declaration.identity().value,
        source=wait.source.descriptor(), base_projection_id=value.base_projection_id,
        desired_projection_id=value.desired_projection_id, attempt=value.attempt.descriptor(),
        effect_id=wait.effect_id, request_fingerprint=runtime_effect_intent_fingerprint(wait_intent),
        installation_effect_id=install.effect_id, installation_activity_id=install.activity_id.value,
        installation_request_fingerprint=runtime_effect_intent_fingerprint(install_intent),
        installation_receipt=value.receipt.descriptor(), credential_selection=selections[0].descriptor(),
        credential_delivery=deliveries[0].descriptor(), started_at_epoch_ms=value.started_at_epoch_ms,
        deadline_epoch_ms=deadline)


def postgres_readiness_password_supported(value):
    """Pure narrow-domain test; only an authorized owner can supply the version.

    Do not retain, hash or report the bytes. Matching public references never
    establishes that this input was installed on the addressed realization.
    """
    if type(value) is not SecretValue:
        return False
    try:
        raw = value.reveal().encode("utf-8")
    except UnicodeError:
        return False
    return 1 <= len(raw) <= 4096 and all(32 <= byte <= 126 for byte in raw)


def postgres_readiness_result_matches(binding, result, *, observed_at_epoch_ms):
    """Congruence only, after the owner verifies transport, bytes and recipient."""
    def matches():
        _require(type(binding) is PostgresReadinessBinding and type(result) is ReceiverHealthReadResult)
        document = binding.descriptor()
        ReceiverHealthReadResult.__post_init__(result)
        return (type(observed_at_epoch_ms) is int
            and document["started_at_epoch_ms"] <= observed_at_epoch_ms < document["deadline_epoch_ms"]
            and result.request == binding.request() and result.declaration == binding.receiver.declaration
            and result.outcome is NodeHealthReadOutcome.HEALTHY
            and len(result.canonical_bytes()) <= document["check"]["policy"]["maximum_evidence_bytes"])
    return _checked(matches)


__all__ = ["PostgresReadinessUnavailable", "PostgresReadinessObserver", "PostgresReadinessBinding",
    "postgres_readiness_password_supported", "postgres_readiness_result_matches"]
