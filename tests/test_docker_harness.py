import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DockerHarnessTests(unittest.TestCase):
    def test_test_sh_is_docker_first_and_avoids_broad_cleanup(self) -> None:
        test_sh = (ROOT / "test.sh").read_text(encoding="utf-8")

        self.assertIn("docker build", test_sh)
        self.assertIn("docker run", test_sh)
        self.assertIn("scripts/docker_residue_audit.sh", test_sh)
        self.assertNotIn("python -m unittest", test_sh)
        self.assertNotIn("docker system prune", test_sh)
        self.assertNotIn("docker volume prune", test_sh)

    def test_test_image_runs_unittest_and_product_image_lane(self) -> None:
        dockerfile = (ROOT / "Dockerfile.test").read_text(encoding="utf-8")
        runner = (ROOT / "scripts" / "run_all_tests.py").read_text(encoding="utf-8")

        self.assertIn("python:3.12-slim", dockerfile)
        self.assertIn("python", dockerfile)
        self.assertIn("scripts/run_all_tests.py", dockerfile)
        self.assertIn("COPY products ./products", dockerfile)
        self.assertIn("unittest", runner)
        self.assertIn("products/cpk_server/tests", runner)
        self.assertIn("products/hello_server/tests", runner)
        self.assertIn("products/http_active_router/tests", runner)
        self.assertIn("products/http_multiplexer/tests", runner)
        self.assertIn("products/cpk_local_gateway/tests", runner)
        self.assertIn("products/postgres_server/tests", runner)
        self.assertIn("product_image_lane.py", runner)

    def test_product_image_lane_reports_cpk_server_image_definition(self) -> None:
        result = subprocess.run(
            [
                "python",
                str(ROOT / "scripts" / "product_image_lane.py"),
                "--inventory",
                str(ROOT / "coordination" / "product-inventory.json"),
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        report = json.loads(result.stdout)

        self.assertEqual(report["schema"], "cpk-servers.product-image-lane-report")
        self.assertEqual(
            [product["product_id"] for product in report["products"]],
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
            report["image_builds"],
            [
                {
                    "product_id": "cpk-server",
                    "image_source": "local-dockerfile",
                    "dockerfile": "products/cpk_server/Dockerfile",
                    "status": "image-definition-present",
                },
                {
                    "product_id": "cpk-server-docker",
                    "image_source": "local-dockerfile",
                    "dockerfile": "products/cpk_server/Dockerfile",
                    "status": "image-definition-present",
                },
                {
                    "product_id": "hello-server",
                    "image_source": "local-dockerfile",
                    "dockerfile": "products/hello_server/Dockerfile",
                    "status": "image-definition-present",
                },
                {
                    "product_id": "http-active-router",
                    "image_source": "local-dockerfile",
                    "dockerfile": "products/http_active_router/Dockerfile",
                    "status": "image-definition-present",
                },
                {
                    "product_id": "http-multiplexer",
                    "image_source": "local-dockerfile",
                    "dockerfile": "products/http_multiplexer/Dockerfile",
                    "status": "image-definition-present",
                },
                {
                    "product_id": "cpk-local-gateway",
                    "image_source": "local-dockerfile",
                    "dockerfile": "products/cpk_local_gateway/Dockerfile",
                    "status": "image-definition-present",
                },
                {
                    "product_id": "postgres-server",
                    "image_source": "external-oci",
                    "external_image": "docker.io/library/postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777",
                    "status": "external-oci-pinned",
                },
            ],
        )
        self.assertEqual(report["status"], "product-image-definitions-present")


    def test_publish_product_image_workflow_is_per_product_and_uses_ghcr(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "publish-product-image.yml").read_text(
            encoding="utf-8"
        )
        script = (ROOT / "scripts" / "publish_product_image.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("workflow_dispatch", workflow)
        self.assertIn("packages: write", workflow)
        self.assertIn("scripts/publish_product_image.sh", workflow)
        self.assertIn("ghcr.io", script)
        self.assertIn("products/cpk_server/Dockerfile", script)
        self.assertIn("products/hello_server/Dockerfile", script)
        self.assertIn("products/http_active_router/Dockerfile", script)
        self.assertIn("products/http_multiplexer/Dockerfile", script)
        self.assertIn("products/cpk_local_gateway/Dockerfile", script)
        self.assertIn("unsupported product id", script)
        self.assertNotIn("docker system prune", script)
        self.assertNotIn("docker volume prune", script)

    def test_residue_audit_filters_only_owned_resources(self) -> None:
        audit = (ROOT / "scripts" / "docker_residue_audit.sh").read_text(encoding="utf-8")

        self.assertIn("org.openj92.project=control-plane-kit-servers", audit)
        self.assertIn("docker ps", audit)
        self.assertIn("docker volume ls", audit)
        self.assertNotIn("docker rm", audit)
        self.assertNotIn("docker volume rm", audit)
        self.assertNotIn("prune", audit)
        self.assertIn("Pottery Factory", audit)

    def test_github_actions_run_tests_on_main_and_develop(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("main", workflow)
        self.assertIn("develop", workflow)
        self.assertIn("./test.sh", workflow)


if __name__ == "__main__":
    unittest.main()
