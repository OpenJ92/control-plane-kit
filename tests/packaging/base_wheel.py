"""Acceptance proof for the installed base wheel outside the source tree."""

from __future__ import annotations

from importlib.util import find_spec
import importlib
from importlib import resources
import sys


OPTIONAL_DISTRIBUTIONS = ("fastapi", "httpx", "psycopg", "uvicorn")
OPTIONAL_MODULE_PREFIXES = (
    "control_plane_kit.adapters",
    "control_plane_kit.docker_runtime",
    "control_plane_kit.servers",
    "control_plane_kit.webhook",
)


for dependency in OPTIONAL_DISTRIBUTIONS:
    if find_spec(dependency) is not None:
        raise AssertionError(f"base wheel unexpectedly installed {dependency}")

from control_plane_kit import (  # noqa: E402
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DeploymentGraph,
    DeploymentRecipe,
    DockerRuntime,
    GraphDescriptorCodec,
    PlanOnlyImplementation,
    compile_activity_plan,
    compile_recipe,
    diff_graphs,
    validate_graph,
)


if any(name.startswith(OPTIONAL_MODULE_PREFIXES) for name in sys.modules):
    raise AssertionError("root import eagerly loaded an optional package surface")
if any(name.startswith("control_plane_kit.domains") for name in sys.modules):
    raise AssertionError("root import eagerly loaded a domain language")
if any(name.startswith("control_plane_kit.products") for name in sys.modules):
    raise AssertionError("root import eagerly loaded a product catalog")
if "jinja2" in sys.modules:
    raise AssertionError("root import eagerly loaded the rendering interpreter")

from control_plane_kit.interpreters import ConfigurationTemplate  # noqa: E402
from control_plane_kit.products.servers import (  # noqa: E402
    COREDNS_PRODUCT,
    HTTP_AUTH_GATEWAY_PRODUCT,
    AuthGatewayPolicy,
    AuthenticationMechanism,
    GatewayMethod,
    ProductCatalog,
    ProductDeclaration,
    RouteAuthorizationPolicy,
    WEBHOOK_DELIVERY_PRODUCT,
    http_auth_gateway_block,
    webhook_delivery_block,
)
from control_plane_kit.products.servers.support.command_rendering import (  # noqa: E402
    render_python_command,
)
from control_plane_kit.operations.webhook import WebhookDeliveryService  # noqa: E402

if ConfigurationTemplate.__module__ != (
    "control_plane_kit.interpreters.configuration_rendering"
):
    raise AssertionError("rendering did not load from its canonical interpreter home")
if ProductDeclaration.__module__ != "control_plane_kit.products.servers.catalog":
    raise AssertionError("product declarations did not load from the canonical catalog")
if ProductCatalog.__module__ != "control_plane_kit.products.servers.catalog":
    raise AssertionError("product catalog did not load from the canonical catalog")
if webhook_delivery_block.__module__ != (
    "control_plane_kit.products.servers.webhook_delivery"
):
    raise AssertionError("webhook block did not load from its canonical product home")
if WEBHOOK_DELIVERY_PRODUCT.block.spec != webhook_delivery_block().spec:
    raise AssertionError("webhook declaration and block constructor disagree")
if http_auth_gateway_block.__module__ != (
    "control_plane_kit.products.servers.http_auth_gateway"
):
    raise AssertionError("auth gateway block did not load from its canonical product home")
auth_gateway = http_auth_gateway_block(
    policy=AuthGatewayPolicy(
        AuthenticationMechanism.API_KEY,
        (RouteAuthorizationPolicy("/", (GatewayMethod.GET,)),),
    ),
)
if HTTP_AUTH_GATEWAY_PRODUCT.block.spec != auth_gateway.spec:
    raise AssertionError("auth gateway declaration and block constructor disagree")
if HTTP_AUTH_GATEWAY_PRODUCT.maturity.value != "test-only":
    raise AssertionError("package auth gateway must remain an explicit test-only fixture")
if COREDNS_PRODUCT.block.implementation.image != "coredns/coredns:1.14.6":
    raise AssertionError("base wheel lost the exact CoreDNS product declaration")
if any(find_spec(dependency) is not None for dependency in OPTIONAL_DISTRIBUTIONS):
    raise AssertionError("pure webhook product import installed an optional dependency")
if WebhookDeliveryService.__module__ != "control_plane_kit.operations.webhook.service":
    raise AssertionError("webhook service did not load from canonical operations")

template = resources.files(
    "control_plane_kit.products.servers.support"
).joinpath("templates", "http_forwarder.py.j2")
if not template.is_file():
    raise AssertionError("base wheel is missing packaged server-support templates")
rendered_command = render_python_command(
    "http_forwarder.py.j2",
    target_env="BASE_WHEEL_TARGET_URL",
    port=8080,
)
if rendered_command[:2] != ("python", "-c"):
    raise AssertionError("installed server-support template did not render a command")
