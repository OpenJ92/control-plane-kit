"""Runnable FastAPI process for the cpk-server image."""

from __future__ import annotations

from dataclasses import dataclass, field
import base64
from datetime import datetime, timezone
import json
import os
from typing import Mapping
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import psycopg
import uvicorn
from control_plane_kit_core.operations.execution import EffectResultKind
from control_plane_kit_core.operations.lifecycle import FailureCategory
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations import (
    ActivityExecutionOutcome,
    ActivityPlanningCommandService,
    ActivityRealizationContext,
    ApprovalCommandService,
    BoundedEvidence,
    CpkServerOperationsApplication,
    CurrentGraphAdvancementCommandService,
    DesiredGraphCommandService,
    ExecutionAdmissionCommandService,
    ExecutionCoordinator,
    FailureEvidence,
    ImagePullAuthorityRegistrationService,
    OperationCommandService,
    ProductRegistrationService,
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
from .composition import (
    CpkServerCompositionError,
    CpkServerProcessConfiguration,
    create_cpk_server_composition,
)


class BootstrapConfigurationError(ValueError):
    """Raised when required process bootstrap configuration is missing."""


@dataclass(frozen=True, slots=True)
class CpkServerBootstrapConfiguration:
    mode: str
    control_auth_configured: bool
    port: int
    runtime_dispatcher: RuntimeDispatcherBootstrapConfiguration
    image_pull_credential_resolver: str
    product_secret_resolver: str
    product_secret_values_json: str | None = field(repr=False)
    docker_config_path: str | None
    store_endpoints: Mapping[str, str]

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "CpkServerBootstrapConfiguration":
        values = dict(os.environ if environ is None else environ)
        mode = _required(values, "CPK_SERVER_MODE")
        auth = _required(values, "CPK_CONTROL_AUTH_CONFIGURED")
        port_text = _required(values, "CPK_PORT")
        try:
            runtime_dispatcher = RuntimeDispatcherBootstrapConfiguration.from_process_value(
                _required(values, "CPK_RUNTIME_INTERPRETERS")
            )
        except RuntimeDispatcherBootstrapError as error:
            raise BootstrapConfigurationError(str(error)) from error
        image_pull_credential_resolver = values.get(
            "CPK_IMAGE_PULL_CREDENTIAL_RESOLVER",
            "none",
        )
        product_secret_resolver = values.get("CPK_PRODUCT_SECRET_RESOLVER", "none")
        product_secret_values_json = values.get("CPK_PRODUCT_SECRET_VALUES_JSON")
        docker_config_path = _docker_config_path(values)
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
        if auth.lower() not in {"true", "1", "yes"}:
            raise BootstrapConfigurationError(
                "CPK_CONTROL_AUTH_CONFIGURED must be true for hosted cpk-server"
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
        if (
            image_pull_credential_resolver == "docker-config"
            and RuntimeKind.DOCKER not in runtime_dispatcher.runtime_kinds
        ):
            raise BootstrapConfigurationError(
                "CPK_IMAGE_PULL_CREDENTIAL_RESOLVER=docker-config requires "
                "a Docker runtime interpreter"
            )
        if product_secret_resolver == "local-development":
            if RuntimeKind.DOCKER not in runtime_dispatcher.runtime_kinds:
                raise BootstrapConfigurationError(
                    "CPK_PRODUCT_SECRET_RESOLVER=local-development requires "
                    "a Docker runtime interpreter"
                )
            if product_secret_values_json is None or product_secret_values_json == "":
                raise BootstrapConfigurationError(
                    "CPK_PRODUCT_SECRET_RESOLVER=local-development requires "
                    "CPK_PRODUCT_SECRET_VALUES_JSON"
                )
        return cls(
            mode=mode,
            control_auth_configured=True,
            port=port,
            runtime_dispatcher=runtime_dispatcher,
            image_pull_credential_resolver=image_pull_credential_resolver,
            product_secret_resolver=product_secret_resolver,
            product_secret_values_json=product_secret_values_json,
            docker_config_path=docker_config_path,
            store_endpoints=store_endpoints,
        )

    def process_configuration(self) -> CpkServerProcessConfiguration:
        return CpkServerProcessConfiguration.execution_capable(token_configured=True)

    def operations_database_url(self) -> str:
        urls = set(self.store_endpoints.values())
        if len(urls) != 1:
            raise BootstrapConfigurationError(
                "current operations package requires all CPK_*_DATABASE_URL values "
                "to point at one instance database"
            )
        return next(iter(urls))


def create_app(config: CpkServerBootstrapConfiguration) -> FastAPI:
    """Create the hosted cpk-server FastAPI application."""

    composition = create_cpk_server_composition(config.process_configuration())
    application = CpkServerApplicationBoundary(_operations_application(config).services)
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
            headers=dict(request.headers),
            message=message,
        )
        return _json_response(response.status, response.body)

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def http(path: str, request: Request) -> JSONResponse:
        response = http_boundary.handle(
            method=request.method,
            path=request.url.path,
            headers=dict(request.headers),
            body=await request.body(),
        )
        return _json_response(response.status, response.body)

    return app


def main() -> int:
    try:
        config = CpkServerBootstrapConfiguration.from_environment()
    except (BootstrapConfigurationError, CpkServerCompositionError) as error:
        print(f"cpk-server bootstrap error: {error}", flush=True)
        return 2
    print(f"cpk-server listening on 0.0.0.0:{config.port}", flush=True)
    uvicorn.run(
        create_app(config),
        host="0.0.0.0",
        port=config.port,
        access_log=False,
    )
    return 0


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
    execution = ExecutionCoordinator(
        unit_of_work,
        lifecycle=lifecycle,
        adapter=_runtime_adapter(config),
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
            clock=lambda: datetime.now(timezone.utc),
        )
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


def _runtime_adapter(
    config: CpkServerBootstrapConfiguration,
) -> _UnsupportedExecutionAdapter | RuntimeInterpreterDispatcher:
    if not config.runtime_dispatcher.enabled:
        return _UnsupportedExecutionAdapter()
    interpreters = {}
    for runtime_kind in config.runtime_dispatcher.runtime_kinds:
        if runtime_kind is RuntimeKind.DOCKER:
            interpreters[RuntimeKind.DOCKER] = _docker_runtime_interpreter(config)
            continue
        raise BootstrapConfigurationError(
            f"no runtime interpreter provider is available for {runtime_kind.value!r}"
        )
    return RuntimeInterpreterDispatcher(interpreters)


def _docker_runtime_interpreter(config: CpkServerBootstrapConfiguration):
    try:
        from control_plane_kit_interpreters.docker import (
            DockerRuntimeInterpreter,
            DockerSdkClient,
        )
    except ModuleNotFoundError as error:
        raise BootstrapConfigurationError(
            "CPK_RUNTIME_INTERPRETERS=docker requires "
            "control-plane-kit-interpreters[docker]"
        ) from error
    return DockerRuntimeInterpreter(
        DockerSdkClient(),
        image_pull_credentials=_image_pull_credential_resolver(config),
        secret_resolver=_product_secret_resolver(config),
    )


def _image_pull_credential_resolver(config: CpkServerBootstrapConfiguration):
    if config.image_pull_credential_resolver == "none":
        return None
    if config.image_pull_credential_resolver != "docker-config":
        raise AssertionError("image pull resolver set validated at bootstrap")
    if config.docker_config_path is None:
        raise BootstrapConfigurationError(
            "CPK_IMAGE_PULL_CREDENTIAL_RESOLVER=docker-config requires "
            "DOCKER_CONFIG or CPK_DOCKER_AUTH_CONFIG"
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
        def __init__(self, config_path: str) -> None:
            self._config_path = config_path

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
            try:
                with open(self._config_path, encoding="utf-8") as file:
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

    return DockerConfigImagePullCredentialResolver(config.docker_config_path)


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
    values: dict[str, str] = {}
    provider_id: SecretProviderId | None = None
    allowed_prefixes: set[tuple[str, ...]] = set()
    for reference_id, secret_value in raw_values.items():
        if not isinstance(reference_id, str) or not isinstance(secret_value, str):
            raise BootstrapConfigurationError(
                "CPK_PRODUCT_SECRET_VALUES_JSON entries must map strings to strings"
            )
        reference = SecretReference(reference_id)
        if provider_id is None:
            provider_id = reference.provider_id
        elif reference.provider_id != provider_id:
            raise BootstrapConfigurationError(
                "CPK_PRODUCT_SECRET_VALUES_JSON must use one secret provider"
            )
        allowed_prefixes.add(reference.path)
        values[reference.reference_id] = secret_value
    if provider_id is None:
        raise BootstrapConfigurationError(
            "CPK_PRODUCT_SECRET_VALUES_JSON must include at least one secret"
        )
    return LocalDevelopmentSecretResolver(
        SecretProviderAuthority(provider_id, tuple(sorted(allowed_prefixes))),
        values,
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
