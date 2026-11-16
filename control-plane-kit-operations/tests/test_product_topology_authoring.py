"""Public pinned-recipe preview/save laws through the Operations owners."""
from dataclasses import replace
from copy import deepcopy
from hashlib import sha256
import json
import unittest

import psycopg

from control_plane_kit_core.algebra import BlockSockets, DeploymentTopology, DockerRuntime, ProviderSocket, RequirementSocket
from control_plane_kit_core.delegation_keys import DelegationKeyAlgorithm, DelegationKeyPurpose, DelegationPublicKey
from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_operations.delegation_signing_keys import RegisterDelegationSigningKeyCommand
from tests.test_delegation_signing_keys import PUBLIC_KEY_A
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.operations import (
    ControlPlaneServiceRole, HttpAuthScope, HttpMethod, HttpOperationSafety,
    operator_read_http_routes,
)
from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductDescriptorCodec,
    ProductCatalog, ProductIdentity, ProductReferenceCodec, ProductInstanceConfiguration, ProductRuntimeContract, ProductRuntimeContractCodec, ProviderRuntimePort, instantiate_catalog_product,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, compile_topology
from control_plane_kit_core.types import Protocol
from control_plane_kit_core.verification import HttpCheck, VerificationContract
from control_plane_kit_operations.cpk_server import CpkServerApplicationError, CpkServerPlanningService, CpkServerReadService
from control_plane_kit_operations.product_topology_authoring import (
    COMMITMENT_PROFILE, ProductTopologyAuthoringService, ProductTopologyRecipe,
    public_product_topology_graph, public_proposal_commitment,
)
from control_plane_kit_core.public_ingress import IngressAuthorityReference
from control_plane_kit_operations.ingress_authorities import CloudflareZoneIngressAuthorityCodec
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.products import InlineDescriptorSource
from control_plane_kit_operations.desired_topology_drafts import DesiredTopologyDraftError, bounded_draft_graph_descriptor
from control_plane_kit_operations.read_services.workspace_graph import _redact_graph_descriptor
from control_plane_kit_operations.workflows import CloseOperationSession, IdempotencyKey, OperationCommandService

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
                provider_ports=(ProviderRuntimePort("internal", 8000),),
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

    def ingress_extension_fixture(self):
        workspace = "workspace-a"
        first_recipe = self.preview()
        owner = ProductTopologyAuthoringService(self.unit_of_work)
        graph = owner.prepare_saved_graph(principal().command_context(workspace),
            first_recipe["recipe"], first_recipe["proposal_commitment"])
        first = self.create(graph=graph)
        second = self.catalogue().execute(self.revise_command(first, graph=graph))
        self.assertEqual(second.revision, 2)
        self.assertNotEqual(first.graph_id, second.graph_id)
        self.assertEqual(graph.node("mux").socket_environment[0].edge_id, "hello-to-mux")

        # A disposable managed product has an unfilled read-only control slot.
        # The portable release itself is exercised in the Servers cohort.
        contract = ProductRuntimeContract(sockets=BlockSockets(
            providers=(ProviderSocket("control", Protocol.HTTP),))).descriptor()
        contract.update(
            capabilities=["health-checkable", "node-controllable"],
            provider_ports=[{"provider_socket": "control", "container_port": 8000}],
            public_environment=[{"kind": "public-static", "name": "CPK_WRAPPER_CONFIGURATION_FILE",
                                 "value": "/etc/cpk/cloudflared/control.json"}],
            control_surfaces=[{"provider_socket_name": "control", "variables": [],
                               "health_reads": ["liveness", "readiness"]}],
            configuration_requirements=[{"artifact_id": "cloudflared-control",
                "target_path": "/etc/cpk/cloudflared/control.json", "media_type": "application/json", "file_mode": "0444"}],
        )
        connector = ContainerServerProduct(ProductIdentity("authoring-example", "managed-connector", 1),
            OciImageReference("example.invalid", "connector", "sha256:" + "9" * 64),
            ProductRuntimeContractCodec().decode(contract))
        authority_ref = IngressAuthorityReference("hello-public")
        hostname = "hello.example.test"
        authority = CloudflareZoneIngressAuthorityCodec().decode({
            "provider_kind": "cloudflare", "account_id": "fixture-account", "zone_id": "fixture-zone",
            "zone_name": "example.test", "allowed_hostname_pattern": hostname, "api_token_ref": "secret://fixture/api",
            "generated_secret_provider_registration_id": "fixture-provider",
            "generated_secret_reference_prefix": "secret://fixture/generated",
        })
        with self.unit_of_work() as uow:
            registered = uow.stores.registered_products.register(workspace_id=workspace,
                descriptor_document=ProductDescriptorCodec().encode_document(connector),
                source=InlineDescriptorSource(), imported_by="operator-a", imported_at=NOW)
            binding = uow.stores.ingress_authorities.register(workspace_id=workspace,
                authority_ref=authority_ref, authority=authority, admitted_by="operator-a", admitted_at=NOW)
            uow.commit()
        extension = {
            "schema": "registered-product-ingress-extension.v1",
            "base": {"draft_id": second.draft_id, "revision": second.revision, "graph_id": second.graph_id},
            "runtime_id": "demo-docker",
            "connector": {"node_id": "connector", "product": registered.reference.descriptor(),
                          "receiver_id": "a" * 32, "provider_socket": "control"},
            "ingress": {"ingress_id": "hello-public", "authority_ref": authority_ref.reference_id,
                        "registration_id": binding.registration_id, "hostname": hostname,
                        "target": {"node_id": "mux", "provider_socket": "internal"},
                        "exposure": "https", "lifecycle": "ephemeral"},
        }
        return extension, first, graph

    def seed_receiver_verifiers(self):
        with self.unit_of_work() as uow:
            for purpose in (DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ, DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ):
                key = DelegationPublicKey("fixture-public", DelegationKeyAlgorithm.ED25519, PUBLIC_KEY_A)
                candidate = RegisterDelegationSigningKeyCommand("workspace-a", purpose, "fixture-issuer", key,
                    SecretReference("secret://fixture/verifier-key"), "operator-a", NOW, ()).candidate()
                uow.stores.delegation_signing_keys.register(candidate)
                uow.stores.delegation_signing_keys.activate("workspace-a", purpose, "fixture-issuer", key.key_id,
                    activated_by="operator-a", activated_at=NOW)
            uow.commit()

    def test_named_ingress_extension_matching_base_previews_connector_and_keeps_source(self):
        extension, _, graph = self.ingress_extension_fixture()
        self.seed_receiver_verifiers()
        before = self.catalogue_truth(), self.runtime_truth()
        result = self.preview(extension)
        compiled, slot, _ = ProductTopologyAuthoringService(self.unit_of_work).prepare_ingress_preview(
            principal().command_context("workspace-a"), extension)
        self.assertEqual(compiled.node("hello"), graph.node("hello"))
        self.assertEqual(compiled.node("mux"), graph.node("mux"))
        self.assertEqual(compiled.edges, graph.edges)
        self.assertEqual(compiled.node("mux").endpoint("internal").url, "http://mux:8000")
        self.assertEqual(compiled.node("hello").public_environment[0].value, "Hello from the operator")
        artifact, = compiled.node("connector").configuration_artifacts
        self.assertEqual((artifact.artifact_id, artifact.target_path, artifact.file_mode.value),
            ("cloudflared-control", "/etc/cpk/cloudflared/control.json", "0444"))
        self.assertEqual(result["receiver_configuration"], slot)
        self.assertEqual(slot["sha256"], sha256(artifact.content.encode()).hexdigest())
        configuration = ReceiverNodeControlConfigurationCodec().decode_bytes(artifact.content.encode())
        self.assertEqual(configuration.target.descriptor(), {"workspace_id": "workspace-a", "runtime_id": "demo-docker",
            "node_id": "connector", "provider_socket_name": "control", "receiver_id": "a" * 32})
        self.assertEqual({family.issuer for family in configuration.verifiers}, {"fixture-issuer"})
        self.assertNotIn("BEGIN PUBLIC KEY", json.dumps(result))
        self.assertNotIn("secret://", json.dumps(result))
        self.assertEqual(result["recipe"]["base"], extension["base"])
        self.assertEqual((self.catalogue_truth(), self.runtime_truth()), before)
        proposed = result["graph_descriptor"]
        source = public_product_topology_graph(graph)
        for role in ("hello", "mux"):
            self.assertEqual(proposed["nodes"][role], source["nodes"][role])
        self.assertEqual(proposed["edges"], source["edges"])
        self.assertEqual(proposed["nodes"]["connector"]["metadata"]["product_descriptor_digest"],
                         extension["connector"]["product"]["descriptor_sha256"])
        self.assertEqual(proposed["public_ingresses"][0]["target"], {"node_id": "mux", "provider_socket": "internal"})
        self.assertNotIn("Hello from the operator", json.dumps(result))

    def test_named_ingress_extension_refuses_unavailable_authority_product_or_receiver(self):
        extension, _, _ = self.ingress_extension_fixture()
        before = self.catalogue_truth(), self.runtime_truth()
        for group, field, value in (
            ("ingress", "registration_id", "iauth_" + "b" * 64),
            ("ingress", "hostname", "other.example.test"),
            ("connector", "product", {**extension["connector"]["product"], "descriptor_sha256": "b" * 64}),
        ):
            changed = deepcopy(extension)
            changed[group][field] = value
            with self.subTest(field=field), self.assertRaises(CpkServerApplicationError) as refused:
                self.preview(changed)
            self.assertEqual(refused.exception.status, 409)
        with self.assertRaises(CpkServerApplicationError) as no_keys:
            self.preview(extension)
        self.assertEqual(no_keys.exception.status, 409)
        for missing in (PolicyScope.INGRESS_AUTHORITY_READ, PolicyScope.DELEGATION_KEY_READ, PolicyScope.INSTANCE_WORKSPACE_READ):
            caller = principal(scopes=tuple(scope for scope in PolicyScope if scope != missing))
            with self.assertRaises(CpkServerApplicationError) as denied:
                self.preview(extension, caller=caller)
            self.assertEqual(denied.exception.status, 403)
        self.assertEqual((self.catalogue_truth(), self.runtime_truth()), before)

    def append_ingress_preview(self, proposal, **changes):
        base = proposal["recipe"]["base"]
        values = {"session_id": self.sessions["workspace-a"], "idempotency_key": "add-ingress-to-mux",
            "product_recipe": proposal["recipe"], "expected_head_revision": base["revision"],
            "expected_proposal_commitment": proposal["proposal_commitment"],
            "receiver_lifecycle": proposal["receiver_lifecycle"], **changes}
        planner = CpkServerPlanningService(None, desired_topology_drafts=self.catalogue(),
            product_topologies=ProductTopologyAuthoringService(self.unit_of_work))
        return planner.handle(CatalogueRequest("http", "command.desired-topology-draft.revise",
            ControlPlaneServiceRole.PLANNING, {"workspace_id": "workspace-a", "draft_id": base["draft_id"]},
            values, principal()))

    def test_append_named_ingress_extension_keeps_base_and_replays_exact_receipt(self):
        recipe, first, base_graph = self.ingress_extension_fixture()
        self.seed_receiver_verifiers()
        preview = self.preview(recipe)
        base = recipe["base"]
        before = self.runtime_truth()
        graph_count = len(self.rows("cpk_graph_versions"))
        action_count = len(self.rows("cpk_operation_actions"))
        receipt = self.append_ingress_preview(preview)
        self.assertEqual((receipt["workspace_id"], receipt["draft_id"], receipt["revision"]),
            ("workspace-a", base["draft_id"], 3))
        self.assertNotIn(receipt["graph_id"], {base["graph_id"], first.graph_id})
        self.assertEqual(len(self.rows("cpk_graph_versions")), graph_count + 1)
        self.assertEqual(len(self.rows("cpk_operation_actions")), action_count + 1)
        after = self.runtime_truth()
        for name in before.keys() - {"cpk_realized_graph_projections"}:
            self.assertEqual(before[name], after[name], name)
        with self.unit_of_work() as uow:
            revisions = uow.stores.desired_topology_drafts
            self.assertEqual([revisions.revision("workspace-a", base["draft_id"], i).graph_id for i in (1, 2, 3)],
                [first.graph_id, base["graph_id"], receipt["graph_id"]])
            saved = DEFAULT_GRAPH_CODEC.decode(uow.stores.graphs.get(receipt["graph_id"]).graph_descriptor)
            self.assertEqual(saved.node("hello"), base_graph.node("hello"))
            self.assertEqual(saved.node("mux"), base_graph.node("mux"))
            self.assertEqual(saved.edges, base_graph.edges)
            self.assertEqual(saved.public_ingresses[0].target.descriptor(),
                             {"node_id": "mux", "provider_socket": "internal"})
            self.assertEqual(saved.node("connector").configuration_artifacts[0].artifact_id, "cloudflared-control")
            uow.stores.ingress_authorities.revoke("workspace-a", IngressAuthorityReference("hello-public"))
            uow.commit()
        committed = self.catalogue_truth(), self.runtime_truth()
        self.assertEqual(self.append_ingress_preview(preview), receipt)
        for changed in ({"idempotency_key": "another-intent"},
                        {"product_recipe": {**recipe, "ingress": {**recipe["ingress"], "hostname": "changed.example.test"}}}):
            with self.assertRaises(CpkServerApplicationError) as refusal:
                self.append_ingress_preview(preview, **changed)
            self.assertEqual(refusal.exception.status, 409)
        self.assertEqual(committed, (self.catalogue_truth(), self.runtime_truth()))

    def test_named_ingress_extension_observes_one_snapshot_during_authority_and_product_change(self):
        recipe, _, _ = self.ingress_extension_fixture()
        self.seed_receiver_verifiers()
        workspace = "workspace-a"
        with self.unit_of_work() as uow:
            pin = ProductReferenceCodec().decode(recipe["connector"]["product"])
            original = uow.stores.registered_products.get(workspace, pin).descriptor_document.product
        successor = replace(original, identity=replace(original.identity, contract_revision=2))
        document = ProductDescriptorCodec().encode_document(successor)
        recipe["connector"]["product"] = {"identity": successor.identity.descriptor(), "descriptor_sha256": document.content_digest}
        commits = []
        observed = set()

        def rotate():
            with self.unit_of_work() as writer:
                writer.stores.ingress_authorities.revoke(workspace, IngressAuthorityReference("hello-public"))
                writer.stores.registered_products.register(workspace_id=workspace, descriptor_document=document,
                    source=InlineDescriptorSource(), imported_by="operator-a", imported_at=NOW)
                writer.commit()
            commits.append(True)

        class ObservedConnection(psycopg.Connection):
            def execute(connection, query, *args, **kwargs):
                result = super().execute(query, *args, **kwargs)
                sql = str(query).lower()
                if "select" in sql and "cpk_" in sql:
                    observed.add(super().execute("SELECT pg_backend_pid(), current_setting('transaction_isolation'), "
                        "current_setting('transaction_read_only')").fetchone())
                if "from cpk_ingress_authorities" in sql and not commits:
                    rotate()
                return result

        def reader():
            return PostgresUnitOfWork(lambda: ObservedConnection.connect(self.database_url,
                options=f"-c search_path={self.schema} -c lock_timeout=5000 -c statement_timeout=10000"))

        request = CatalogueRequest("http", PREVIEW, ControlPlaneServiceRole.READS, {"workspace_id": workspace},
            {"recipe": recipe}, principal())
        with self.assertRaises(CpkServerApplicationError) as refused:
            CpkServerReadService(reader).handle(request)
        self.assertEqual(refused.exception.status, 409)
        self.assertEqual(commits, [True])
        self.assertEqual(len(observed), 1)
        self.assertEqual(next(iter(observed))[1:], ("repeatable read", "on"))
        with self.assertRaises(CpkServerApplicationError) as fresh:
            self.preview(recipe)
        self.assertEqual(fresh.exception.status, 409)

    def test_named_ingress_extension_refuses_a_graph_id_from_an_older_revision(self):
        extension, first, _ = self.ingress_extension_fixture()
        extension["base"]["graph_id"] = first.graph_id
        before = self.catalogue_truth(), self.runtime_truth()
        with self.assertRaises(CpkServerApplicationError) as refused:
            self.preview(extension)
        self.assertEqual(before, (self.catalogue_truth(), self.runtime_truth()))
        self.assertEqual(refused.exception.status, 409,
            "a valid extension with a mismatched base graph must report a stale revision")

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

    def save(self, preview, *, key="recipe-save", caller=None, surface="http", service=None, **changes):
        values = {"session_id": self.sessions["workspace-a"], "title": "Proposed graph",
            "idempotency_key": key, "product_recipe": preview["recipe"],
            "expected_proposal_commitment": preview["proposal_commitment"], **changes}
        path = {"workspace_id": "workspace-a"}
        if surface == "mcp":
            values.update(path)
            path = {}
        adapter = CpkServerPlanningService(None, desired_topology_drafts=service or self.catalogue(),
            product_topologies=ProductTopologyAuthoringService(self.unit_of_work))
        return adapter.handle(CatalogueRequest(surface, "command.desired-topology-draft.create",
            ControlPlaneServiceRole.PLANNING, path, values, caller or principal()))

    def test_public_commitment_has_independently_encoded_normalized_preimage(self):
        recipe = self.recipe()
        response = self.preview(recipe)
        preimage = {"profile": COMMITMENT_PROFILE, "recipe": response["recipe"],
            "graph_descriptor": response["graph_descriptor"]}
        encoded = json.dumps(preimage, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False).encode("ascii")
        self.assertEqual(response["proposal_commitment"], {"profile": COMMITMENT_PROFILE,
            "sha256": sha256(encoded).hexdigest()})
        recipe["nodes"].reverse()
        self.assertEqual(self.preview(recipe), response)
        for changed in ("name", "setting", "edge"):
            recipe = self.recipe()
            if changed == "name":
                recipe["name"] = "Another proposed name"
            elif changed == "setting":
                recipe["nodes"][0]["configuration"]["public_environment"][0]["value"] = "Unicode \u00e9\n"
            else:
                recipe["connections"][0]["edge_id"] = "another-edge"
            self.assertNotEqual(self.preview(recipe)["proposal_commitment"], response["proposal_commitment"])

    def test_omitted_fixed_fields_do_not_enter_projection_or_commitment_with_pins_held_constant(self):
        service = ProductTopologyAuthoringService(self.unit_of_work)
        recipe = ProductTopologyRecipe.decode(self.recipe())
        graph = service.prepare_saved_graph(principal().command_context("workspace-a"), recipe.descriptor(),
            self.preview()["proposal_commitment"])
        node = graph.nodes["hello"]
        check = replace(node.block_spec.verification.checks[0], path="/different-fixed-value", expected_body_sha256="a" * 64)
        changed = replace(graph, nodes={**graph.nodes, "hello": replace(node,
            block_spec=replace(node.block_spec, verification=VerificationContract(checks=(check,))))})
        self.assertNotEqual(DEFAULT_GRAPH_CODEC.encode(graph), DEFAULT_GRAPH_CODEC.encode(changed))
        self.assertEqual(public_product_topology_graph(graph), public_product_topology_graph(changed))
        self.assertEqual(public_proposal_commitment(recipe, graph), public_proposal_commitment(recipe, changed))

    def test_changed_registered_pin_is_part_of_the_public_commitment(self):
        original = self.preview()
        product = replace(self.documents["hello"].product, display_name="A different registered revision")
        document = ProductDescriptorCodec().encode_document(product)
        with self.unit_of_work() as uow:
            uow.stores.registered_products.revoke("workspace-a",
                ProductTopologyRecipe.decode(original["recipe"]).instances[0].reference)
            registered = uow.stores.registered_products.register(workspace_id="workspace-a", descriptor_document=document,
                source=InlineDescriptorSource(), imported_by="operator-a", imported_at=NOW)
            uow.commit()
        recipe = self.recipe()
        recipe["nodes"][0]["product"] = registered.reference.descriptor()
        changed = self.preview(recipe)
        self.assertNotEqual(changed["proposal_commitment"], original["proposal_commitment"])
        self.assertEqual(changed["graph_descriptor"]["nodes"]["hello"]["metadata"]["product_descriptor_digest"],
            registered.reference.descriptor()["descriptor_sha256"])

    def test_save_returns_exact_revision_and_preserves_runtime_pins_edge_and_history(self):
        preview = self.preview()
        before = self.runtime_truth()
        saved = self.save(preview)
        self.assertEqual(set(saved), {"workspace_id", "draft_id", "revision", "graph_id"})
        self.assertEqual((saved["workspace_id"], saved["revision"]), ("workspace-a", 1))
        detail = self.read("read.desired-topology-draft-revision", draft_id=saved["draft_id"], revision=1)
        self.assertEqual({key: detail[key] for key in saved}, saved)
        graph = detail["graph_descriptor"]
        for role in ("hello", "mux"):
            self.assertEqual(graph["nodes"][role]["metadata"]["product_descriptor_digest"],
                self.references[role]["descriptor_sha256"])
            self.assertEqual(graph["nodes"][role]["runtime_id"], "demo-docker")
        self.assertEqual(set(graph["runtimes"]), {"demo-docker"})
        self.assertEqual(graph["edges"]["hello-to-mux"]["provider"], {"role": "hello", "socket": "internal"})
        self.assertEqual(graph["edges"]["hello-to-mux"]["consumer"], {"role": "mux", "requirement": "primary"})
        serialized = json.dumps(detail)
        for private in (PRIVATE_PATH, "Hello from the operator", "hidden-default-canary"):
            self.assertNotIn(private, serialized)
        with self.unit_of_work() as uow:
            stored = DEFAULT_GRAPH_CODEC.decode(uow.stores.graphs.get(saved["graph_id"]).graph_descriptor)
        self.assertEqual(stored.node("mux").socket_environment[0].edge_id, "hello-to-mux")
        self.assertEqual(stored.node("hello").public_environment[0].value, "Hello from the operator")
        actions = [row[0] for row in self.rows("cpk_operation_actions") if row[0]["action_type"] == "create-desired-topology-draft"]
        self.assertEqual([row["payload"] for row in actions], [saved])
        self.assertEqual(self.runtime_truth(), before)

    def test_save_mismatch_and_ambiguous_graph_input_leave_no_draft(self):
        preview = self.preview()
        before = self.catalogue_truth()
        changes = (
            {"expected_proposal_commitment": {"profile": COMMITMENT_PROFILE, "sha256": "0" * 64}},
            {"expected_proposal_commitment": None},
            {"graph": DEFAULT_GRAPH_CODEC.encode(self.graph())},
            {"actor_scopes": ["instance:workspace:edit"]},
        )
        for change in changes:
            with self.subTest(change=change):
                with self.assertRaises(CpkServerApplicationError):
                    self.save(preview, **change)
                self.assertEqual(self.catalogue_truth(), before)

    def test_current_read_and_edit_scopes_are_separate_and_denied_before_storage(self):
        reader = principal(scopes=(PolicyScope.INSTANCE_WORKSPACE_READ,))
        editor = principal(scopes=(PolicyScope.INSTANCE_WORKSPACE_EDIT,))
        preview = self.preview(caller=reader)
        self.save(preview, caller=editor)
        def storage_forbidden():
            self.fail("denied request opened storage")
        denied_preview = CatalogueRequest("http", PREVIEW, ControlPlaneServiceRole.READS,
            {"workspace_id": "workspace-a"}, {"recipe": self.recipe()}, editor)
        with self.assertRaises(CpkServerApplicationError) as error:
            CpkServerReadService(storage_forbidden).handle(denied_preview)
        self.assertEqual(error.exception.status, 403)
        with self.assertRaises(CpkServerApplicationError) as error:
            self.save(preview, caller=reader, service=self.catalogue(unit_of_work_factory=storage_forbidden))
        self.assertEqual(error.exception.status, 403)

    def test_foreign_unknown_and_revoked_pins_refuse_preview_and_fresh_save(self):
        preview = self.preview()
        with self.unit_of_work() as uow:
            uow.stores.registered_products.revoke("workspace-a",
                ProductTopologyRecipe.decode(preview["recipe"]).instances[0].reference)
            uow.commit()
        before = self.catalogue_truth()
        for recipe in (self.recipe(), {**self.recipe(), "nodes": [
                {**self.recipe()["nodes"][0], "product": {**self.references["hello"], "descriptor_sha256": "a" * 64}},
                self.recipe()["nodes"][1]]}):
            with self.assertRaises(CpkServerApplicationError):
                self.preview(recipe)
        with self.assertRaises(CpkServerApplicationError):
            self.save(preview)
        request = CatalogueRequest("http", PREVIEW, ControlPlaneServiceRole.READS,
            {"workspace_id": "workspace-b"}, {"recipe": self.recipe()}, principal("workspace-b"))
        with self.assertRaises(CpkServerApplicationError):
            CpkServerReadService(self.unit_of_work).handle(request)
        self.assertEqual(self.catalogue_truth(), before)

    def test_recipe_save_keeps_owner_historical_replay_and_conflict_ordering(self):
        preview = self.preview()
        saved = self.save(preview)
        with self.unit_of_work() as uow:
            for instance in ProductTopologyRecipe.decode(preview["recipe"]).instances:
                uow.stores.registered_products.revoke("workspace-a", instance.reference)
            uow.commit()
        OperationCommandService(self.unit_of_work, clock=lambda: NOW, id_factory=lambda: "close-action").execute(
            CloseOperationSession(self.sessions["workspace-a"], "operator-a", IdempotencyKey("close")))
        before = self.catalogue_truth()
        def allocation_forbidden():
            self.fail("replay or conflict allocated a new identity")
        owner = self.catalogue(id_factory=allocation_forbidden)
        self.assertEqual(self.save(preview, service=owner, surface="mcp"), saved)
        with self.assertRaises(CpkServerApplicationError) as error:
            self.save(preview, service=owner, title="changed intent")
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.catalogue_truth(), before)

    def test_equally_named_submissions_keep_distinct_receipts(self):
        preview = self.preview()
        first = self.save(preview, key="first")
        second = self.save(preview, key="second", surface="mcp")
        self.assertNotEqual(first["draft_id"], second["draft_id"])
        for saved in (first, second):
            detail = self.read("read.desired-topology-draft-revision", draft_id=saved["draft_id"], revision=1)
            self.assertEqual({key: detail[key] for key in saved}, saved)

    def test_closed_recipe_and_incompatible_edges_refuse_without_writes(self):
        mutations = (
            lambda r: r.update(extra=True),
            lambda r: r["runtime"].update(authority_ref="not-admitted"),
            lambda r: r["nodes"][0]["configuration"].update(secret_deliveries=[{}]),
            lambda r: r["nodes"][0]["configuration"].update(public_environment=[]),
            lambda r: r["connections"][0].update(protocol={"transport": "tcp", "application": None}),
            lambda r: r["connections"].extend(deepcopy(r["connections"])),
            lambda r: r["nodes"].extend(deepcopy(r["nodes"])),
            lambda r: r.update(nodes=[deepcopy(r["nodes"][0])] * 33),
            lambda r: r.update(connections=[deepcopy(r["connections"][0])] * 65),
            lambda r: r.update(name="x" * 1048577),
            lambda r: r["nodes"][0]["configuration"]["public_environment"][0].update(value=float("nan")),
        )
        before = self.catalogue_truth(), self.runtime_truth()
        for mutate in mutations:
            recipe = self.recipe()
            mutate(recipe)
            with self.subTest(mutation=mutate):
                with self.assertRaises(CpkServerApplicationError) as error:
                    self.preview(recipe)
                self.assertLessEqual(len(str(error.exception)), 512)
                self.assertEqual((self.catalogue_truth(), self.runtime_truth()), before)

    def test_preview_and_save_share_exact_owner_graph_bound_beyond_compact_json(self):
        recipe = self.recipe()
        recipe["connections"] = []
        # Size the real canonical graph, not a fixture estimate. Fixed checks
        # enlarge compiled bytes without expanding the public input recipe.
        for count in range(10, 101, 10):
            product = replace(self.documents["hello"].product, identity=ProductIdentity("authoring-example", "large", 1), runtime_contract=replace(
                self.documents["hello"].product.runtime_contract,
                verification=VerificationContract(checks=tuple(HttpCheck(check_id="check-" + str(i),
                    provider_socket="internal", path="/" + "x" * 254) for i in range(count)))))
            document = ProductDescriptorCodec().encode_document(product)
            def compile_value(value):
                blocks = tuple(instantiate_catalog_product(ProductCatalog.from_documents((document,)), product.identity,
                    role_id="hello-" + str(i), configuration=ProductInstanceConfiguration(public_environment=(
                        PublicStaticEnvironmentBinding("HELLO_MESSAGE", value),))) for i in range(32))
                return compile_topology(DeploymentTopology(recipe["name"], DockerRuntime(
                    runtime_id="demo-docker", network_name="demo-net", children=blocks)))
            graph = compile_value("")
            compact = len(json.dumps(DEFAULT_GRAPH_CODEC.encode(graph), sort_keys=True,
                separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii"))
            if compact >= 650000:
                break
        self.assertLess(compact, 1048576)
        descriptor = DEFAULT_GRAPH_CODEC.encode(graph)
        storage = len(json.dumps(descriptor, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        padding = (1048576 - (storage - compact) // 2 - compact) // 32
        self.assertTrue(0 < padding <= 16384)
        graph = compile_value("x" * padding)
        descriptor = DEFAULT_GRAPH_CODEC.encode(graph)
        compact = len(json.dumps(descriptor, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False).encode("ascii"))
        storage = len(json.dumps(descriptor, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        self.assertLess(compact, 1048576)
        self.assertGreater(storage, 1048576)
        with self.assertRaisesRegex(DesiredTopologyDraftError, "bounded graph descriptor"):
            bounded_draft_graph_descriptor(graph)
        with self.unit_of_work() as uow:
            registered = uow.stores.registered_products.register(workspace_id="workspace-a", descriptor_document=document,
                source=InlineDescriptorSource(), imported_by="operator-a", imported_at=NOW)
            uow.commit()
        node = recipe["nodes"][0]
        node["product"] = registered.reference.descriptor()
        node["configuration"]["public_environment"][0]["value"] = "x" * padding
        recipe["nodes"] = [{**deepcopy(node), "role_id": "hello-" + str(i)} for i in range(32)]
        self.assertLess(len(json.dumps(recipe).encode()), 1048576)
        commitment = public_proposal_commitment(ProductTopologyRecipe.decode(recipe), graph)
        before = self.catalogue_truth()
        with self.assertRaises(CpkServerApplicationError):
            self.preview(recipe)
        with self.assertRaises(CpkServerApplicationError):
            self.save({"recipe": recipe, "proposal_commitment": commitment})
        self.assertEqual(self.catalogue_truth(), before)

    def test_exact_saved_revision_hides_fixed_verification_and_keeps_role_named_verification(self):
        document = self.documents["hello"]
        block = instantiate_catalog_product(ProductCatalog.from_documents((document,)), document.product.identity,
            role_id="verification", configuration=ProductInstanceConfiguration(public_environment=(
                PublicStaticEnvironmentBinding("HELLO_MESSAGE", "supplied public message"),)))
        graph = compile_topology(DeploymentTopology("saved", DockerRuntime(children=(block,))))
        saved = self.create(graph=graph)
        response = self.read("read.desired-topology-draft-revision", draft_id=saved.draft_id, revision=1)
        self.assertNotIn(PRIVATE_PATH, json.dumps(response))
        node = response["graph_descriptor"]["nodes"]["verification"]
        self.assertEqual(node["metadata"]["product_descriptor_digest"], self.references["hello"]["descriptor_sha256"])
        self.assertNotIn("verification", node)
        self.assertEqual(node["block_spec"]["verification"],
            {"checks": [{"kind": "http", "check_id": "live", "provider_socket": "internal"}]})
        directory = CpkServerReadService(self.unit_of_work).handle(CatalogueRequest("http", "read.product-detail",
            ControlPlaneServiceRole.READS, {"workspace_id": "workspace-a",
                "descriptor_sha256": self.references["hello"]["descriptor_sha256"]}, {}, principal()))
        self.assertEqual(directory["product"]["contract"]["verification"],
            [{"kind": "http", "check_id": "live", "provider_socket": "internal"}])
        with self.unit_of_work() as uow:
            self.assertIn(PRIVATE_PATH, json.dumps(uow.stores.graphs.get(saved.graph_id).graph_descriptor))

    def test_graph_projection_covers_both_known_positions_without_reinterpreting_metadata(self):
        contract = {"checks": [{"kind": "http", "check_id": "live", "provider_socket": "internal", "path": PRIVATE_PATH}]}
        descriptor = {"nodes": {"verification": {"verification": contract,
            "block_spec": {"verification": contract}, "metadata": {"verification": "ordinary-value"}}}}
        projected = _redact_graph_descriptor(descriptor)
        node = projected["nodes"]["verification"]
        self.assertNotIn(PRIVATE_PATH, json.dumps(projected))
        self.assertEqual(node["verification"], node["block_spec"]["verification"])
        self.assertEqual(node["metadata"]["verification"], "ordinary-value")
        self.assertIn(PRIVATE_PATH, json.dumps(descriptor))

    def test_neighboring_current_and_desired_graph_reads_hide_the_same_verification_canary(self):
        # Independent disposable fixture: these pointer assignments are setup
        # for read projection evidence, not part of save-only behavior.
        saved = self.save(self.preview())
        with self.unit_of_work() as uow:
            uow.stores.workspaces.set_current_graph("workspace-a", saved["graph_id"])
            uow.stores.workspaces.set_desired_graph("workspace-a", saved["graph_id"])
            uow.commit()
        for route in ("read.current-graph", "read.desired-graph"):
            response = self.read(route)
            self.assertEqual(response["graph_id"], saved["graph_id"])
            self.assertNotIn(PRIVATE_PATH, json.dumps(response))
            node = response["graph_descriptor"]["nodes"]["hello"]
            self.assertEqual(node["block_spec"]["verification"],
                {"checks": [{"kind": "http", "check_id": "live", "provider_socket": "internal"}]})
