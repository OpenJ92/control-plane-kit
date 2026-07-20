import os
from unittest import main, skipUnless

from examples.read_interface_demo_server import DEMO_WORKSPACE_ID, DemoSettings, create_demo_app
from tests.postgres_case import PostgresStoreTestCase

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    TestClient = None


@skipUnless(TestClient is not None, "FastAPI optional dependency is not installed")
class ReadInterfaceDemoServerTests(PostgresStoreTestCase):
    def test_demo_server_seeds_and_serves_read_routes(self):
        app = create_demo_app(
            DemoSettings(
                database_url=_database_url(),
                token="test-token",
                reset=True,
            )
        )
        headers = {"Authorization": "Bearer test-token"}

        try:
            workflow_routes = {
                route.path: route.methods
                for route in app.routes
                if route.path.startswith("/workspaces/")
            }
            with TestClient(app) as client:
                workspace = client.get(f"/workspaces/{DEMO_WORKSPACE_ID}", headers=headers)
                operator_graph = client.get(f"/workspaces/{DEMO_WORKSPACE_ID}/operator-graph", headers=headers)
                activity = client.get(f"/workspaces/{DEMO_WORKSPACE_ID}/activity?limit=5", headers=headers)

            self.assertEqual(workspace.status_code, 200)
            self.assertEqual(workspace.json()["workspace"]["workspace_id"], DEMO_WORKSPACE_ID)
            self.assertEqual(operator_graph.status_code, 200)
            self.assertTrue(operator_graph.json()["assigned"])
            self.assertEqual(activity.status_code, 200)
            self.assertEqual(activity.json()["sessions"][0]["session_id"], "demo-session-1")
            self.assertTrue(workflow_routes)
            self.assertTrue(
                all(methods == {"GET"} for methods in workflow_routes.values())
            )
        finally:
            app.state.demo_connection.close()

    def test_demo_server_requires_configured_token(self):
        app = create_demo_app(
            DemoSettings(
                database_url=_database_url(),
                token="test-token",
                reset=True,
            )
        )
        try:
            with TestClient(app) as client:
                response = client.get(f"/workspaces/{DEMO_WORKSPACE_ID}")

            self.assertEqual(response.status_code, 401)
        finally:
            app.state.demo_connection.close()


def _database_url() -> str:
    value = os.environ.get("CPK_TEST_DATABASE_URL")
    if not value:
        raise RuntimeError("CPK_TEST_DATABASE_URL is required")
    return value


if __name__ == "__main__":
    main()