if "BASE_WHEEL_TARGET_URL" not in rendered_command[2]:
    raise AssertionError("installed server-support template lost typed render input")

coredns_template = resources.files(
    "control_plane_kit.products.servers"
).joinpath("templates", "coredns.Corefile.j2")
if not coredns_template.is_file():
    raise AssertionError("base wheel is missing the product-owned CoreDNS template")

from control_plane_kit.domains.discovery import DiscoveryIdentity  # noqa: E402
from control_plane_kit.domains.idempotency import IdempotencyIdentity  # noqa: E402
from control_plane_kit.domains.load_generation import LoadMethod  # noqa: E402
from control_plane_kit.domains.webhook import (  # noqa: E402
    WebhookAddressPolicy,
    WebhookDeliveryIdentity,
)

if not all(
    value.__module__.startswith("control_plane_kit.domains.")
    for value in (
        DiscoveryIdentity,
        IdempotencyIdentity,
        LoadMethod,
        WebhookDeliveryIdentity,
    )
):
    raise AssertionError("domain values did not load from canonical package entrances")
if WebhookAddressPolicy.__module__ != "control_plane_kit.domains.webhook.language":
    raise AssertionError("webhook endpoint authority did not load from its domain")

application = ApplicationBlock(
    spec=BlockSpec("base-wheel-app", "Base wheel application"),
    implementation=PlanOnlyImplementation("base-wheel"),
    sockets=BlockSockets(),
)
graph = compile_recipe(
    DeploymentRecipe(
        "base-wheel-proof",
        DockerRuntime(runtime_id="base-wheel-runtime", children=(application,)),
    )
)
validated = validate_graph(graph)
if validated.graph.node("base-wheel-app").node_id != "base-wheel-app":
    raise AssertionError("installed base wheel did not compile the expected graph")
codec = GraphDescriptorCodec()
reconstructed = codec.decode(codec.encode(validated.graph))
if reconstructed.descriptor() != validated.graph.descriptor():
    raise AssertionError("installed base wheel did not round-trip the expected graph")
empty = validate_graph(DeploymentGraph(graph.name))
plan = compile_activity_plan(diff_graphs(empty, validated))
if not plan.activities:
    raise AssertionError("installed base wheel did not compile a nonempty ActivityPlan")
if find_spec("control_plane_kit.topology") is not None:
    raise AssertionError("installed base wheel retained the retired topology package")
if find_spec("control_plane_kit.planning") is not None:
    raise AssertionError("installed base wheel retained the retired planning package")
if find_spec("control_plane_kit.configuration_rendering") is not None:
    raise AssertionError("installed base wheel retained the retired rendering module")
if find_spec("control_plane_kit.cli") is not None:
    raise AssertionError("installed base wheel retained the retired CLI module")
if find_spec("control_plane_kit.entrypoints.cli") is None:
    raise AssertionError("installed base wheel is missing the canonical CLI entrypoint")
for retired in ("app", "http", "postgres", "protocols", "service", "unit_of_work"):
    try:
        retired_spec = find_spec(f"control_plane_kit.webhook.{retired}")
    except ModuleNotFoundError:
        retired_spec = None
    if retired_spec is not None:
        raise AssertionError(f"installed base wheel retained webhook.{retired}")
for retired in ("control_plane_kit.webhook", "control_plane_kit.webhook_server"):
    if find_spec(retired) is not None:
        raise AssertionError(f"installed base wheel retained {retired}")
try:
    retired_auth_gateway = find_spec("control_plane_kit.servers.http_auth_gateway")
except ModuleNotFoundError:
    retired_auth_gateway = None
if retired_auth_gateway is not None:
    raise AssertionError("installed base wheel retained the legacy auth gateway home")
try:
    retired_coredns = find_spec("control_plane_kit.servers.coredns")
except ModuleNotFoundError:
    retired_coredns = None
if retired_coredns is not None:
    raise AssertionError("installed base wheel retained the legacy CoreDNS home")

for module in (
    "control_plane_kit.adapters",
    "control_plane_kit.servers",
):
    try:
        importlib.import_module(module)
    except ModuleNotFoundError as error:
        if "control-plane-kit[http]" not in str(error):
            raise AssertionError(f"{module} did not give actionable HTTP-extra guidance") from error
    else:
        raise AssertionError(f"{module} imported without its declared HTTP extra")

try:
    importlib.import_module("control_plane_kit.interpreters.webhook_http")
except ModuleNotFoundError as error:
    if "control-plane-kit[http]" not in str(error):
        raise AssertionError("webhook HTTP interpreter lacks extra guidance") from error
else:
    raise AssertionError("webhook HTTP interpreter imported without its HTTP extra")

print("base wheel acceptance passed")
