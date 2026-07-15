from io import StringIO
from json import loads
from unittest import TestCase, main
from urllib.error import HTTPError

from control_plane_kit.cli import run


class CliTests(TestCase):
    def test_workspace_command_reads_from_base_url_and_prints_json(self):
        opener = FakeOpener({"workspace": {"workspace_id": "workspace-a"}})
        stdout = StringIO()

        status = run(
            ["--base-url", "http://instance", "workspace", "workspace-a"],
            opener=opener,
            stdout=stdout,
            stderr=StringIO(),
            env={},
        )

        self.assertEqual(status, 0)
        self.assertEqual(loads(stdout.getvalue()), {"workspace": {"workspace_id": "workspace-a"}})
        self.assertEqual(opener.requests[0].full_url, "http://instance/workspaces/workspace-a")

    def test_token_sets_bearer_header(self):
        opener = FakeOpener({"ok": True})

        status = run(
            ["--base-url", "http://instance", "--token", "secret", "current-graph", "workspace-a"],
            opener=opener,
            stdout=StringIO(),
            stderr=StringIO(),
            env={},
        )

        self.assertEqual(status, 0)
        self.assertEqual(opener.requests[0].get_header("Authorization"), "Bearer secret")

    def test_environment_supplies_base_url_and_token(self):
        opener = FakeOpener({"ok": True})

        status = run(
            ["desired-graph", "workspace/a"],
            opener=opener,
            stdout=StringIO(),
            stderr=StringIO(),
            env={"CONTROL_PLANE_INSTANCE_URL": "http://instance/root", "CONTROL_PLANE_TOKEN": "secret"},
        )

        self.assertEqual(status, 0)
        self.assertEqual(opener.requests[0].full_url, "http://instance/root/workspaces/workspace%2Fa/graphs/desired")
        self.assertEqual(opener.requests[0].get_header("Authorization"), "Bearer secret")

    def test_pointer_and_limit_commands_encode_query_parameters(self):
        opener = FakeOpener({"ok": True})

        run(
            ["--base-url", "http://instance", "operator-graph", "workspace-a", "--pointer", "desired"],
            opener=opener,
            stdout=StringIO(),
            stderr=StringIO(),
            env={},
        )
        run(
            ["--base-url", "http://instance", "activity", "workspace-a", "--limit", "3"],
            opener=opener,
            stdout=StringIO(),
            stderr=StringIO(),
            env={},
        )

        self.assertEqual(opener.requests[0].full_url, "http://instance/workspaces/workspace-a/operator-graph?pointer=desired")
        self.assertEqual(opener.requests[1].full_url, "http://instance/workspaces/workspace-a/activity?limit=3")

    def test_missing_base_url_is_usage_error(self):
        stderr = StringIO()

        status = run(["workspace", "workspace-a"], opener=FakeOpener({"ok": True}), stdout=StringIO(), stderr=stderr, env={})

        self.assertEqual(status, 2)
        self.assertEqual(stderr.getvalue(), "--base-url or CONTROL_PLANE_INSTANCE_URL is required\n")

    def test_http_error_returns_nonzero_and_prints_detail(self):
        stderr = StringIO()

        status = run(
            ["--base-url", "http://instance", "workspace", "missing"],
            opener=FakeOpener({"detail": "missing workspace 'missing'"}, status=404),
            stdout=StringIO(),
            stderr=stderr,
            env={},
        )

        self.assertEqual(status, 1)
        self.assertEqual(stderr.getvalue(), "request failed with HTTP 404: missing workspace 'missing'\n")


class FakeOpener:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if self.status >= 400:
            raise HTTPError(request.full_url, self.status, "error", {}, FakeResponse(self.payload))
        return FakeResponse(self.payload)


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def read(self) -> bytes:
        import json

        return json.dumps(self.payload).encode("utf-8")

    def close(self) -> None:
        return None


if __name__ == "__main__":
    main()
