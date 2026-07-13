from unittest import TestCase, main, skipUnless

from control_plane_kit import BlockControlState, CapabilityName, create_block_control_app

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    TestClient = None


@skipUnless(TestClient is not None, "FastAPI optional dependency is not installed")
class BlockControlFastAPITests(TestCase):
    def test_control_routes_can_be_called_without_token_when_unconfigured(self):
        app = create_block_control_app(
            BlockControlState("router", capabilities=(CapabilityName.HEALTH_CHECKABLE,)),
        )
        client = TestClient(app)

        response = client.get("/__deploy/capabilities")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["block_id"], "router")

    def test_configured_token_protects_control_routes(self):
        app = create_block_control_app(BlockControlState("router"), token="secret")
        client = TestClient(app)

        self.assertEqual(client.get("/__deploy/status").status_code, 401)
        self.assertEqual(
            client.get("/__deploy/status", headers={"Authorization": "Bearer secret"}).status_code,
            200,
        )
        self.assertEqual(
            client.get("/__deploy/status", headers={"X-Control-Plane-Token": "secret"}).status_code,
            200,
        )

    def test_unknown_active_target_returns_bad_request(self):
        app = create_block_control_app(BlockControlState("router"))
        client = TestClient(app)

        response = client.post("/__deploy/active-target", json={"target_id": "missing"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "unknown target")

    def test_observers_route_mutates_state(self):
        state = BlockControlState("mux")
        client = TestClient(create_block_control_app(state))

        response = client.post("/__deploy/observers", json={"logger": "http://logger"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["observers"], {"logger": "http://logger"})
        self.assertEqual(state.observers, {"logger": "http://logger"})


if __name__ == "__main__":
    main()
