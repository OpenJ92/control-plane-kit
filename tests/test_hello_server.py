from unittest import TestCase, main

from control_plane_kit import create_hello_app
from fastapi.testclient import TestClient


class HelloServerTests(TestCase):
    def test_hello_route_uses_supplied_world_value(self):
        client = TestClient(create_hello_app("earth"))

        response = client.get("/hello")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"message": "Hello World earth"})

    def test_health_route_reports_supplied_world_value(self):
        client = TestClient(create_hello_app("mars"))

        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "world": "mars"})


if __name__ == "__main__":
    main()
