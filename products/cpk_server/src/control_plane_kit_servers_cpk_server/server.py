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
    control_auth_configured: bool
    port: int
    runtime_dispatcher: RuntimeDispatcherBootstrapConfiguration
    ingress_interpreters: IngressInterpreterBootstrapConfiguration
    image_pull_credential_resolver: str
    product_secret_resolver: str
    product_secret_values_json: str | None = field(repr=False)
    docker_config_path: str | None
    docker_config_json: str | None = field(repr=False)
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
        ):
            raise BootstrapConfigurationError(
                "CPK_PRODUCT_SECRET_RESOLVER=local-development requires "
                "a Docker runtime interpreter or Cloudflare ingress interpreter"
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
            control_auth_configured=True,
            port=port,
            runtime_dispatcher=runtime_dispatcher,
            ingress_interpreters=ingress_interpreters,
            image_pull_credential_resolver=image_pull_credential_resolver,
            product_secret_resolver=product_secret_resolver,
            product_secret_values_json=product_secret_values_json,
            docker_config_path=docker_config_path,
            docker_config_json=docker_config_json,
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
