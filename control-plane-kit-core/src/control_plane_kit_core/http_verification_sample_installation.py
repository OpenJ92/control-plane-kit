"""Opt-in installed HTTP sample trust; no current authority, file I/O or probes."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import ClassVar

from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.configuration import (
    ConfigurationArtifact, ConfigurationArtifactRequirement, ConfigurationFileMode, ConfigurationMediaType,
)
from control_plane_kit_core.delegation_keys import DelegationKeyAlgorithm, DelegationKeyPurpose, DelegationPublicKey
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding, SocketDerivedEnvironmentBinding
from control_plane_kit_core.http_verification_samples import (
    HttpVerificationSampleRequest, _bounded, _checked, _closed, _raw, _require, _text,
    http_verification_check_digest, require_http_verification_sample_check,
)
from control_plane_kit_core._node_control_public_wire import canonical_json_bytes, digest_violation
from control_plane_kit_core.products import ProductRuntimeContract, ProviderRuntimePort
from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfiguration, ReceiverNodeControlConfigurationCodec
from control_plane_kit_core.receiver_identity import NodeControlReceiverTarget, NodeControlReceiverTargetCodec, receiver_node_control_audience
from control_plane_kit_core.types import Protocol
from control_plane_kit_core.verification import HttpCheck, VerificationContract

MAX_HTTP_VERIFICATION_SAMPLE_CONFIGURATION_BYTES = 65536
HTTP_VERIFICATION_SAMPLES_CONFIGURATION_ENVIRONMENT = "CPK_HTTP_VERIFICATION_SAMPLES_CONFIGURATION_FILE"


@dataclass(frozen=True, slots=True, repr=False)
class HttpVerificationSampleVerifierConfiguration:
    purpose: DelegationKeyPurpose
    issuer: str
    public_keys: tuple[DelegationPublicKey, ...]

    def __post_init__(self):
        _checked(lambda: _validate_verifier(self))
        object.__setattr__(self, "public_keys", tuple(sorted(self.public_keys, key=lambda key: key.key_id)))


def _validate_verifier(verifier):
    _require(type(verifier) is HttpVerificationSampleVerifierConfiguration
             and verifier.purpose is DelegationKeyPurpose.WORKLOAD_HTTP_VERIFICATION_SAMPLE)
    _text(verifier.issuer)
    _require(type(verifier.public_keys) is tuple and 1 <= len(verifier.public_keys) <= 16)
    for key in verifier.public_keys:
        _require(type(key) is DelegationPublicKey and key.algorithm is DelegationKeyAlgorithm.ED25519)
        _require(replace(key) == key)
    _require(len({key.key_id for key in verifier.public_keys}) == len(verifier.public_keys)
             and len({key.fingerprint_sha256 for key in verifier.public_keys}) == len(verifier.public_keys))


@dataclass(frozen=True, order=True, slots=True)
class HttpVerificationSampleCheckBinding:
    check_id: str
    check_digest: str

    def __post_init__(self):
        def validate():
            _text(self.check_id)
            _text(self.check_digest, digest_violation)
        _checked(validate)


@dataclass(frozen=True, slots=True, repr=False)
class HttpVerificationSampleReceiverConfiguration:
    target: NodeControlReceiverTarget
    declaration_identity: str
    verifier: HttpVerificationSampleVerifierConfiguration
    checks: tuple[HttpVerificationSampleCheckBinding, ...]
    profile: ClassVar[str] = "workload-http-verification-sample-configuration.v1"

    def __post_init__(self):
        _checked(lambda: _validate_configuration(self))
        object.__setattr__(self, "checks", tuple(sorted(self.checks)))


def _configuration_document(value):
    return dict(profile=HttpVerificationSampleReceiverConfiguration.profile,
        target=NodeControlReceiverTargetCodec().encode(value.target),
        declaration_identity=value.declaration_identity,
        verifier=dict(purpose=value.verifier.purpose.value, issuer=value.verifier.issuer,
            public_keys=[dict(key_id=key.key_id, algorithm=key.algorithm.value, public_key_pem=key.public_key_pem)
                         for key in sorted(value.verifier.public_keys, key=lambda key: key.key_id)]),
        checks=[dict(check_id=check.check_id, check_digest=check.check_digest) for check in sorted(value.checks)])


def _validate_configuration(value):
    _require(type(value) is HttpVerificationSampleReceiverConfiguration)
    NodeControlReceiverTargetCodec().encode(value.target)
    _text(value.declaration_identity, digest_violation)
    _validate_verifier(value.verifier)
    _require(type(value.checks) is tuple and 1 <= len(value.checks) <= 100)
    for check in value.checks:
        _require(type(check) is HttpVerificationSampleCheckBinding and replace(check) == check)
    _require(len({check.check_id for check in value.checks}) == len(value.checks))
    _bounded(_configuration_document(value), MAX_HTTP_VERIFICATION_SAMPLE_CONFIGURATION_BYTES)


class HttpVerificationSampleReceiverConfigurationCodec:
    def encode(self, value):
        def encode():
            _validate_configuration(value)
            return _configuration_document(value)
        return _checked(encode)

    def decode(self, document):
        def decode():
            value = _closed(document, ("profile", "target", "declaration_identity", "verifier", "checks"),
                MAX_HTTP_VERIFICATION_SAMPLE_CONFIGURATION_BYTES)
            _require(value["profile"] == HttpVerificationSampleReceiverConfiguration.profile)
            verifier = _closed(value["verifier"], ("purpose", "issuer", "public_keys"),
                MAX_HTTP_VERIFICATION_SAMPLE_CONFIGURATION_BYTES)
            _require(type(verifier["purpose"]) is str and type(verifier["public_keys"]) is list
                     and 1 <= len(verifier["public_keys"]) <= 16)
            keys = []
            for candidate in verifier["public_keys"]:
                key = _closed(candidate, ("key_id", "algorithm", "public_key_pem"),
                    MAX_HTTP_VERIFICATION_SAMPLE_CONFIGURATION_BYTES)
                _require(all(type(item) is str for item in key.values()))
                keys.append(DelegationPublicKey(key["key_id"], DelegationKeyAlgorithm(key["algorithm"]), key["public_key_pem"]))
            _require(type(value["checks"]) is list and 1 <= len(value["checks"]) <= 100)
            checks = []
            for candidate in value["checks"]:
                check = _closed(candidate, ("check_id", "check_digest"), MAX_HTTP_VERIFICATION_SAMPLE_CONFIGURATION_BYTES)
                checks.append(HttpVerificationSampleCheckBinding(**check))
            return HttpVerificationSampleReceiverConfiguration(
                NodeControlReceiverTargetCodec().decode(value["target"]), value["declaration_identity"],
                HttpVerificationSampleVerifierConfiguration(DelegationKeyPurpose(verifier["purpose"]),
                    verifier["issuer"], tuple(keys)), tuple(checks))
        return _checked(decode)

    def encode_canonical_bytes(self, value):
        return canonical_json_bytes(self.encode(value))

    def decode_canonical_bytes(self, raw):
        def decode():
            value = self.decode(_raw(raw, MAX_HTTP_VERIFICATION_SAMPLE_CONFIGURATION_BYTES))
            _require(self.encode_canonical_bytes(value) == raw)
            return value
        return _checked(decode)


@dataclass(frozen=True, slots=True)
class HttpVerificationSampleInstalledCheck:
    """An original check and product port, never a dispatch permit."""
    check: HttpCheck
    container_port: int


@dataclass(frozen=True, slots=True, repr=False)
class HttpVerificationSampleReceiverBinding:
    """Revalidate independent installed inputs before exposing the allowed subset."""
    configuration: HttpVerificationSampleReceiverConfiguration
    receiver_configuration: ReceiverNodeControlConfiguration
    verification_contract: VerificationContract
    provider_sockets: tuple[ProviderSocket, ...]
    provider_ports: tuple[ProviderRuntimePort, ...]

    def __post_init__(self):
        _checked(lambda: _admitted_checks(self))

    @property
    def checks(self):
        return _checked(lambda: _admitted_checks(self))

    @property
    def audience(self):
        def audience():
            _admitted_checks(self)
            return receiver_node_control_audience(self.receiver_configuration.target)
        return _checked(audience)

    def key(self, key_id):
        def key():
            _admitted_checks(self)
            _text(key_id)
            selected = tuple(key for key in self.configuration.verifier.public_keys if key.key_id == key_id)
            _require(len(selected) == 1)
            return selected[0]
        return _checked(key)

    def check_for_request(self, request):
        def check():
            _require(type(request) is HttpVerificationSampleRequest)
            entries = tuple(entry for entry in _admitted_checks(self) if entry.check.check_id == request.check_id)
            _require(len(entries) == 1)
            entry, = entries
            require_http_verification_sample_check(request,
                expected_target=self.receiver_configuration.target,
                expected_declaration_identity=self.receiver_configuration.declaration.identity().value,
                installed_check=entry.check)
            return entry
        return _checked(check)


def _admitted_checks(binding):
    _require(type(binding) is HttpVerificationSampleReceiverBinding)
    configuration = binding.configuration
    _validate_configuration(configuration)
    codec = ReceiverNodeControlConfigurationCodec()
    receiver = codec.decode(codec.encode(binding.receiver_configuration))
    _require(configuration.target == receiver.target
             and configuration.declaration_identity == receiver.declaration.identity().value)
    _require(type(binding.verification_contract) is VerificationContract
             and type(binding.provider_sockets) is tuple
             and all(type(socket) is ProviderSocket for socket in binding.provider_sockets)
             and type(binding.provider_ports) is tuple
             and all(type(port) is ProviderRuntimePort for port in binding.provider_ports))
    # Existing product laws prove protocol compatibility and unique named ports.
    product = ProductRuntimeContract(sockets=BlockSockets(providers=binding.provider_sockets),
        provider_ports=binding.provider_ports,
        verification=VerificationContract.from_descriptor(binding.verification_contract.descriptor()))
    receiver_provider = product.sockets.provider(receiver.target.provider_socket_name.value)
    _require(receiver_provider.protocol == Protocol.HTTP)
    entries = []
    for selected in sorted(configuration.checks):
        checks = tuple(check for check in product.verification.checks if check.check_id == selected.check_id)
        _require(len(checks) == 1 and type(checks[0]) is HttpCheck)
        check, = checks
        _require(selected.check_digest == http_verification_check_digest(check)
                 and check.expected_body_sha256 is None
                 and not any(300 <= status < 400 for status in check.expected_statuses))
        provider = product.sockets.provider(check.provider_socket)
        ports = tuple(port for port in product.provider_ports if port.provider_socket == check.provider_socket)
        _require(provider.protocol == Protocol.HTTP and len(ports) == 1)
        entries.append(HttpVerificationSampleInstalledCheck(check, ports[0].container_port))
    return tuple(entries)


def _slot(value):
    return ConfigurationArtifactRequirement(value.artifact_id, value.target_path, value.media_type, value.file_mode)


def select_http_verification_sample_configuration_slot(*, expected_slot,
        artifacts, environment, requirements=()):
    """Select the product's exact optional slot; file/provenance checks are external."""
    def select():
        _require(type(expected_slot) is ConfigurationArtifactRequirement)
        expected = ConfigurationArtifactRequirement.from_descriptor(expected_slot.descriptor())
        _require(expected.media_type is ConfigurationMediaType.JSON and expected.file_mode is ConfigurationFileMode.READ_ONLY)
        _require(type(artifacts) is tuple and type(requirements) is tuple and type(environment) is tuple)
        for artifact in artifacts:
            _require(type(artifact) is ConfigurationArtifact
                     and ConfigurationArtifact.from_descriptor(artifact.descriptor()) == artifact)
        for requirement in requirements:
            _require(type(requirement) is ConfigurationArtifactRequirement
                     and ConfigurationArtifactRequirement.from_descriptor(requirement.descriptor()) == requirement)
        _require(all(type(value) in (PublicStaticEnvironmentBinding, SocketDerivedEnvironmentBinding) for value in environment))
        for value in environment:
            replace(value)
        slots = (*artifacts, *requirements)
        _require(len({value.artifact_id for value in slots}) == len(slots)
                 and len({value.target_path for value in slots}) == len(slots))
        candidates = tuple(value for value in slots if value.artifact_id == expected.artifact_id
                           or value.target_path == expected.target_path)
        bindings = tuple(value for value in environment if value.name == HTTP_VERIFICATION_SAMPLES_CONFIGURATION_ENVIRONMENT)
        if not candidates and not bindings:
            return None
        _require(len(candidates) == 1 and len(bindings) == 1
                 and type(bindings[0]) is PublicStaticEnvironmentBinding
                 and bindings[0].value == expected.target_path and _slot(candidates[0]) == expected)
        return candidates[0]
    return _checked(select)


__all__ = [
    "MAX_HTTP_VERIFICATION_SAMPLE_CONFIGURATION_BYTES", "HTTP_VERIFICATION_SAMPLES_CONFIGURATION_ENVIRONMENT",
    "HttpVerificationSampleVerifierConfiguration", "HttpVerificationSampleCheckBinding",
    "HttpVerificationSampleReceiverConfiguration", "HttpVerificationSampleReceiverConfigurationCodec",
    "HttpVerificationSampleInstalledCheck", "HttpVerificationSampleReceiverBinding",
    "select_http_verification_sample_configuration_slot",
]
