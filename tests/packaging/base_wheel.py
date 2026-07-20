"""Acceptance proof for the installed base wheel outside the source tree."""

from __future__ import annotations

from importlib.util import find_spec
import importlib
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
    ProductCatalog,
    ProductDeclaration,
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
if WebhookDeliveryService.__module__ != "control_plane_kit.operations.webhook.service":
    raise AssertionError("webhook service did not load from canonical operations")

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
for retired in ("http", "postgres", "protocols", "service", "unit_of_work"):
    if find_spec(f"control_plane_kit.webhook.{retired}") is not None:
        raise AssertionError(f"installed base wheel retained webhook.{retired}")
transitional_webhook = importlib.import_module("control_plane_kit.webhook")
if transitional_webhook.__all__:
    raise AssertionError("transitional webhook package retained eager exports")

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
