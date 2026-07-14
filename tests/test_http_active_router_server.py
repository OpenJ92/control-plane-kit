from dataclasses import dataclass
from unittest import TestCase, main

from control_plane_kit import BlockControlState
from control_plane_kit.servers.http_active_router import (
    _forward_headers,
    _response_headers,
    _target_url,
    create_http_active_router_app,
)

from fastapi.testclient import TestClient


@dataclass
class FakeResponse:
    status_code: int
    content: bytes
    headers: dict[str, str]


class FakeAsyncClient:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def request(self, method, url, headers, content):
        self.requests.append(
            {"method": method, "url": url, "headers": dict(headers), "content": content}
        )
        return self.response


class FailingAsyncClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def request(self, method, url, headers, content):
        raise OSError("target unavailable")


class HttpActiveRouterHelpersTests(TestCase):
    def test_target_url_preserves_path_and_query(self):
        self.assertEqual(
            _target_url("http://api", "v1/orders", "page=2"),
            "http://api/v1/orders?page=2",
        )

    def test_forward_headers_drop_hop_specific_values(self):
        self.assertEqual(
            _forward_headers({"host": "router", "content-length": "3", "x-trace": "abc"}),
            {"x-trace": "abc"},
        )

    def test_response_headers_drop_hop_specific_values(self):
        self.assertEqual(
            _response_headers({"transfer-encoding": "chunked", "x-result": "ok"}),
            {"x-result": "ok"},
        )


class HttpActiveRouterFastAPITests(TestCase):
    def test_control_routes_register_and_switch_targets(self):
        state = BlockControlState("api-router")
        client = TestClient(create_http_active_router_app(state))

        response = client.post(
            "/__deploy/targets",
            json={"api-v1": "http://api-v1", "api-v2": "http://api-v2"},
        )
        self.assertEqual(response.status_code, 200)

        response = client.post("/__deploy/active-target", json={"target_id": "api-v2"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"block_id": "api-router", "active_target": "api-v2"})
        self.assertEqual(state.active_target, "api-v2")

    def test_missing_active_target_returns_service_unavailable(self):
        client = TestClient(create_http_active_router_app(BlockControlState("api-router")))

        response = client.get("/orders")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "no active target")

    def test_data_path_forwards_to_active_target(self):
        fake_client = FakeAsyncClient(
            FakeResponse(
                status_code=202,
                content=b"accepted",
                headers={"content-type": "text/plain", "x-target": "api-v2"},
            )
        )
        state = BlockControlState(
            "api-router",
            targets={"api-v2": "http://api-v2"},
            active_target="api-v2",
        )
        app = create_http_active_router_app(state, client_factory=lambda: fake_client)
        client = TestClient(app)

        response = client.post("/orders?source=test", content=b"payload", headers={"x-trace": "abc"})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.text, "accepted")
        self.assertEqual(response.headers["x-target"], "api-v2")
        self.assertEqual(len(fake_client.requests), 1)
        forwarded = fake_client.requests[0]
        self.assertEqual(forwarded["method"], "POST")
        self.assertEqual(forwarded["url"], "http://api-v2/orders?source=test")
        self.assertEqual(forwarded["content"], b"payload")
        self.assertEqual(forwarded["headers"]["x-trace"], "abc")
        self.assertNotIn("host", forwarded["headers"])
        self.assertNotIn("content-length", forwarded["headers"])

    def test_data_path_returns_bad_gateway_when_active_target_is_unreachable(self):
        state = BlockControlState(
            "api-router",
            targets={"api-v2": "http://api-v2"},
            active_target="api-v2",
        )
        app = create_http_active_router_app(state, client_factory=FailingAsyncClient)
        client = TestClient(app)

        response = client.get("/orders")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "active target request failed")

    def test_router_specific_routes_add_inactive_target_then_switch(self):
        state = BlockControlState(
            "api-router",
            targets={"hello-earth": "http://hello-earth:8000"},
            active_target="hello-earth",
        )
        client = TestClient(create_http_active_router_app(state))

        response = client.put(
            "/__deploy/routers/api-router/targets/hello-mars",
            json={"url": "http://hello-mars:8000"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(state.active_target, "hello-earth")
        self.assertEqual(
            response.json(),
            {
                "block_id": "api-router",
                "active_target": "hello-earth",
                "targets": {
                    "hello-earth": "http://hello-earth:8000",
                    "hello-mars": "http://hello-mars:8000",
                },
            },
        )

        response = client.post(
            "/__deploy/routers/api-router/active-target",
            json={"target_id": "hello-mars"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"block_id": "api-router", "active_target": "hello-mars"})
        self.assertEqual(state.active_target, "hello-mars")

    def test_router_specific_routes_require_matching_router_id(self):
        client = TestClient(create_http_active_router_app(BlockControlState("api-router")))

        response = client.put(
            "/__deploy/routers/other-router/targets/hello-mars",
            json={"url": "http://hello-mars:8000"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "unknown router")

    def test_router_specific_routes_remove_inactive_target(self):
        state = BlockControlState(
            "api-router",
            targets={
                "hello-earth": "http://hello-earth:8000",
                "hello-mars": "http://hello-mars:8000",
            },
            active_target="hello-earth",
        )
        client = TestClient(create_http_active_router_app(state))

        response = client.delete("/__deploy/routers/api-router/targets/hello-mars")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["targets"], {"hello-earth": "http://hello-earth:8000"})
        self.assertEqual(state.active_target, "hello-earth")


if __name__ == "__main__":
    main()
