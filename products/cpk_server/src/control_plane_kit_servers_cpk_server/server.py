"""Runnable FastAPI process for the cpk-server image."""

from __future__ import annotations

from dataclasses import dataclass, field
import base64
from datetime import datetime, timezone
import json
import os
import time
from typing import Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import psycopg
import uvicorn
from control_plane_kit_core.identity import (
    CredentialVerifier,
    PrincipalKind,
    WorkspaceGrant,
)
from control_plane_kit_core.operations.execution import EffectResultKind
from control_plane_kit_core.operations.lifecycle import FailureCategory
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    LiteralEndpointMaterial,
)
from control_plane_kit_core.secrets import SecretReference, SecretResolutionError
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations import (
    ActivityExecutionOutcome,
    ActivityExecutionAdapter,
    ActivityPlanningCommandService,
    ActivityRealizationContext,
    ApprovalCommandService,
    BoundedEvidence,
    CloudflareOwnedIngressResource,
    CloudflareZoneIngressAuthority,
    CpkServerOperationsApplication,
    CurrentGraphAdvancementCommandService,
    DesiredGraphCommandService,
    ExecutionAdmissionCommandService,
    ExecutionCoordinator,
    FailureEvidence,
    GatewayProbeAttemptStatus,
    GatewayProbeCommandService,
    GatewayProbeDispatch,
    GatewayProbeDispatchError,
    GatewayProbeDispatchResult,
    ImagePullAuthorityRegistrationService,
    InMemoryGeneratedSecretRecorder,
    IngressAuthorityProviderKind,
    IngressAuthorityRegistrationService,
    IngressRealizationAdapter,
    OperationCommandService,
    ProductRegistrationService,
    RuntimeAuthorityRegistrationService,
    RuntimeDispatcherBootstrapConfiguration,
    RuntimeDispatcherBootstrapError,
    RuntimeInterpreterDispatcher,
    RunLifecycleCommandService,
    WorkspaceCommandService,
    cpk_server_services,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema

from .boundary import (
    CpkServerApplicationBoundary,
    CpkServerHttpProcessBoundary,
    CpkServerMcpProcessBoundary,
)
from .authentication import (
    StaticDevelopmentCredentialVerifier,
    StaticDevelopmentMultiCredentialVerifier,
    StaticDevelopmentPrincipalCredential,
    static_development_principal,
)
from .composition import (
    CpkServerCompositionError,
    CpkServerProcessConfiguration,
    create_cpk_server_composition,
)


class BootstrapConfigurationError(ValueError):
    """Raised when required process bootstrap configuration is missing."""


@dataclass(frozen=True, slots=True)
class IngressInterpreterBootstrapConfiguration:
    """Closed process-local ingress interpreter availability."""

    provider_kinds: tuple[IngressAuthorityProviderKind, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.provider_kinds, tuple):
            raise BootstrapConfigurationError(
                "ingress interpreter provider kinds must be a tuple"
            )
        if len(set(self.provider_kinds)) != len(self.provider_kinds):
            raise BootstrapConfigurationError(
                "ingress interpreter providers must be unique"
            )
        if not all(
            isinstance(kind, IngressAuthorityProviderKind)
            for kind in self.provider_kinds
        ):
            raise BootstrapConfigurationError(
                "ingress interpreter providers must be closed provider kinds"
            )

    @property
    def enabled(self) -> bool:
        return bool(self.provider_kinds)

    @classmethod
    def from_process_value(
        cls,
        value: str,
    ) -> "IngressInterpreterBootstrapConfiguration":
        if value == "none":
            return cls(())
        parts = tuple(part.strip() for part in value.split(",") if part.strip())
        if not parts:
            raise BootstrapConfigurationError(
                "CPK_INGRESS_INTERPRETERS must be one of: none, cloudflare"
            )
        providers: list[IngressAuthorityProviderKind] = []
        for part in parts:
            try:
                providers.append(IngressAuthorityProviderKind(part))
            except ValueError as error:
                raise BootstrapConfigurationError(
                    "ingress interpreter bootstrap includes an unknown provider kind"
                ) from error
        return cls(tuple(providers))

    def __str__(self) -> str:
        if not self.provider_kinds:
            return "none"
        return ",".join(kind.value for kind in self.provider_kinds)


@dataclass(frozen=True, slots=True)
class CpkServerBootstrapConfiguration:
    mode: str
    control_auth_verifier: str
    control_auth_static_credential: bytes | None = field(
        repr=False,
        compare=False,
        hash=False,
    )
    port: int
    runtime_dispatcher: RuntimeDispatcherBootstrapConfiguration
    ingress_interpreters: IngressInterpreterBootstrapConfiguration
    image_pull_credential_resolver: str
    product_secret_resolver: str
    product_secret_values_json: str | None = field(repr=False)
    docker_config_path: str | None
    docker_config_json: str | None = field(repr=False)
    store_endpoints: Mapping[str, str]
    gateway_probe_signer: str = "none"
    gateway_probe_signing_key_reference: SecretReference | None = None
    gateway_probe_issuer: str | None = None
    gateway_probe_key_id: str | None = None
    control_auth_static_workspace_grants: tuple[WorkspaceGrant, ...] = ()
    control_auth_static_principals: tuple[
        StaticDevelopmentPrincipalCredential, ...
    ] = field(default=(), repr=False, compare=False, hash=False)

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "CpkServerBootstrapConfiguration":
        values = dict(os.environ if environ is None else environ)
        mode = _required(values, "CPK_SERVER_MODE")
        control_auth_verifier = values.get("CPK_CONTROL_AUTH_VERIFIER", "none")
        control_auth_static_credential_text = values.get(
            "CPK_CONTROL_AUTH_STATIC_CREDENTIAL"
        )
        control_auth_static_workspace_grants_text = values.get(
            "CPK_CONTROL_AUTH_STATIC_WORKSPACE_GRANTS_JSON"
        )
        control_auth_static_principals_text = values.get(
            "CPK_CONTROL_AUTH_STATIC_PRINCIPALS_JSON"
        )
        port_text = _required(values, "CPK_PORT")
        try:
            runtime_dispatcher = RuntimeDispatcherBootstrapConfiguration.from_process_value(
                _required(values, "CPK_RUNTIME_INTERPRETERS")
            )
        except RuntimeDispatcherBootstrapError as error:
            raise BootstrapConfigurationError(str(error)) from error
        ingress_interpreters = IngressInterpreterBootstrapConfiguration.from_process_value(
            values.get("CPK_INGRESS_INTERPRETERS", "none")
        )
        image_pull_credential_resolver = values.get(
            "CPK_IMAGE_PULL_CREDENTIAL_RESOLVER",
            "none",
        )
        product_secret_resolver = values.get("CPK_PRODUCT_SECRET_RESOLVER", "none")
        product_secret_values_json = values.get("CPK_PRODUCT_SECRET_VALUES_JSON")
        docker_config_path = _docker_config_path(values)
        docker_config_json = values.get("CPK_DOCKER_AUTH_CONFIG_JSON")
        gateway_probe_signer = values.get("CPK_GATEWAY_PROBE_SIGNER", "none")
        gateway_probe_signing_key_text = values.get(
            "CPK_GATEWAY_PROBE_SIGNING_KEY_REF"
        )
        gateway_probe_issuer = values.get("CPK_GATEWAY_PROBE_ISSUER")
        gateway_probe_key_id = values.get("CPK_GATEWAY_PROBE_KEY_ID")
        store_endpoints = {
            name: _required(values, name)
            for name in (
                "CPK_WORKPLACE_DATABASE_URL",
                "CPK_ACTIVITY_HISTORY_DATABASE_URL",
                "CPK_OBSERVER_STATE_DATABASE_URL",
                "CPK_GRAPH_TOPOLOGY_DATABASE_URL",
            )
        }
        if mode != "execution-capable":
            raise BootstrapConfigurationError("CPK_SERVER_MODE must be execution-capable")
        if control_auth_verifier not in {"none", "static-development"}:
            raise BootstrapConfigurationError(
                "CPK_CONTROL_AUTH_VERIFIER must be one of: none, static-development"
            )
        control_auth_static_credential = None
        control_auth_static_workspace_grants: tuple[WorkspaceGrant, ...] = ()
        control_auth_static_principals: tuple[
            StaticDevelopmentPrincipalCredential, ...
        ] = ()
        if control_auth_verifier == "static-development":
            if control_auth_static_principals_text is not None:
                if (
                    control_auth_static_credential_text is not None
                    or control_auth_static_workspace_grants_text is not None
                ):
                    raise BootstrapConfigurationError(
                        "CPK_CONTROL_AUTH_STATIC_PRINCIPALS_JSON must not be mixed "
                        "with single static credential configuration"
                    )
                control_auth_static_principals = _static_principals(
                    control_auth_static_principals_text
                )
            elif not control_auth_static_credential_text:
                raise BootstrapConfigurationError(
                    "CPK_CONTROL_AUTH_VERIFIER=static-development requires "
                    "CPK_CONTROL_AUTH_STATIC_CREDENTIAL"
                )
            else:
                try:
                    control_auth_static_credential = (
                        control_auth_static_credential_text.encode("ascii")
                    )
                except UnicodeEncodeError as error:
                    raise BootstrapConfigurationError(
                        "CPK_CONTROL_AUTH_STATIC_CREDENTIAL must be bounded ASCII"
                    ) from error
                try:
                    StaticDevelopmentCredentialVerifier(control_auth_static_credential)
                except ValueError as error:
                    raise BootstrapConfigurationError(
                        "CPK_CONTROL_AUTH_STATIC_CREDENTIAL must be bounded and nonempty"
                    ) from error
                if not control_auth_static_workspace_grants_text:
                    raise BootstrapConfigurationError(
                        "CPK_CONTROL_AUTH_VERIFIER=static-development requires "
                        "CPK_CONTROL_AUTH_STATIC_WORKSPACE_GRANTS_JSON"
                    )
                control_auth_static_workspace_grants = _static_workspace_grants(
                    control_auth_static_workspace_grants_text
                )
        elif control_auth_static_credential_text is not None:
            raise BootstrapConfigurationError(
                "CPK_CONTROL_AUTH_STATIC_CREDENTIAL requires "
                "CPK_CONTROL_AUTH_VERIFIER=static-development"
            )
        elif control_auth_static_workspace_grants_text is not None:
            raise BootstrapConfigurationError(
                "CPK_CONTROL_AUTH_STATIC_WORKSPACE_GRANTS_JSON requires "
                "CPK_CONTROL_AUTH_VERIFIER=static-development"
            )
        elif control_auth_static_principals_text is not None:
            raise BootstrapConfigurationError(
                "CPK_CONTROL_AUTH_STATIC_PRINCIPALS_JSON requires "
                "CPK_CONTROL_AUTH_VERIFIER=static-development"
            )
        try:
            port = int(port_text)
        except ValueError as error:
            raise BootstrapConfigurationError("CPK_PORT must be an integer") from error
        if not 1 <= port <= 65535:
            raise BootstrapConfigurationError("CPK_PORT must be in TCP port range")
        if image_pull_credential_resolver not in {"none", "docker-config"}:
            raise BootstrapConfigurationError(
                "CPK_IMAGE_PULL_CREDENTIAL_RESOLVER must be one of: none, docker-config"
            )
        if product_secret_resolver not in {"none", "local-development"}:
            raise BootstrapConfigurationError(
                "CPK_PRODUCT_SECRET_RESOLVER must be one of: none, local-development"
            )
        if gateway_probe_signer not in {"none", "ed25519"}:
            raise BootstrapConfigurationError(
                "CPK_GATEWAY_PROBE_SIGNER must be one of: none, ed25519"
            )
        gateway_probe_signing_key_reference = None
        gateway_probe_fields = (
            gateway_probe_signing_key_text,
            gateway_probe_issuer,
            gateway_probe_key_id,
        )
        if gateway_probe_signer == "none" and any(
            value is not None for value in gateway_probe_fields
        ):
            raise BootstrapConfigurationError(
                "gateway probe signing authority requires "
                "CPK_GATEWAY_PROBE_SIGNER=ed25519"
            )
        if gateway_probe_signer == "ed25519":
            if not all(gateway_probe_fields):
                raise BootstrapConfigurationError(
                    "CPK_GATEWAY_PROBE_SIGNER=ed25519 requires signing key "
                    "reference, issuer, and key id"
                )
            if product_secret_resolver == "none":
                raise BootstrapConfigurationError(
                    "CPK_GATEWAY_PROBE_SIGNER=ed25519 requires "
                    "CPK_PRODUCT_SECRET_RESOLVER"
                )
            try:
                gateway_probe_signing_key_reference = SecretReference(
                    gateway_probe_signing_key_text
                )
            except SecretResolutionError as error:
                raise BootstrapConfigurationError(
                    "CPK_GATEWAY_PROBE_SIGNING_KEY_REF must be a secret reference"
                ) from error
            gateway_probe_issuer = _bounded_ascii(
                gateway_probe_issuer,
                "CPK_GATEWAY_PROBE_ISSUER",
            )
            gateway_probe_key_id = _bounded_ascii(
                gateway_probe_key_id,
                "CPK_GATEWAY_PROBE_KEY_ID",
            )
        if (
            image_pull_credential_resolver == "docker-config"
            and RuntimeKind.DOCKER not in runtime_dispatcher.runtime_kinds
        ):
            raise BootstrapConfigurationError(
                "CPK_IMAGE_PULL_CREDENTIAL_RESOLVER=docker-config requires "
                "a Docker runtime interpreter"
            )
        if (
            image_pull_credential_resolver == "docker-config"
            and docker_config_path is None
            and not docker_config_json
        ):
            raise BootstrapConfigurationError(
                "CPK_IMAGE_PULL_CREDENTIAL_RESOLVER=docker-config requires "
                "DOCKER_CONFIG, CPK_DOCKER_AUTH_CONFIG, or CPK_DOCKER_AUTH_CONFIG_JSON"
            )
        if (
            product_secret_resolver == "local-development"
            and RuntimeKind.DOCKER not in runtime_dispatcher.runtime_kinds
            and IngressAuthorityProviderKind.CLOUDFLARE
            not in ingress_interpreters.provider_kinds
            and gateway_probe_signer == "none"
        ):
            raise BootstrapConfigurationError(
                "CPK_PRODUCT_SECRET_RESOLVER=local-development requires "
                "a Docker runtime interpreter, Cloudflare ingress interpreter, "
                "or gateway probe signer"
            )
        if product_secret_resolver == "local-development":
            if product_secret_values_json is None or product_secret_values_json == "":
                raise BootstrapConfigurationError(
                    "CPK_PRODUCT_SECRET_RESOLVER=local-development requires "
                    "CPK_PRODUCT_SECRET_VALUES_JSON"
                )
        if (
            IngressAuthorityProviderKind.CLOUDFLARE
            in ingress_interpreters.provider_kinds
            and product_secret_resolver == "none"
        ):
            raise BootstrapConfigurationError(
                "CPK_INGRESS_INTERPRETERS=cloudflare requires "
                "CPK_PRODUCT_SECRET_RESOLVER"
            )
        return cls(
            mode=mode,
            control_auth_verifier=control_auth_verifier,
            control_auth_static_credential=control_auth_static_credential,
            control_auth_static_principals=control_auth_static_principals,
            port=port,
            runtime_dispatcher=runtime_dispatcher,
            ingress_interpreters=ingress_interpreters,
            image_pull_credential_resolver=image_pull_credential_resolver,
            product_secret_resolver=product_secret_resolver,
            product_secret_values_json=product_secret_values_json,
            docker_config_path=docker_config_path,
            docker_config_json=docker_config_json,
            store_endpoints=store_endpoints,
            gateway_probe_signer=gateway_probe_signer,
            gateway_probe_signing_key_reference=(
                gateway_probe_signing_key_reference
            ),
            gateway_probe_issuer=gateway_probe_issuer,
            gateway_probe_key_id=gateway_probe_key_id,
            control_auth_static_workspace_grants=(
                control_auth_static_workspace_grants
            ),
        )

    def process_configuration(self) -> CpkServerProcessConfiguration:
        return CpkServerProcessConfiguration.execution_capable(
            authentication_required=True
        )

    def operations_database_url(self) -> str:
        urls = set(self.store_endpoints.values())
        if len(urls) != 1:
            raise BootstrapConfigurationError(
                "current operations package requires all CPK_*_DATABASE_URL values "
                "to point at one instance database"
            )
        return next(iter(urls))


def create_app(
    config: CpkServerBootstrapConfiguration,
    credential_verifier: CredentialVerifier,
) -> FastAPI:
    """Create the hosted cpk-server FastAPI application."""

    composition = create_cpk_server_composition(config.process_configuration())
    application = CpkServerApplicationBoundary(
        _operations_application(config).services,
        credential_verifier,
    )
    http_boundary = CpkServerHttpProcessBoundary(composition, application)
    mcp_boundary = CpkServerMcpProcessBoundary(composition, application)
    app = FastAPI(
        title="cpk-server",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.http_boundary = http_boundary
    app.state.mcp_boundary = mcp_boundary

    @app.get("/health/live")
    async def live() -> JSONResponse:
        return _json_response(200, {"status": "live"})

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        return _json_response(
            200,
            {
                "status": "ready",
                "application": "configured",
                "stores": "configured",
                "runtime_interpreters": str(config.runtime_dispatcher),
                "ingress_interpreters": str(config.ingress_interpreters),
            },
        )

    @app.post("/mcp")
    async def mcp(request: Request) -> JSONResponse:
        body = await request.body()
        try:
            message = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _json_response(
                400,
                {"error": {"status": 400, "message": "invalid JSON request body"}},
            )
        response = mcp_boundary.handle(
            headers=request.headers,
            message=message,
        )
        return _json_response(response.status, response.body)

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def http(path: str, request: Request) -> JSONResponse:
        response = http_boundary.handle(
            method=request.method,
            path=request.url.path,
            headers=request.headers,
            body=await request.body(),
        )
        return _json_response(response.status, response.body)

    return app


def main() -> int:
    try:
        config = CpkServerBootstrapConfiguration.from_environment()
        credential_verifier = _credential_verifier(config)
    except (BootstrapConfigurationError, CpkServerCompositionError) as error:
        print(f"cpk-server bootstrap error: {error}", flush=True)
        return 2
    print(f"cpk-server listening on 0.0.0.0:{config.port}", flush=True)
    uvicorn.run(
        create_app(config, credential_verifier),
        host="0.0.0.0",
        port=config.port,
        access_log=False,
    )
    return 0


def _credential_verifier(
    config: CpkServerBootstrapConfiguration,
) -> CredentialVerifier:
    if config.control_auth_verifier == "static-development":
        if config.control_auth_static_principals:
            return StaticDevelopmentMultiCredentialVerifier(
                config.control_auth_static_principals
            )
        assert config.control_auth_static_credential is not None
        return StaticDevelopmentCredentialVerifier(
            config.control_auth_static_credential,
            config.control_auth_static_workspace_grants,
        )
    raise BootstrapConfigurationError(
        "no credential verifier is configured for cpk-server"
    )


def _docker_config_path(values: Mapping[str, str]) -> str | None:
    docker_config = values.get("DOCKER_CONFIG")
    if docker_config:
        return os.path.join(docker_config, "config.json")
    docker_auth_config = values.get("CPK_DOCKER_AUTH_CONFIG")
    if docker_auth_config:
        return docker_auth_config
    return None


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name)
    if value is None or value == "":
        raise BootstrapConfigurationError(f"{name} is required")
    return value


def _bounded_ascii(value: str | None, name: str, *, maximum: int = 256) -> str:
    if value is None or not 1 <= len(value) <= maximum:
        raise BootstrapConfigurationError(f"{name} must be bounded ASCII")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise BootstrapConfigurationError(f"{name} must be bounded ASCII") from error
    if any(byte < 0x21 or byte > 0x7E for byte in encoded):
        raise BootstrapConfigurationError(f"{name} must be bounded ASCII")
    return value


def _static_workspace_grants(value: str) -> tuple[WorkspaceGrant, ...]:
    if not isinstance(value, str) or not 1 <= len(value) <= 65_536:
        raise BootstrapConfigurationError(
            "CPK_CONTROL_AUTH_STATIC_WORKSPACE_GRANTS_JSON must be bounded JSON"
        )
    try:
        value.encode("ascii")
        raw = json.loads(value)
    except (UnicodeEncodeError, json.JSONDecodeError) as error:
        raise BootstrapConfigurationError(
            "CPK_CONTROL_AUTH_STATIC_WORKSPACE_GRANTS_JSON must be bounded JSON"
        ) from error
    return _static_workspace_grants_from_mapping(raw)


def _static_workspace_grants_from_mapping(raw: object) -> tuple[WorkspaceGrant, ...]:
    if not isinstance(raw, Mapping) or not raw or len(raw) > 64:
        raise BootstrapConfigurationError(
            "static workspace grants must map exact workspace ids to scopes"
        )
    grants: list[WorkspaceGrant] = []
    for workspace_id, raw_scopes in raw.items():
        if workspace_id == "*":
            raise BootstrapConfigurationError(
                "wildcard workspace grants are forbidden"
            )
        workspace_id = _bounded_ascii(
            workspace_id if isinstance(workspace_id, str) else None,
            "static workspace grant id",
            maximum=128,
        )
        if (
            not isinstance(raw_scopes, list)
            or not raw_scopes
            or len(raw_scopes) > len(PolicyScope)
            or not all(isinstance(scope, str) for scope in raw_scopes)
            or len(set(raw_scopes)) != len(raw_scopes)
        ):
            raise BootstrapConfigurationError(
                "static workspace grant scopes must be unique closed scope names"
            )
        try:
            scopes = tuple(PolicyScope(scope) for scope in raw_scopes)
        except ValueError as error:
            raise BootstrapConfigurationError(
                "static workspace grant includes an unknown policy scope"
            ) from error
        grants.append(WorkspaceGrant(workspace_id, scopes))
    return tuple(sorted(grants, key=lambda grant: grant.workspace_id))


def _static_principals(
    value: str,
) -> tuple[StaticDevelopmentPrincipalCredential, ...]:
    if not isinstance(value, str) or not 1 <= len(value) <= 131_072:
        raise BootstrapConfigurationError(
            "CPK_CONTROL_AUTH_STATIC_PRINCIPALS_JSON must be bounded JSON"
        )
    try:
        value.encode("ascii")
        raw = json.loads(value)
    except (UnicodeEncodeError, json.JSONDecodeError) as error:
        raise BootstrapConfigurationError(
            "CPK_CONTROL_AUTH_STATIC_PRINCIPALS_JSON must be bounded JSON"
        ) from error
    if not isinstance(raw, list) or not raw or len(raw) > 16:
        raise BootstrapConfigurationError(
            "CPK_CONTROL_AUTH_STATIC_PRINCIPALS_JSON must list principals"
        )
    principals: list[StaticDevelopmentPrincipalCredential] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise BootstrapConfigurationError("static principal must be an object")
        credential_text = _bounded_ascii(
            item.get("credential") if isinstance(item.get("credential"), str) else None,
            f"static principal {index} credential",
            maximum=4096,
        )
        subject_id = _bounded_ascii(
            item.get("subject_id") if isinstance(item.get("subject_id"), str) else None,
            f"static principal {index} subject_id",
            maximum=128,
        )
        try:
            kind = PrincipalKind(item.get("kind"))
        except ValueError as error:
            raise BootstrapConfigurationError(
                "static principal kind must be operator, service, or worker"
            ) from error
        grants = _static_workspace_grants_from_mapping(item.get("workspace_grants"))
        try:
            principals.append(
                StaticDevelopmentPrincipalCredential(
                    credential_text.encode("ascii"),
                    static_development_principal(
                        subject_id=subject_id,
                        kind=kind,
                        workspace_grants=grants,
                    ),
                )
            )
        except ValueError as error:
            raise BootstrapConfigurationError(
                "static principal must contain valid credential and grants"
            ) from error
    credentials = tuple(principal.credential for principal in principals)
    if len(set(credentials)) != len(credentials):
        raise BootstrapConfigurationError(
            "static principal credentials must be unique"
        )
    return tuple(principals)


def _operations_application(
    config: CpkServerBootstrapConfiguration,
) -> CpkServerOperationsApplication:
    database_url = config.operations_database_url()
    _install_operations_schema(database_url)

    def unit_of_work() -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    lifecycle = RunLifecycleCommandService(
        unit_of_work,
        clock=_clock,
        id_factory=_id,
    )
    generated_secret_recorder = InMemoryGeneratedSecretRecorder()
    execution = ExecutionCoordinator(
        unit_of_work,
        lifecycle=lifecycle,
        adapter=_activity_adapter(config, unit_of_work, generated_secret_recorder),
        clock=_clock,
        id_factory=_id,
    )
    return CpkServerOperationsApplication(
        cpk_server_services(
            unit_of_work_factory=unit_of_work,
            planning=ActivityPlanningCommandService(
                unit_of_work,
                clock=_clock,
                id_factory=_id,
            ),
            workspaces=WorkspaceCommandService(
                unit_of_work,
                clock=_clock,
                id_factory=_id,
            ),
            products=ProductRegistrationService(unit_of_work),
            image_pull_authorities=ImagePullAuthorityRegistrationService(unit_of_work),
            runtime_authorities=RuntimeAuthorityRegistrationService(unit_of_work),
            ingress_authorities=IngressAuthorityRegistrationService(unit_of_work),
            desired_graphs=DesiredGraphCommandService(
                unit_of_work,
                clock=_clock,
                id_factory=_id,
            ),
            approval=ApprovalCommandService(
                unit_of_work,
                clock=_clock,
                id_factory=_id,
            ),
            admission=ExecutionAdmissionCommandService(
                unit_of_work,
                clock=_clock,
                id_factory=_id,
            ),
            lifecycle=lifecycle,
            operations=OperationCommandService(
                unit_of_work,
                clock=_clock,
                id_factory=_id,
            ),
            execution=execution,
            advancement=CurrentGraphAdvancementCommandService(
                unit_of_work,
                clock=_clock,
                id_factory=_id,
            ),
            gateway_probes=_gateway_probe_service(config, unit_of_work),
            clock=lambda: datetime.now(timezone.utc),
        )
    )


def _gateway_probe_service(config: CpkServerBootstrapConfiguration, unit_of_work):
    if config.gateway_probe_signer == "none":
        return None
    if config.gateway_probe_issuer is None or config.gateway_probe_key_id is None:
        raise AssertionError("gateway probe signer fields validated at bootstrap")
    return GatewayProbeCommandService(
        unit_of_work,
        dispatcher=_gateway_probe_dispatcher(config),
        issuer=config.gateway_probe_issuer,
        key_id=config.gateway_probe_key_id,
        epoch_clock=lambda: int(time.time()),
        clock=_clock,
        id_factory=_id,
    )


@dataclass(frozen=True, repr=False)
class _SignedGatewayProbeDispatcher:
    client_factory: object = field(repr=False)
    bounded_error_types: tuple[type[Exception], ...] = field(repr=False)
    succeeded_code: object = field(repr=False)
    rejected_code: object = field(repr=False)

    def dispatch(self, request: GatewayProbeDispatch) -> GatewayProbeDispatchResult:
        endpoint = request.gateway_endpoint
        if (
            endpoint.context is not EndpointContext.RUNTIME_PRIVATE
            or not isinstance(endpoint.address, LiteralEndpointMaterial)
        ):
            raise GatewayProbeDispatchError(
                "gateway endpoint is not an admitted private runtime address"
            )
        parsed = urlsplit(endpoint.address.value)
        if not parsed.scheme or not parsed.netloc:
            raise GatewayProbeDispatchError("gateway endpoint is malformed")
        try:
            client = self.client_factory(f"{parsed.scheme}://{parsed.netloc}")
            result = client.dispatch(
                request.grant,
                request.request,
                endpoint,
            )
        except self.bounded_error_types:
            raise GatewayProbeDispatchError(
                "gateway probe dispatch was rejected"
            ) from None
        if result.code is self.succeeded_code:
            status = GatewayProbeAttemptStatus.SUCCEEDED
        elif result.code is self.rejected_code:
            status = GatewayProbeAttemptStatus.REJECTED
        else:
            status = GatewayProbeAttemptStatus.FAILED
        return GatewayProbeDispatchResult(
            status=status,
            code=result.code.value,
            evidence=BoundedEvidence.from_mapping(result.evidence),
        )

    def __repr__(self) -> str:
        return "_SignedGatewayProbeDispatcher(<redacted>)"


def _gateway_probe_dispatcher(
    config: CpkServerBootstrapConfiguration,
    *,
    transport=None,
):
    if config.gateway_probe_signer != "ed25519":
        raise BootstrapConfigurationError("gateway probe signer is disabled")
    if config.gateway_probe_signing_key_reference is None:
        raise AssertionError("gateway probe signing key validated at bootstrap")
    secret_resolver = _product_secret_resolver(config)
    if secret_resolver is None:
        raise BootstrapConfigurationError(
            "gateway probe signer requires a product secret resolver"
        )
    try:
        from control_plane_kit_interpreters.probes import (
            Ed25519GatewayProbeSigner,
            GatewayProbeClientCode,
            GatewayProbeClientError,
            ProbeAddressPolicy,
            ProbeSecurityError,
            SignedGatewayProbeClient,
        )
    except ModuleNotFoundError as error:
        raise BootstrapConfigurationError(
            "CPK_GATEWAY_PROBE_SIGNER=ed25519 requires "
            "control-plane-kit-interpreters[gateway]"
        ) from error
    signer = Ed25519GatewayProbeSigner(
        config.gateway_probe_signing_key_reference,
        secret_resolver,
    )

    def client_factory(runtime_private_authority: str):
        return SignedGatewayProbeClient(
            signer=signer,
            address_policy=ProbeAddressPolicy(
                runtime_private_authorities=frozenset(
                    {runtime_private_authority}
                )
            ),
            transport=transport,
        )

    return _SignedGatewayProbeDispatcher(
        client_factory=client_factory,
        bounded_error_types=(
            GatewayProbeClientError,
            ProbeSecurityError,
        ),
        succeeded_code=GatewayProbeClientCode.SUCCEEDED,
        rejected_code=GatewayProbeClientCode.REJECTED,
    )


class _UnsupportedExecutionAdapter:
    """cpk-server wrapper default: operations exists, runtime effects do not."""

    def execute(self, context: ActivityRealizationContext) -> ActivityExecutionOutcome:
        return ActivityExecutionOutcome(
            EffectResultKind.UNSUPPORTED,
            failure=FailureEvidence(
                FailureCategory.UNSUPPORTED,
                "runtime-adapter-unavailable",
                "cpk-server runtime interpreter dispatch is disabled",
                BoundedEvidence.from_mapping(
                    {"activity_id": context.activity.activity_id.value}
                ),
            ),
        )


@dataclass(frozen=True)
class _CompositeExecutionAdapter:
    adapters: tuple[ActivityExecutionAdapter, ...]

    def execute(self, context: ActivityRealizationContext) -> ActivityExecutionOutcome:
        last_unsupported: ActivityExecutionOutcome | None = None
        for adapter in self.adapters:
            outcome = adapter.execute(context)
            if outcome.kind is EffectResultKind.UNSUPPORTED:
                last_unsupported = outcome
                continue
            return outcome
        assert last_unsupported is not None
        return last_unsupported


def _activity_adapter(
    config: CpkServerBootstrapConfiguration,
    unit_of_work,
    generated_secret_recorder: InMemoryGeneratedSecretRecorder,
) -> ActivityExecutionAdapter:
    runtime = _runtime_adapter(config, generated_secret_recorder)
    if not config.ingress_interpreters.enabled:
        return runtime
    ingress = IngressRealizationAdapter(
        unit_of_work,
        interpreters=_ingress_interpreters(config),
        generated_secret_recorder=generated_secret_recorder,
        clock=_clock,
    )
    return _CompositeExecutionAdapter((ingress, runtime))


def _runtime_adapter(
    config: CpkServerBootstrapConfiguration,
    generated_secret_recorder: InMemoryGeneratedSecretRecorder | None = None,
) -> _UnsupportedExecutionAdapter | RuntimeInterpreterDispatcher:
    if not config.runtime_dispatcher.enabled:
        return _UnsupportedExecutionAdapter()
    interpreters = {}
    for runtime_kind in config.runtime_dispatcher.runtime_kinds:
        if runtime_kind is RuntimeKind.DOCKER:
            interpreters[RuntimeKind.DOCKER] = _docker_runtime_interpreter(
                config,
                generated_secret_recorder,
            )
            continue
        raise BootstrapConfigurationError(
            f"no runtime interpreter provider is available for {runtime_kind.value!r}"
        )
    return RuntimeInterpreterDispatcher(interpreters)


def _docker_runtime_interpreter(
    config: CpkServerBootstrapConfiguration,
    generated_secret_recorder: InMemoryGeneratedSecretRecorder | None = None,
):
    try:
        from control_plane_kit_interpreters.docker import (
            DockerLocalAmbientClientConfig,
            DockerRuntimeInterpreter,
            DockerSdkClient,
        )
    except ModuleNotFoundError as error:
        raise BootstrapConfigurationError(
            "CPK_RUNTIME_INTERPRETERS=docker requires "
            "control-plane-kit-interpreters[docker]"
        ) from error
    return DockerRuntimeInterpreter(
        DockerSdkClient.from_authority(
            DockerLocalAmbientClientConfig(),
            connect_on_init=False,
        ),
        image_pull_credentials=_image_pull_credential_resolver(config),
        secret_resolver=_combined_product_secret_resolver(
            config,
            generated_secret_recorder,
        ),
    )


def _ingress_interpreters(config: CpkServerBootstrapConfiguration):
    interpreters = {}
    for provider_kind in config.ingress_interpreters.provider_kinds:
        if provider_kind is IngressAuthorityProviderKind.CLOUDFLARE:
            interpreters[provider_kind] = _cloudflare_ingress_interpreter(config)
            continue
        raise BootstrapConfigurationError(
            f"no ingress interpreter provider is available for {provider_kind.value!r}"
        )
    return interpreters


def _cloudflare_ingress_interpreter(config: CpkServerBootstrapConfiguration):
    try:
        from control_plane_kit_interpreters.cloudflare import (
            CloudflareNamedIngressInterpreter,
            CloudflareOwnedIngressResources,
            CloudflareZoneAuthority,
        )
    except ModuleNotFoundError as error:
        raise BootstrapConfigurationError(
            "CPK_INGRESS_INTERPRETERS=cloudflare requires "
            "control-plane-kit-interpreters[cloudflare]"
        ) from error

    class CloudflareIngressProvider:
        def __init__(self, secret_resolver) -> None:
            self._inner = CloudflareNamedIngressInterpreter(
                secret_resolver=secret_resolver,
            )

        def create(
            self,
            ingress,
            *,
            authority: CloudflareZoneIngressAuthority,
            allocation_name: str,
            origin_service_url: str,
        ):
            return self._inner.create(
                ingress,
                authority=CloudflareZoneAuthority(
                    account_id=authority.account_id,
                    zone_id=authority.zone_id,
                    zone_name=authority.zone_name,
                    api_token_ref=authority.api_token_ref,
                    allowed_hostname_pattern=authority.allowed_hostname_pattern,
                ),
                allocation_name=allocation_name,
                origin_service_url=origin_service_url,
            )

        def teardown(
            self,
            *,
            authority: CloudflareZoneIngressAuthority,
            resources: CloudflareOwnedIngressResource,
        ) -> None:
            return self._inner.teardown(
                authority=CloudflareZoneAuthority(
                    account_id=authority.account_id,
                    zone_id=authority.zone_id,
                    zone_name=authority.zone_name,
                    api_token_ref=authority.api_token_ref,
                    allowed_hostname_pattern=authority.allowed_hostname_pattern,
                ),
                resources=CloudflareOwnedIngressResources(
                    tunnel_id=resources.tunnel_id,
                    dns_record_id=resources.dns_record_id,
                    tunnel_name=resources.tunnel_name,
                    hostname=resources.hostname,
                ),
            )

        def __repr__(self) -> str:
            return "CloudflareIngressProvider(<redacted>)"

    return CloudflareIngressProvider(_product_secret_resolver(config))


def _image_pull_credential_resolver(config: CpkServerBootstrapConfiguration):
    if config.image_pull_credential_resolver == "none":
        return None
    if config.image_pull_credential_resolver != "docker-config":
        raise AssertionError("image pull resolver set validated at bootstrap")
    if config.docker_config_path is None and not config.docker_config_json:
        raise BootstrapConfigurationError(
            "CPK_IMAGE_PULL_CREDENTIAL_RESOLVER=docker-config requires "
            "DOCKER_CONFIG, CPK_DOCKER_AUTH_CONFIG, or CPK_DOCKER_AUTH_CONFIG_JSON"
        )
    try:
        from control_plane_kit_core.secrets import SecretProviderId, SecretValue
        from control_plane_kit_interpreters.secrets import (
            ImagePullCredentialDenied,
            ImagePullCredentialMissing,
            ImagePullCredentialResolved,
            ResolvedImagePullCredential,
        )
    except ModuleNotFoundError as error:
        raise BootstrapConfigurationError(
            "CPK_IMAGE_PULL_CREDENTIAL_RESOLVER=docker-config requires "
            "control-plane-kit-interpreters[docker]"
        ) from error

    class DockerConfigImagePullCredentialResolver:
        def __init__(
            self,
            *,
            config_path: str | None,
            config_json: str | None,
        ) -> None:
            self._config_path = config_path
            self._config_json = config_json

        def resolve(self, authority):
            reference = authority.credential_reference
            if (
                reference.provider_id != SecretProviderId("docker-config")
                or reference.path[0] != authority.registry
            ):
                return ImagePullCredentialDenied(reference)
            auths = self._auths()
            entry = auths.get(authority.registry)
            if not isinstance(entry, Mapping):
                return ImagePullCredentialMissing(reference)
            identitytoken = entry.get("identitytoken")
            if isinstance(identitytoken, str) and identitytoken:
                return ImagePullCredentialResolved(
                    ResolvedImagePullCredential(
                        identitytoken=SecretValue(identitytoken),
                    )
                )
            username = entry.get("username")
            password = entry.get("password")
            if isinstance(username, str) and isinstance(password, str) and password:
                return ImagePullCredentialResolved(
                    ResolvedImagePullCredential(
                        username=username,
                        password=SecretValue(password),
                    )
                )
            auth = entry.get("auth")
            if isinstance(auth, str) and auth:
                try:
                    decoded = base64.b64decode(auth).decode("utf-8")
                except Exception:
                    return ImagePullCredentialMissing(reference)
                username, separator, password = decoded.partition(":")
                if separator and username and password:
                    return ImagePullCredentialResolved(
                        ResolvedImagePullCredential(
                            username=username,
                            password=SecretValue(password),
                        )
                    )
            return ImagePullCredentialMissing(reference)

        def _auths(self) -> Mapping[str, object]:
            if self._config_json:
                try:
                    config_doc = json.loads(self._config_json)
                except json.JSONDecodeError:
                    return {}
            else:
                try:
                    with open(str(self._config_path), encoding="utf-8") as file:
                        config_doc = json.load(file)
                except OSError:
                    return {}
            if not isinstance(config_doc, Mapping):
                return {}
            auths = config_doc.get("auths")
            if not isinstance(auths, Mapping):
                return {}
            return auths

        def __repr__(self) -> str:
            return "DockerConfigImagePullCredentialResolver(<redacted>)"

    return DockerConfigImagePullCredentialResolver(
        config_path=config.docker_config_path,
        config_json=config.docker_config_json,
    )


def _product_secret_resolver(config: CpkServerBootstrapConfiguration):
    if config.product_secret_resolver == "none":
        return None
    if config.product_secret_resolver != "local-development":
        raise AssertionError("product secret resolver set validated at bootstrap")
    if config.product_secret_values_json is None:
        raise AssertionError("product secret values set validated at bootstrap")
    try:
        from control_plane_kit_core.secrets import (
            LocalDevelopmentSecretResolver,
            SecretProviderAuthority,
            SecretProviderId,
            SecretReference,
        )
    except ModuleNotFoundError as error:
        raise BootstrapConfigurationError(
            "CPK_PRODUCT_SECRET_RESOLVER=local-development requires "
            "control-plane-kit-core"
        ) from error
    try:
        raw_values = json.loads(config.product_secret_values_json)
    except json.JSONDecodeError as error:
        raise BootstrapConfigurationError(
            "CPK_PRODUCT_SECRET_VALUES_JSON must be a JSON object"
        ) from error
    if not isinstance(raw_values, Mapping) or not raw_values:
        raise BootstrapConfigurationError(
            "CPK_PRODUCT_SECRET_VALUES_JSON must be a non-empty JSON object"
        )
    values_by_provider: dict[SecretProviderId, dict[str, str]] = {}
    prefixes_by_provider: dict[SecretProviderId, set[tuple[str, ...]]] = {}
    for reference_id, secret_value in raw_values.items():
        if not isinstance(reference_id, str) or not isinstance(secret_value, str):
            raise BootstrapConfigurationError(
                "CPK_PRODUCT_SECRET_VALUES_JSON entries must map strings to strings"
            )
        reference = SecretReference(reference_id)
        prefixes_by_provider.setdefault(reference.provider_id, set()).add(reference.path)
        values_by_provider.setdefault(reference.provider_id, {})[
            reference.reference_id
        ] = secret_value
    if not values_by_provider:
        raise BootstrapConfigurationError(
            "CPK_PRODUCT_SECRET_VALUES_JSON must include at least one secret"
        )
    resolvers = tuple(
        LocalDevelopmentSecretResolver(
            SecretProviderAuthority(
                provider_id,
                tuple(sorted(prefixes_by_provider[provider_id])),
            ),
            values,
        )
        for provider_id, values in sorted(
            values_by_provider.items(),
            key=lambda item: item[0].value,
        )
    )
    if len(resolvers) == 1:
        return resolvers[0]
    return _CompositeSecretResolver(resolvers)


def _combined_product_secret_resolver(
    config: CpkServerBootstrapConfiguration,
    generated_secret_recorder: InMemoryGeneratedSecretRecorder | None,
):
    base = _product_secret_resolver(config)
    if generated_secret_recorder is None:
        return base
    if base is None:
        return _GeneratedSecretResolver(generated_secret_recorder)
    return _CompositeSecretResolver((base, _GeneratedSecretResolver(generated_secret_recorder)))


@dataclass(frozen=True)
class _GeneratedSecretResolver:
    generated_secret_recorder: InMemoryGeneratedSecretRecorder

    @property
    def authority(self):
        from control_plane_kit_core.secrets import SecretProviderAuthority, SecretProviderId

        return SecretProviderAuthority(SecretProviderId("generated"), (("ingress",),))

    def resolve(self, reference):
        from control_plane_kit_core.secrets import (
            SecretDenied,
            SecretMissing,
            SecretResolved,
        )

        if not self.authority.permits(reference):
            return SecretDenied(reference)
        try:
            value = self.generated_secret_recorder.resolve_generated_secret(reference)
        except Exception:
            return SecretMissing(reference)
        return SecretResolved(reference, value)

    def __repr__(self) -> str:
        return "GeneratedSecretResolver(<redacted>)"


@dataclass(frozen=True)
class _CompositeSecretResolver:
    resolvers: tuple[object, ...]

    @property
    def authority(self):
        return self.resolvers[0].authority

    def resolve(self, reference):
        from control_plane_kit_core.secrets import SecretDenied, SecretMissing

        denied = None
        for resolver in self.resolvers:
            result = resolver.resolve(reference)
            if not isinstance(result, (SecretDenied, SecretMissing)):
                return result
            if isinstance(result, SecretDenied):
                denied = result
        if denied is not None:
            return denied
        return SecretMissing(reference)

    def __repr__(self) -> str:
        return "CompositeSecretResolver(<redacted>)"


def _install_operations_schema(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        install_schema(connection)
        connection.commit()


def _clock() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _id() -> str:
    return str(uuid4())


def _json_response(status: int, payload: Mapping[str, object]) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=dict(payload),
    )


if __name__ == "__main__":
    raise SystemExit(main())
