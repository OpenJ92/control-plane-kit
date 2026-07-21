"""Pure handoff contracts for the future cpk-server process package."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from control_plane_kit_core.configuration import ConfigurationArtifact
from control_plane_kit_core.environment import (
    PublicStaticEnvironmentBinding,
    environment_binding_from_descriptor,
)
from control_plane_kit_core.operations.parity import (
    AdapterCommandParityContract,
    AdapterOperationSecurityParityContract,
    AdapterParityContract,
)
from control_plane_kit_core.operations.process import ControlPlaneProcessContract
from control_plane_kit_core.operations.services import DeploymentProgramBoundary
from control_plane_kit_core.operations.transactions import UnitOfWorkBoundary
from control_plane_kit_core.products import ProductIdentity, ProductIdentityCodec
from control_plane_kit_core.secrets import (
    SecretDelivery,
    secret_delivery_from_descriptor,
)


class InvalidCpkServerHandoffContract(ValueError):
    """Raised when the cpk-server handoff contract is incoherent."""


class EntrypointCompositionPolicy(StrEnum):
    """Closed composition policy for the future cpk-server wrapper."""

    ONE_DEPLOYMENT_PROGRAM = "one-deployment-program"


class ProcessStatePolicy(StrEnum):
    """Closed process state policy for hosted control-plane adapters."""

    PROCESS_GLOBALS_ARE_NOT_TRUTH = "process-globals-are-not-truth"
    PROCESS_GLOBALS_OWN_TRUTH = "process-globals-own-truth"


@dataclass(frozen=True)
class CpkServerEntrypointHandoffContract:
    """Core contract the external cpk-server process package must satisfy."""

    process: ControlPlaneProcessContract
    program: DeploymentProgramBoundary
    unit_of_work: UnitOfWorkBoundary
    projection_parity: AdapterParityContract
    command_parity: AdapterCommandParityContract
    security_parity: AdapterOperationSecurityParityContract
    implementation_package: str = "control-plane-kit-servers/cpk-server"
    import_direction: str = "cpk-server-imports-core"
    composition_policy: EntrypointCompositionPolicy = (
        EntrypointCompositionPolicy.ONE_DEPLOYMENT_PROGRAM
    )
    state_policy: ProcessStatePolicy = (
        ProcessStatePolicy.PROCESS_GLOBALS_ARE_NOT_TRUTH
    )

    def __post_init__(self) -> None:
        if not isinstance(self.process, ControlPlaneProcessContract):
            raise InvalidCpkServerHandoffContract(
                "process must be ControlPlaneProcessContract"
            )
        if not isinstance(self.program, DeploymentProgramBoundary):
            raise InvalidCpkServerHandoffContract(
                "program must be DeploymentProgramBoundary"
            )
        if not isinstance(self.unit_of_work, UnitOfWorkBoundary):
            raise InvalidCpkServerHandoffContract(
                "unit_of_work must be UnitOfWorkBoundary"
            )
        if not isinstance(self.projection_parity, AdapterParityContract):
            raise InvalidCpkServerHandoffContract(
                "projection_parity must be AdapterParityContract"
            )
        if not isinstance(self.command_parity, AdapterCommandParityContract):
            raise InvalidCpkServerHandoffContract(
                "command_parity must be AdapterCommandParityContract"
            )
        if not isinstance(
            self.security_parity,
            AdapterOperationSecurityParityContract,
        ):
            raise InvalidCpkServerHandoffContract(
                "security_parity must be AdapterOperationSecurityParityContract"
            )
        if self.implementation_package != "control-plane-kit-servers/cpk-server":
            raise InvalidCpkServerHandoffContract(
                "cpk-server implementation belongs to control-plane-kit-servers/cpk-server"
            )
        if self.import_direction != "cpk-server-imports-core":
            raise InvalidCpkServerHandoffContract(
                "cpk-server must import core; core must not import cpk-server"
            )
        if self.composition_policy is not (
            EntrypointCompositionPolicy.ONE_DEPLOYMENT_PROGRAM
        ):
            raise InvalidCpkServerHandoffContract(
                "cpk-server must compose one DeploymentProgram"
            )
        if self.state_policy is not ProcessStatePolicy.PROCESS_GLOBALS_ARE_NOT_TRUTH:
            raise InvalidCpkServerHandoffContract(
                "process globals must not own workflow truth"
            )
        if self.process.http_api is None:
            raise InvalidCpkServerHandoffContract(
                "cpk-server process handoff requires HTTP API contract"
            )
        if self.process.mcp is None:
            raise InvalidCpkServerHandoffContract(
                "cpk-server process handoff requires MCP contract"
            )
        if self.unit_of_work.program != self.program:
            raise InvalidCpkServerHandoffContract(
                "UnitOfWorkBoundary must describe the handoff program"
            )
        if self.process.http_api != self.projection_parity.http_api:
            raise InvalidCpkServerHandoffContract(
                "process HTTP API must match projection parity"
            )
        if self.process.http_api != self.command_parity.http_api:
            raise InvalidCpkServerHandoffContract(
                "process HTTP API must match command parity"
            )
        if self.process.mcp != self.projection_parity.mcp:
            raise InvalidCpkServerHandoffContract(
                "process MCP contract must match projection parity"
            )
        if self.process.mcp != self.command_parity.mcp:
            raise InvalidCpkServerHandoffContract(
                "process MCP contract must match command parity"
            )
        if self.command_parity.unit_of_work != self.unit_of_work:
            raise InvalidCpkServerHandoffContract(
                "command parity must use the handoff UnitOfWorkBoundary"
            )
        if self.security_parity.projection_parity != self.projection_parity:
            raise InvalidCpkServerHandoffContract(
                "security parity must use the handoff projection parity"
            )
        if self.security_parity.command_parity != self.command_parity:
            raise InvalidCpkServerHandoffContract(
                "security parity must use the handoff command parity"
            )

    @property
    def http_api(self):
        return self.process.http_api

    @property
    def mcp(self):
        return self.process.mcp

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "cpk-server-entrypoint-handoff",
            "implementation_package": self.implementation_package,
            "import_direction": self.import_direction,
            "composition_policy": self.composition_policy.value,
            "state_policy": self.state_policy.value,
            "process": self.process.descriptor(),
            "program": self.program.descriptor(),
            "unit_of_work": self.unit_of_work.descriptor(),
            "projection_parity": self.projection_parity.descriptor(),
            "command_parity": self.command_parity.descriptor(),
            "security_parity": self.security_parity.descriptor(),
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "CpkServerEntrypointHandoffContract":
        if set(value) != {
            "kind",
            "implementation_package",
            "import_direction",
            "composition_policy",
            "state_policy",
            "process",
            "program",
            "unit_of_work",
            "projection_parity",
            "command_parity",
            "security_parity",
        }:
            raise InvalidCpkServerHandoffContract(
                "cpk-server handoff descriptor has unexpected keys"
            )
        if value["kind"] != "cpk-server-entrypoint-handoff":
            raise InvalidCpkServerHandoffContract(
                "cpk-server handoff descriptor has wrong kind"
            )
        try:
            return cls(
                implementation_package=_text(
                    value["implementation_package"],
                    "implementation_package",
                ),
                import_direction=_text(value["import_direction"], "import_direction"),
                composition_policy=EntrypointCompositionPolicy(
                    _text(value["composition_policy"], "composition_policy")
                ),
                state_policy=ProcessStatePolicy(
                    _text(value["state_policy"], "state_policy")
                ),
                process=ControlPlaneProcessContract.from_descriptor(
                    _mapping(value["process"], "process")
                ),
                program=DeploymentProgramBoundary.from_descriptor(
                    _mapping(value["program"], "program")
                ),
                unit_of_work=UnitOfWorkBoundary.from_descriptor(
                    _mapping(value["unit_of_work"], "unit_of_work")
                ),
                projection_parity=AdapterParityContract.from_descriptor(
                    _mapping(value["projection_parity"], "projection_parity")
                ),
                command_parity=AdapterCommandParityContract.from_descriptor(
                    _mapping(value["command_parity"], "command_parity")
                ),
                security_parity=(
                    AdapterOperationSecurityParityContract.from_descriptor(
                        _mapping(value["security_parity"], "security_parity")
                    )
                ),
            )
        except ValueError as error:
            raise InvalidCpkServerHandoffContract(str(error)) from error


def canonical_cpk_server_entrypoint_handoff(
    *,
    process: ControlPlaneProcessContract,
    program: DeploymentProgramBoundary,
    unit_of_work: UnitOfWorkBoundary,
    projection_parity: AdapterParityContract,
    command_parity: AdapterCommandParityContract,
    security_parity: AdapterOperationSecurityParityContract,
) -> CpkServerEntrypointHandoffContract:
    """Construct the canonical core-to-cpk-server entrypoint handoff."""

    return CpkServerEntrypointHandoffContract(
        process=process,
        program=program,
        unit_of_work=unit_of_work,
        projection_parity=projection_parity,
        command_parity=command_parity,
        security_parity=security_parity,
    )


@dataclass(frozen=True)
class CpkServerMaterialHandoffContract:
    """Environment, secret, configuration, and descriptor obligations."""

    entrypoint: CpkServerEntrypointHandoffContract
    product_identity: ProductIdentity
    public_environment: tuple[PublicStaticEnvironmentBinding, ...] = ()
    required_environment_names: tuple[str, ...] = ()
    secret_deliveries: tuple[SecretDelivery, ...] = ()
    required_secret_environment_names: tuple[str, ...] = ()
    configuration_artifacts: tuple[ConfigurationArtifact, ...] = ()
    required_configuration_targets: tuple[str, ...] = ()
    descriptor_filename: str = "control-plane-instance.product.cpk.json"
    descriptor_admission_policy: str = "ordinary-external-product-data"
    self_registration_policy: str = "not-auto-registered"
    runtime_lookup_policy: str = "lookup-at-runtime"
    required_product_descriptor_fields: tuple[str, ...] = (
        "schema",
        "product.identity",
        "product.image",
        "product.runtime_contract",
        "product.runtime_contract.sockets",
        "product.runtime_contract.verification",
    )

    def __post_init__(self) -> None:
        if not isinstance(self.entrypoint, CpkServerEntrypointHandoffContract):
            raise InvalidCpkServerHandoffContract(
                "entrypoint must be CpkServerEntrypointHandoffContract"
            )
        if not isinstance(self.product_identity, ProductIdentity):
            raise InvalidCpkServerHandoffContract(
                "product_identity must be ProductIdentity"
            )
        if self.product_identity.namespace != "control-plane-kit" or (
            self.product_identity.name != "cpk-server"
        ):
            raise InvalidCpkServerHandoffContract(
                "material handoff must describe the cpk-server product identity"
            )
        if not isinstance(self.public_environment, tuple) or not all(
            isinstance(binding, PublicStaticEnvironmentBinding)
            for binding in self.public_environment
        ):
            raise InvalidCpkServerHandoffContract(
                "public_environment must contain PublicStaticEnvironmentBinding values"
            )
        if not isinstance(self.secret_deliveries, tuple) or not all(
            isinstance(delivery, SecretDelivery)
            for delivery in self.secret_deliveries
        ):
            raise InvalidCpkServerHandoffContract(
                "secret_deliveries must contain SecretDelivery values"
            )
        if not isinstance(self.configuration_artifacts, tuple) or not all(
            isinstance(artifact, ConfigurationArtifact)
            for artifact in self.configuration_artifacts
        ):
            raise InvalidCpkServerHandoffContract(
                "configuration_artifacts must contain ConfigurationArtifact values"
            )
        _validate_names(self.required_environment_names, "required_environment_names")
        _validate_names(
            self.required_secret_environment_names,
            "required_secret_environment_names",
        )
        _validate_names(
            self.required_product_descriptor_fields,
            "required_product_descriptor_fields",
            allow_dots=True,
        )
        _validate_targets(self.required_configuration_targets)
        for binding in self.public_environment:
            _reject_private_public_environment(binding)
        delivered_secret_names = {
            getattr(delivery, "environment_name", None)
            for delivery in self.secret_deliveries
        }
        if not set(self.required_secret_environment_names) <= delivered_secret_names:
            raise InvalidCpkServerHandoffContract(
                "required secret environment names must have secret deliveries"
            )
        artifact_targets = {
            artifact.target_path for artifact in self.configuration_artifacts
        }
        if not set(self.required_configuration_targets) <= artifact_targets:
            raise InvalidCpkServerHandoffContract(
                "required configuration targets must have artifacts"
            )
        if self.descriptor_filename != "control-plane-instance.product.cpk.json":
            raise InvalidCpkServerHandoffContract(
                "cpk-server descriptor filename is fixed by handoff"
            )
        if self.descriptor_admission_policy != "ordinary-external-product-data":
            raise InvalidCpkServerHandoffContract(
                "cpk-server descriptor must be ordinary external product data"
            )
        if self.self_registration_policy != "not-auto-registered":
            raise InvalidCpkServerHandoffContract(
                "cpk-server descriptor must not be auto-registered or auto-trusted"
            )
        if self.runtime_lookup_policy != "lookup-at-runtime":
            raise InvalidCpkServerHandoffContract(
                "cpk-server material must be looked up at runtime"
            )

        ordered_public = tuple(sorted(self.public_environment))
        ordered_secrets = tuple(
            sorted(self.secret_deliveries, key=lambda delivery: repr(delivery.descriptor()))
        )
        ordered_artifacts = tuple(sorted(self.configuration_artifacts))
        object.__setattr__(self, "public_environment", ordered_public)
        object.__setattr__(self, "secret_deliveries", ordered_secrets)
        object.__setattr__(self, "configuration_artifacts", ordered_artifacts)
        object.__setattr__(
            self,
            "required_environment_names",
            tuple(sorted(self.required_environment_names)),
        )
        object.__setattr__(
            self,
            "required_secret_environment_names",
            tuple(sorted(self.required_secret_environment_names)),
        )
        object.__setattr__(
            self,
            "required_configuration_targets",
            tuple(sorted(self.required_configuration_targets)),
        )
        object.__setattr__(
            self,
            "required_product_descriptor_fields",
            tuple(sorted(self.required_product_descriptor_fields)),
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "cpk-server-material-handoff",
            "entrypoint": self.entrypoint.descriptor(),
            "product_identity": ProductIdentityCodec().encode(self.product_identity),
            "public_environment": [
                binding.descriptor() for binding in self.public_environment
            ],
            "required_environment_names": list(self.required_environment_names),
            "secret_deliveries": [
                delivery.descriptor() for delivery in self.secret_deliveries
            ],
            "required_secret_environment_names": list(
                self.required_secret_environment_names
            ),
            "configuration_artifacts": [
                artifact.descriptor() for artifact in self.configuration_artifacts
            ],
            "required_configuration_targets": list(
                self.required_configuration_targets
            ),
            "descriptor_filename": self.descriptor_filename,
            "descriptor_admission_policy": self.descriptor_admission_policy,
            "self_registration_policy": self.self_registration_policy,
            "runtime_lookup_policy": self.runtime_lookup_policy,
            "required_product_descriptor_fields": list(
                self.required_product_descriptor_fields
            ),
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "CpkServerMaterialHandoffContract":
        if set(value) != {
            "kind",
            "entrypoint",
            "product_identity",
            "public_environment",
            "required_environment_names",
            "secret_deliveries",
            "required_secret_environment_names",
            "configuration_artifacts",
            "required_configuration_targets",
            "descriptor_filename",
            "descriptor_admission_policy",
            "self_registration_policy",
            "runtime_lookup_policy",
            "required_product_descriptor_fields",
        }:
            raise InvalidCpkServerHandoffContract(
                "cpk-server material descriptor has unexpected keys"
            )
        if value["kind"] != "cpk-server-material-handoff":
            raise InvalidCpkServerHandoffContract(
                "cpk-server material descriptor has wrong kind"
            )
        try:
            public_environment = _list(value["public_environment"], "public_environment")
            secret_deliveries = _list(value["secret_deliveries"], "secret_deliveries")
            configuration_artifacts = _list(
                value["configuration_artifacts"],
                "configuration_artifacts",
            )
            return cls(
                entrypoint=CpkServerEntrypointHandoffContract.from_descriptor(
                    _mapping(value["entrypoint"], "entrypoint")
                ),
                product_identity=ProductIdentityCodec().decode(
                    _mapping(value["product_identity"], "product_identity")
                ),
                public_environment=tuple(
                    _public_environment(binding)
                    for binding in public_environment
                ),
                required_environment_names=tuple(
                    _string_list(
                        value["required_environment_names"],
                        "required_environment_names",
                    )
                ),
                secret_deliveries=tuple(
                    secret_delivery_from_descriptor(
                        _mapping(delivery, "secret_delivery")
                    )
                    for delivery in secret_deliveries
                ),
                required_secret_environment_names=tuple(
                    _string_list(
                        value["required_secret_environment_names"],
                        "required_secret_environment_names",
                    )
                ),
                configuration_artifacts=tuple(
                    ConfigurationArtifact.from_descriptor(
                        _mapping(artifact, "configuration_artifact")
                    )
                    for artifact in configuration_artifacts
                ),
                required_configuration_targets=tuple(
                    _string_list(
                        value["required_configuration_targets"],
                        "required_configuration_targets",
                    )
                ),
                descriptor_filename=_text(
                    value["descriptor_filename"],
                    "descriptor_filename",
                ),
                descriptor_admission_policy=_text(
                    value["descriptor_admission_policy"],
                    "descriptor_admission_policy",
                ),
                self_registration_policy=_text(
                    value["self_registration_policy"],
                    "self_registration_policy",
                ),
                runtime_lookup_policy=_text(
                    value["runtime_lookup_policy"],
                    "runtime_lookup_policy",
                ),
                required_product_descriptor_fields=tuple(
                    _string_list(
                        value["required_product_descriptor_fields"],
                        "required_product_descriptor_fields",
                    )
                ),
            )
        except ValueError as error:
            raise InvalidCpkServerHandoffContract(str(error)) from error


def canonical_cpk_server_material_handoff(
    *,
    entrypoint: CpkServerEntrypointHandoffContract,
    product_identity: ProductIdentity,
    public_environment: tuple[PublicStaticEnvironmentBinding, ...] = (),
    required_environment_names: tuple[str, ...] = (),
    secret_deliveries: tuple[SecretDelivery, ...] = (),
    required_secret_environment_names: tuple[str, ...] = (),
    configuration_artifacts: tuple[ConfigurationArtifact, ...] = (),
    required_configuration_targets: tuple[str, ...] = (),
) -> CpkServerMaterialHandoffContract:
    """Construct the canonical material handoff for the future cpk-server."""

    return CpkServerMaterialHandoffContract(
        entrypoint=entrypoint,
        product_identity=product_identity,
        public_environment=public_environment,
        required_environment_names=required_environment_names,
        secret_deliveries=secret_deliveries,
        required_secret_environment_names=required_secret_environment_names,
        configuration_artifacts=configuration_artifacts,
        required_configuration_targets=required_configuration_targets,
    )


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidCpkServerHandoffContract(f"{field} must be text")
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidCpkServerHandoffContract(f"{field} must be a descriptor")
    return value


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise InvalidCpkServerHandoffContract(f"{field} must be a list")
    return value


def _string_list(value: object, field: str) -> list[str]:
    values = _list(value, field)
    if not all(isinstance(item, str) for item in values):
        raise InvalidCpkServerHandoffContract(f"{field} must be a string list")
    return values


def _validate_names(
    values: tuple[str, ...],
    field: str,
    *,
    allow_dots: bool = False,
) -> None:
    if not isinstance(values, tuple) or not all(isinstance(value, str) for value in values):
        raise InvalidCpkServerHandoffContract(f"{field} must be a tuple of strings")
    for value in values:
        allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
        if allow_dots:
            allowed |= set("abcdefghijklmnopqrstuvwxyz.")
        if not value or any(character not in allowed for character in value):
            raise InvalidCpkServerHandoffContract(f"{field} contains invalid value")


def _validate_targets(values: tuple[str, ...]) -> None:
    if not isinstance(values, tuple) or not all(isinstance(value, str) for value in values):
        raise InvalidCpkServerHandoffContract(
            "required_configuration_targets must be a tuple of strings"
        )
    for value in values:
        if not value.startswith("/"):
            raise InvalidCpkServerHandoffContract(
                "required configuration targets must be absolute paths"
            )


def _public_environment(value: object) -> PublicStaticEnvironmentBinding:
    binding = environment_binding_from_descriptor(_mapping(value, "public_environment"))
    if not isinstance(binding, PublicStaticEnvironmentBinding):
        raise InvalidCpkServerHandoffContract(
            "public environment must use public-static bindings"
        )
    return binding


def _reject_private_public_environment(
    binding: PublicStaticEnvironmentBinding,
) -> None:
    normalized = binding.value.casefold()
    if any(
        marker in normalized
        for marker in (
            "postgres://",
            "postgresql://",
            "private.",
            "internal.",
            "127.0.0.1",
            "0.0.0.0",
        )
    ):
        raise InvalidCpkServerHandoffContract(
            "private endpoints must not be baked into public environment"
        )
