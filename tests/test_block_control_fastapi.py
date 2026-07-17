from unittest import TestCase, main, skipUnless

from control_plane_kit import BlockControlState, CapabilityName, create_block_control_app

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    TestClient = None


@skipUnless(TestClient is not None, "FastAPI optional dependency is not installed")
class BlockControlFastAPITests(TestCase):
    def test_execution_mode_requires_auth_configuration(self):
        with self.assertRaisesRegex(ValueError, "token"):
            create_block_control_app(
                BlockControlState("router"),
                execution_mode=True,
            )

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

    def test_execution_mutations_require_identity_replay_and_conflict(self):
        state = BlockControlState("router", targets={"v1": "http://v1"})
        client = TestClient(
            create_block_control_app(state, token="secret", execution_mode=True)
        )
        auth = {"Authorization": "Bearer secret"}
        self.assertEqual(
            client.post("/__deploy/targets", headers=auth, json={"v2": "http://v2"}).status_code,
            400,
        )
        headers = {
            **auth,
            "X-Control-Plane-Request-ID": "request-a",
            "Idempotency-Key": "mutation-a",
        }

        first = client.post("/__deploy/targets", headers=headers, json={"v2": "http://v2"})
        replay = client.post("/__deploy/targets", headers=headers, json={"v2": "http://v2"})
        conflict = client.post("/__deploy/targets", headers=headers, json={"v3": "http://v3"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.json(), first.json())
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(state.targets, {"v2": "http://v2"})
        self.assertEqual(state.runtime.version, 1)

    def test_execution_mutation_body_is_bounded_before_application(self):
        state = BlockControlState("router")
        client = TestClient(
            create_block_control_app(
                state,
                token="secret",
                execution_mode=True,
                max_control_request_bytes=64,
            )
        )
        response = client.post(
            "/__deploy/targets",
            headers={
                "Authorization": "Bearer secret",
                "X-Control-Plane-Request-ID": "request-a",
                "Idempotency-Key": "mutation-a",
            },
            json={"target": "x" * 128},
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(state.targets, {})

    def test_execution_auth_does_not_protect_ordinary_data_routes(self):
        app = create_block_control_app(
            BlockControlState("router"),
            token="secret",
            execution_mode=True,
        )

        @app.get("/data")
        def data():
            return {"message": "ordinary traffic"}

        client = TestClient(app)
        self.assertEqual(client.get("/__deploy/status").status_code, 401)
        self.assertEqual(client.get("/data").json(), {"message": "ordinary traffic"})


if __name__ == "__main__":
    main()
