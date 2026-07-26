import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ServerRepositoryPolicyTests(unittest.TestCase):
    def test_agents_records_issue_loop_and_server_ownership_laws(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        normalized_agents = agents.lower()

        for required in (
            "inspect governing frozen tests",
            "extract behavioral law cards",
            "dry-run source and architecture",
            "focused target tests",
            "prove focused target red",
            "Docker-first validation",
            "one product implementation lane owns one directory",
            "Core never imports servers",
            "cpk-server and Hello have different roles",
            "Do not use broad Docker prune",
        ):
            with self.subTest(required=required):
                self.assertIn(required, agents)

        self.assertIn(
            "catalogue imports values, not applications or stores",
            normalized_agents,
        )

    def test_git_flow_keeps_main_develop_and_issue_branches_explicit(self) -> None:
        git_flow = (ROOT / "GIT-FLOW.md").read_text(encoding="utf-8")

        for required in (
            "main",
            "develop",
            "codex/<issue-id>-<slug>",
            "Pull requests target the active milestone branch or main as directed",
            "Do not merge product implementation work directly to main",
        ):
            with self.subTest(required=required):
                self.assertIn(required, git_flow)

    def test_product_layout_examples_separate_values_processes_and_support(self) -> None:
        layout = (ROOT / "docs" / "product-layouts.md").read_text(encoding="utf-8")

        for required in (
            "Allowed product layout",
            "Forbidden product layout",
            "descriptor.py",
            "image/",
            "entrypoint/",
            "tests/",
            "products/cpk_server",
            "products/hello_server",
            "No shared support without evidence from two products",
            "Catalogue imports declaration entrances only",
        ):
            with self.subTest(required=required):
                self.assertIn(required, layout)

    def test_product_inventory_tracks_published_seed_descriptors(self) -> None:
        inventory = json.loads(
            (ROOT / "coordination" / "product-inventory.json").read_text(encoding="utf-8")
        )

        self.assertEqual(
            [product["product_id"] for product in inventory["products"]],
            [
                "cpk-server",
                "cpk-server-docker",
                "hello-server",
                "http-active-router",
                "http-multiplexer",
                "cpk-local-gateway",
                "postgres-server",
            ],
        )
        self.assertEqual(
            [product["status"] for product in inventory["products"]],
            [
                "descriptor-published",
                "descriptor-published",
                "descriptor-published",
                "descriptor-published",
                "descriptor-published",
                "descriptor-published",
                "descriptor-published",
            ],
        )
        self.assertEqual(
            inventory["products"][0]["descriptor_issue"],
            "OpenJ92/control-plane-kit#816",
        )
        self.assertEqual(
            inventory["products"][1]["descriptor_issue"],
            "OpenJ92/control-plane-kit#966",
        )
        self.assertEqual(
            inventory["products"][2]["descriptor_issue"],
            "OpenJ92/control-plane-kit#824",
        )
        self.assertEqual(
            inventory["products"][3]["descriptor_issue"],
            "OpenJ92/control-plane-kit#825",
        )
        self.assertEqual(
            inventory["products"][4]["descriptor_issue"],
            "OpenJ92/control-plane-kit#826",
        )
        self.assertEqual(
            inventory["products"][5]["descriptor_issue"],
            "OpenJ92/control-plane-kit#1023",
        )
        self.assertEqual(
            inventory["products"][6]["descriptor_issue"],
            "OpenJ92/control-plane-kit#828",
        )
        self.assertEqual(inventory["products"][6]["image_source"], "external-oci")
        catalogue = json.loads(
            (ROOT / "catalogue" / "products.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [product["product_id"] for product in catalogue["products"]],
            [
                "cpk-server",
                "cpk-server-docker",
                "hello-server",
                "http-active-router",
                "http-multiplexer",
                "cpk-local-gateway",
                "postgres-server",
            ],
        )
        self.assertTrue(all(product["status"] == "completed" for product in catalogue["products"]))
        self.assertIn(
            "catalogue entries",
            inventory["laws"][0],
        )
        reserved = {product["product_id"] for product in inventory["bootstrap_reserved_products"]}
        self.assertEqual(reserved, {"hello"})

    def test_learning_and_decision_logs_record_no_product_implementation(self) -> None:
        learning = (ROOT / "docs" / "learning" / "extract-f-run-0001.md").read_text(
            encoding="utf-8"
        )
        decision = (ROOT / "docs" / "decisions" / "0002-server-repository-policy.md").read_text(
            encoding="utf-8"
        )

        for document in (learning, decision):
            normalized_document = re.sub(r"\s+", " ", document)
            with self.subTest(document=document[:64]):
                self.assertIn("no product implementation", normalized_document)
                self.assertIn("#650", document)
                self.assertIn("#651", document)
                self.assertIn("#652", document)


if __name__ == "__main__":
    unittest.main()
