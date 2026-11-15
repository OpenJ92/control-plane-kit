"""Public pinned-recipe preview/save laws through the Operations owners."""
from dataclasses import replace
import json
import unittest

from control_plane_kit_core.algebra import BlockSockets, DeploymentTopology, DockerRuntime, ProviderSocket, RequirementSocket
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.operations import (
    ControlPlaneServiceRole, HttpAuthScope, HttpMethod, HttpOperationSafety,
    operator_read_http_routes,
)
from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductDescriptorCodec,
    ProductCatalog, ProductIdentity, ProductInstanceConfiguration, ProductRuntimeContract, instantiate_catalog_product,
)
from control_plane_kit_core.topology import compile_topology
from control_plane_kit_core.types import Protocol
from control_plane_kit_core.verification import HttpCheck, VerificationContract
from control_plane_kit_operations.cpk_server import CpkServerReadService
from control_plane_kit_operations.products import InlineDescriptorSource

from draft_catalogue_fixture import CatalogueRequest, DraftCatalogueFixture, NOW, principal


PREVIEW = "read.product-topology-preview"
PRIVATE_PATH = "/fixed-verification-canary-not-for-public-read"


class ProductTopologyPreviewContractTests(unittest.TestCase):
    def test_preview_declares_bounded_read_only_post(self):
        matches = [route for route in operator_read_http_routes() if route.route_id == PREVIEW]
        self.assertEqual(len(matches), 1, "missing owner-declared pinned-product preview query")
        route = matches[0]
        self.assertEqual((route.method, route.service_role, route.auth_scope, route.safety),
            (HttpMethod.POST, ControlPlaneServiceRole.READS, HttpAuthScope.READ, HttpOperationSafety.READ_ONLY))
        self.assertEqual(route.path_template, "/workspaces/{workspace_id}/topology-authoring/preview")
        self.assertEqual(route.request_schema.max_bytes, 1048576)


class ProductTopologyAuthoringTests(DraftCatalogueFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.documents = {}
        self.references = {}
        for name in ("hello", "mux"):
            requirements = () if name == "hello" else (
                RequirementSocket("primary", Protocol.HTTP, ("MULTIPLEXER_PRIMARY_URL",)),)
            contract = ProductRuntimeContract(
                sockets=BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),), requirements=requirements),
                public_environment=(PublicStaticEnvironmentBinding("HELLO_MESSAGE", "hidden-default-canary"),) if name == "hello" else (),
                verification=VerificationContract(checks=(HttpCheck(check_id="live", provider_socket="internal",
                    path=PRIVATE_PATH, expected_body_sha256="f" * 64),)),
            )
            product = ContainerServerProduct(ProductIdentity("authoring-example", name, 1),
                OciImageReference("example.invalid", name, "sha256:" + "e" * 64), contract)
            document = ProductDescriptorCodec().encode_document(product)
            self.documents[name] = document
            with self.unit_of_work() as uow:
                registered = uow.stores.registered_products.register(workspace_id="workspace-a",
                    descriptor_document=document, source=InlineDescriptorSource(), imported_by="operator-a", imported_at=NOW)
                self.references[name] = registered.reference.descriptor()
                uow.commit()

    def recipe(self):
        def configuration(environment):
            return {"public_environment": environment, "configuration_artifacts": [], "secret_deliveries": [],
                "requirement_secret_deliveries": [], "runtime_authority_deliveries": []}
        return {"schema": "registered-product-topology.v1", "name": "hello-through-mux",
            "runtime": {"runtime_id": "demo-docker", "kind": "docker", "network_name": "demo-net",
                "authority_ref": None, "management": None,
                "lifecycle": {"ownership": "owned", "compute": "ephemeral", "data": []}},
            "nodes": [
                {"role_id": "hello", "product": self.references["hello"], "configuration": configuration([
                    {"kind": "public-static", "name": "HELLO_MESSAGE", "value": "Hello from the operator"}])},
                {"role_id": "mux", "product": self.references["mux"], "configuration": configuration([])}],
            "connections": [{"edge_id": "hello-to-mux", "provider_role": "hello", "provider_socket": "internal",
                "consumer_role": "mux", "requirement_socket": "primary", "protocol": {"transport": "tcp", "application": "http"}}]}

    def preview(self, recipe=None, *, surface="http", caller=None):
        payload = {"recipe": self.recipe() if recipe is None else recipe}
        path = {"workspace_id": "workspace-a"}
        if surface == "mcp":
            payload["workspace_id"] = "workspace-a"
            path = {}
        return CpkServerReadService(self.unit_of_work).handle(CatalogueRequest(
            surface, PREVIEW, ControlPlaneServiceRole.READS, path, payload, caller or principal()))

    def test_two_pinned_products_preview_without_durable_writes(self):
        before = self.catalogue_truth(), self.runtime_truth(), self.rows("cpk_operation_sessions")
        response = self.preview()
        self.assertEqual(response["workspace_id"], "workspace-a")
        self.assertEqual(response["graph_descriptor"]["name"], "hello-through-mux")
        self.assertEqual(set(response["graph_descriptor"]["nodes"]), {"hello", "mux"})
        for role in ("hello", "mux"):
            self.assertEqual(response["graph_descriptor"]["nodes"][role]["metadata"]["product_descriptor_digest"],
                self.references[role]["descriptor_sha256"])
        self.assertEqual(response, self.preview(surface="mcp"))
        self.assertEqual((self.catalogue_truth(), self.runtime_truth(), self.rows("cpk_operation_sessions")), before)
        self.assertNotIn(PRIVATE_PATH, json.dumps(response))
        self.assertNotIn("hidden-default-canary", json.dumps(response))

    def test_exact_saved_revision_hides_fixed_verification_at_both_locations(self):
        document = self.documents["hello"]
        block = instantiate_catalog_product(ProductCatalog.from_documents((document,)), document.product.identity,
            role_id="app", configuration=ProductInstanceConfiguration(public_environment=(
                PublicStaticEnvironmentBinding("HELLO_MESSAGE", "supplied public message"),)))
        graph = compile_topology(DeploymentTopology("saved", DockerRuntime(children=(block,))))
        saved = self.create(graph=graph)
        response = self.read("read.desired-topology-draft-revision", draft_id=saved.draft_id, revision=1)
        self.assertNotIn(PRIVATE_PATH, json.dumps(response))
        for value in (response["graph_descriptor"]["nodes"]["app"]["verification"],
                      response["graph_descriptor"]["nodes"]["app"]["block_spec"]["verification"]):
            self.assertEqual(value, {"checks": [{"kind": "http", "check_id": "live", "provider_socket": "internal"}]})
        with self.unit_of_work() as uow:
            self.assertIn(PRIVATE_PATH, json.dumps(uow.stores.graphs.get(saved.graph_id).graph_descriptor))
