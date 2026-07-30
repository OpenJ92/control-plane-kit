"""Runnable FastAPI process for the cpk-server image."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
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
    SecretProviderRegistrationService,
    SecretUseAuthorizationService,
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
    product_material_resolver: str
    product_secret_values_json: str | None = field(repr=False)
    material_provider_routes_json: str | None = field(repr=False)
    material_provider_bootstrap_files_json: str | None = field(repr=False)
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
        product_material_resolver = values.get(
            "CPK_PRODUCT_MATERIAL_RESOLVER",
            "none",
        )
        product_secret_values_json = values.get("CPK_PRODUCT_SECRET_VALUES_JSON")
        material_provider_routes_json = values.get(
            "CPK_MATERIAL_PROVIDER_ROUTES_JSON"
        )
        material_provider_bootstrap_files_json = values.get(
            "CPK_MATERIAL_PROVIDER_BOOTSTRAP_FILES_JSON"
        )
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
        if any(
            name in values
            for name in (
                "CPK_IMAGE_PULL_CREDENTIAL_RESOLVER",
                "DOCKER_CONFIG",
                "CPK_DOCKER_AUTH_CONFIG",
                "CPK_DOCKER_AUTH_CONFIG_JSON",
            )
        ):
            raise BootstrapConfigurationError(
                "legacy Docker credential bootstrap is unavailable"
            )
        if product_material_resolver not in {
            "none",
            "provider",
            "local-development",
        }:
            raise BootstrapConfigurationError(
                "CPK_PRODUCT_MATERIAL_RESOLVER must be one of: "
                "none, provider, local-development"
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
            if product_material_resolver != "provider":
                raise BootstrapConfigurationError(
                    "CPK_GATEWAY_PROBE_SIGNER=ed25519 requires provider-backed "
                    "secret resolution"
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
            product_material_resolver == "local-development"
            and RuntimeKind.DOCKER not in runtime_dispatcher.runtime_kinds
            and IngressAuthorityProviderKind.CLOUDFLARE
            not in ingress_interpreters.provider_kinds
            and gateway_probe_signer == "none"
        ):
            raise BootstrapConfigurationError(
                "CPK_PRODUCT_MATERIAL_RESOLVER=local-development requires "
                "a Docker runtime interpreter, Cloudflare ingress interpreter, "
                "or gateway probe signer"
            )
        if product_material_resolver == "local-development":
            if product_secret_values_json is None or product_secret_values_json == "":
                raise BootstrapConfigurationError(
                    "CPK_PRODUCT_MATERIAL_RESOLVER=local-development requires "
                    "CPK_PRODUCT_SECRET_VALUES_JSON"
                )
            if (
                material_provider_routes_json is not None
                or material_provider_bootstrap_files_json is not None
            ):
                raise BootstrapConfigurationError(
                    "local-development secret resolution cannot use provider bootstrap"
                )
        elif product_secret_values_json is not None:
            raise BootstrapConfigurationError(
                "CPK_PRODUCT_SECRET_VALUES_JSON requires "
                "CPK_PRODUCT_MATERIAL_RESOLVER=local-development"
            )
        if product_material_resolver == "provider":
            if (
                not material_provider_routes_json
                or not material_provider_bootstrap_files_json
            ):
                raise BootstrapConfigurationError(
                    "provider-backed secret resolution requires endpoint and "
                    "credential-file registries"
                )
            _validated_bootstrap_mapping_json(material_provider_routes_json)
            _validated_bootstrap_mapping_json(
                material_provider_bootstrap_files_json
            )
        elif (
            material_provider_routes_json is not None
            or material_provider_bootstrap_files_json is not None
        ):
            raise BootstrapConfigurationError(
                "secret provider bootstrap requires "
                "CPK_PRODUCT_MATERIAL_RESOLVER=provider"
            )
        if (
            IngressAuthorityProviderKind.CLOUDFLARE
            in ingress_interpreters.provider_kinds
            and product_material_resolver != "provider"
        ):
            raise BootstrapConfigurationError(
                "CPK_INGRESS_INTERPRETERS=cloudflare requires provider-backed "
                "secret resolution"
            )
        return cls(
            mode=mode,
            control_auth_verifier=control_auth_verifier,
            control_auth_static_credential=control_auth_static_credential,
            control_auth_static_principals=control_auth_static_principals,
            port=port,
            runtime_dispatcher=runtime_dispatcher,
            ingress_interpreters=ingress_interpreters,
            product_material_resolver=product_material_resolver,
            product_secret_values_json=product_secret_values_json,
            material_provider_routes_json=material_provider_routes_json,
            material_provider_bootstrap_files_json=(
                material_provider_bootstrap_files_json
            ),
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
                "material_provider": {
                    "none": "disabled",
                    "provider": "configured",
                    "local-development": "development-fixture",
                }[config.product_material_resolver],
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


def _validated_bootstrap_mapping_json(value: str) -> dict[str, str]:
    if not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= 65_536:
        raise BootstrapConfigurationError(
            "secret provider bootstrap registry is malformed"
        )
    try:
        decoded = json.loads(value, object_pairs_hook=_unique_json_mapping)
    except (TypeError, ValueError):
        raise BootstrapConfigurationError(
            "secret provider bootstrap registry is malformed"
        ) from None
    if (
        not isinstance(decoded, dict)
        or not 1 <= len(decoded) <= 64
        or not all(
            isinstance(key, str)
            and isinstance(item, str)
            and key
            and item
            and len(key.encode("utf-8")) <= 2_048
            and len(item.encode("utf-8")) <= 4_096
            for key, item in decoded.items()
        )
    ):
        raise BootstrapConfigurationError(
            "secret provider bootstrap registry is malformed"
        )
    return decoded


def _unique_json_mapping(pairs: list[tuple[str, object]]) -> dict[str, object]:
    values: dict[str, object] = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("duplicate key")
        values[key] = value
    return values


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
    secret_use_authorizer = SecretUseAuthorizationService(unit_of_work)
    secret_provider = _secret_provider_composition(config)
    execution = ExecutionCoordinator(
        unit_of_work,
        lifecycle=lifecycle,
        adapter=_activity_adapter(
            config,
            unit_of_work,
            secret_use_authorizer,
            secret_provider,
        ),
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
            secret_providers=SecretProviderRegistrationService(unit_of_work),
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
            gateway_probes=_gateway_probe_service(
                config,
                unit_of_work,
                secret_use_authorizer,
                secret_provider,
            ),
            clock=lambda: datetime.now(timezone.utc),
        )
    )


def _gateway_probe_service(
    config: CpkServerBootstrapConfiguration,
    unit_of_work,
    secret_use_authorizer,
    secret_provider: "_SecretProviderComposition",
):
    if config.gateway_probe_signer == "none":
        return None
    if (
        config.gateway_probe_issuer is None
        or config.gateway_probe_key_id is None
        or config.gateway_probe_signing_key_reference is None
    ):
        raise AssertionError("gateway probe signer fields validated at bootstrap")
    return GatewayProbeCommandService(
        unit_of_work,
        dispatcher=_gateway_probe_dispatcher(
            config,
            secret_provider=secret_provider,
        ),
        issuer=config.gateway_probe_issuer,
        key_id=config.gateway_probe_key_id,
        signing_key_reference=config.gateway_probe_signing_key_reference,
        secret_use_authorizer=secret_use_authorizer,
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
                request.secret_resolution_grant,
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
    secret_provider: "_SecretProviderComposition | None" = None,
    transport=None,
):
    if config.gateway_probe_signer != "ed25519":
        raise BootstrapConfigurationError("gateway probe signer is disabled")
    if config.gateway_probe_signing_key_reference is None:
        raise AssertionError("gateway probe signing key validated at bootstrap")
    provider = secret_provider or _secret_provider_composition(
        config,
        transport=transport,
    )
    if provider.authorized_resolver is None:
        raise BootstrapConfigurationError(
            "gateway probe signer requires provider-backed secret resolution"
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
        provider.authorized_resolver,
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
    secret_use_authorizer,
    secret_provider: "_SecretProviderComposition",
) -> ActivityExecutionAdapter:
    runtime = _runtime_adapter(
        config,
        secret_use_authorizer=secret_use_authorizer,
        secret_provider=secret_provider,
    )
    if not config.ingress_interpreters.enabled:
        return runtime
    ingress = IngressRealizationAdapter(
        unit_of_work,
        interpreters=_ingress_interpreters(config, secret_provider),
        clock=_clock,
        secret_use_authorizer=secret_use_authorizer,
    )
    return _CompositeExecutionAdapter((ingress, runtime))


def _runtime_adapter(
    config: CpkServerBootstrapConfiguration,
    *,
    secret_use_authorizer=None,
    secret_provider: "_SecretProviderComposition | None" = None,
) -> _UnsupportedExecutionAdapter | RuntimeInterpreterDispatcher:
    if not config.runtime_dispatcher.enabled:
        return _UnsupportedExecutionAdapter()
    provider = secret_provider or _secret_provider_composition(config)
    interpreters = {}
    for runtime_kind in config.runtime_dispatcher.runtime_kinds:
        if runtime_kind is RuntimeKind.DOCKER:
            interpreters[RuntimeKind.DOCKER] = _docker_runtime_interpreter(
                config,
                provider,
            )
            continue
        raise BootstrapConfigurationError(
            f"no runtime interpreter provider is available for {runtime_kind.value!r}"
        )
    return RuntimeInterpreterDispatcher(
        interpreters,
        secret_use_authorizer=secret_use_authorizer,
    )


def _docker_runtime_interpreter(
    config: CpkServerBootstrapConfiguration,
    secret_provider: "_SecretProviderComposition | None" = None,
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
    provider = secret_provider or _secret_provider_composition(config)
    return DockerRuntimeInterpreter(
        DockerSdkClient.from_authority(
            DockerLocalAmbientClientConfig(),
            connect_on_init=False,
        ),
        authorized_secret_resolver=provider.authorized_resolver,
    )


def _ingress_interpreters(
    config: CpkServerBootstrapConfiguration,
    secret_provider: "_SecretProviderComposition | None" = None,
):
    provider = secret_provider or _secret_provider_composition(config)
    interpreters = {}
    for provider_kind in config.ingress_interpreters.provider_kinds:
        if provider_kind is IngressAuthorityProviderKind.CLOUDFLARE:
            interpreters[provider_kind] = _cloudflare_ingress_interpreter(
                config,
                provider,
            )
            continue
        raise BootstrapConfigurationError(
            f"no ingress interpreter provider is available for {provider_kind.value!r}"
        )
    return interpreters


def _cloudflare_ingress_interpreter(
    config: CpkServerBootstrapConfiguration,
    secret_provider: "_SecretProviderComposition | None" = None,
):
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

    provider = secret_provider or _secret_provider_composition(config)
    if (
        provider.authorized_resolver is None
        or provider.secret_custodian is None
    ):
        raise BootstrapConfigurationError(
            "Cloudflare ingress requires provider-backed secret resolution"
        )

    class CloudflareIngressProvider:
        def __init__(self) -> None:
            self._inner = CloudflareNamedIngressInterpreter(
                authorized_secret_resolver=provider.authorized_resolver,
                secret_custodian=provider.secret_custodian,
            )

        def create(
            self,
            ingress,
            *,
            authority: CloudflareZoneIngressAuthority,
            allocation_name: str,
            origin_service_url: str,
            secret_resolution_grant,
            secret_custody_grant,
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
                secret_resolution_grant=secret_resolution_grant,
                secret_custody_grant=secret_custody_grant,
            )

        def teardown(
            self,
            *,
            authority: CloudflareZoneIngressAuthority,
            resources: CloudflareOwnedIngressResource,
            secret_resolution_grant,
            secret_custody_grant,
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
                secret_resolution_grant=secret_resolution_grant,
                secret_custody_grant=secret_custody_grant,
            )

        def __repr__(self) -> str:
            return "CloudflareIngressProvider(<redacted>)"

    return CloudflareIngressProvider()


@dataclass(frozen=True, repr=False)
class _SecretProviderComposition:
    authorized_resolver: object | None = field(default=None, repr=False)
    secret_custodian: object | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return (
            "SecretProviderComposition("
            f"configured={self.authorized_resolver is not None})"
        )


@dataclass(frozen=True, repr=False)
class _LocalDevelopmentAuthorizedSecretResolver:
    values: Mapping[str, str] = field(repr=False)

    def resolve(self, grant):
        from control_plane_kit_core.secrets import (
            SecretMissing,
            SecretResolutionGrant,
            SecretResolved,
            SecretValue,
        )

        if not isinstance(grant, SecretResolutionGrant):
            raise TypeError(
                "local development secret resolution requires SecretResolutionGrant"
            )
        value = self.values.get(grant.reference.reference_id)
        if value is None:
            return SecretMissing(grant.reference)
        return SecretResolved(grant.reference, SecretValue(value))

    def __repr__(self) -> str:
        return "LocalDevelopmentAuthorizedSecretResolver(<redacted>)"


def _secret_provider_composition(
    config: CpkServerBootstrapConfiguration,
    *,
    transport=None,
) -> _SecretProviderComposition:
    if config.product_material_resolver == "none":
        return _SecretProviderComposition()
    if config.product_material_resolver == "local-development":
        if config.product_secret_values_json is None:
            raise AssertionError("local development secret values validated")
        values = _validated_bootstrap_mapping_json(
            config.product_secret_values_json
        )
        try:
            for reference_id in values:
                SecretReference(reference_id)
        except SecretResolutionError:
            raise BootstrapConfigurationError(
                "local development secret registry is malformed"
            ) from None
        return _SecretProviderComposition(
            authorized_resolver=_LocalDevelopmentAuthorizedSecretResolver(
                values
            )
        )
    if config.product_material_resolver != "provider":
        raise AssertionError("product material resolver set validated at bootstrap")
    if (
        config.material_provider_routes_json is None
        or config.material_provider_bootstrap_files_json is None
    ):
        raise AssertionError("provider bootstrap registries validated")
    try:
        from control_plane_kit_core.secrets import (
            SecretProviderEndpointReference,
        )
        from control_plane_kit_interpreters.secret_provider import (
            ControlPlaneKitSecretsCustodian,
            ControlPlaneKitSecretsResolver,
            SecretProviderBootstrapError,
            SecretProviderBootstrapRegistry,
        )
    except ModuleNotFoundError:
        raise BootstrapConfigurationError(
            "secret provider integration is unavailable"
        ) from None
    try:
        registry = SecretProviderBootstrapRegistry(
            endpoints={
                SecretProviderEndpointReference(reference_id): endpoint
                for reference_id, endpoint in _validated_bootstrap_mapping_json(
                    config.material_provider_routes_json
                ).items()
            },
            credential_files={
                SecretReference(reference_id): Path(path)
                for reference_id, path in _validated_bootstrap_mapping_json(
                    config.material_provider_bootstrap_files_json
                ).items()
            },
        )
    except (
        SecretProviderBootstrapError,
        SecretResolutionError,
        TypeError,
        ValueError,
    ):
        raise BootstrapConfigurationError(
            "secret provider bootstrap configuration is unavailable"
        ) from None
    return _SecretProviderComposition(
        authorized_resolver=ControlPlaneKitSecretsResolver(
            registry,
            transport=transport,
        ),
        secret_custodian=ControlPlaneKitSecretsCustodian(
            registry,
            transport=transport,
        ),
    )


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
