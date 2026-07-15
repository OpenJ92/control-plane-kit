"""Read-only command line client for a control-plane instance API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Callable, Mapping, Sequence, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL_ENV = "CONTROL_PLANE_INSTANCE_URL"
DEFAULT_TOKEN_ENV = "CONTROL_PLANE_TOKEN"

Opener = Callable[[Request], object]


def main(argv: Sequence[str] | None = None) -> None:
    """Run the console entry point."""

    raise SystemExit(run(argv))


def run(
    argv: Sequence[str] | None = None,
    *,
    opener: Opener = urlopen,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    env: Mapping[str, str] = os.environ,
) -> int:
    """Run the CLI and return a process status code."""

    parser = _parser()
    args = parser.parse_args(argv)
    base_url = args.base_url or env.get(DEFAULT_BASE_URL_ENV)
    if not base_url:
        stderr.write(f"--base-url or {DEFAULT_BASE_URL_ENV} is required\n")
        return 2
    token = args.token or env.get(DEFAULT_TOKEN_ENV, "")
    try:
        payload = _read_json(_request_for(args, base_url=base_url, token=token), opener=opener)
    except HTTPError as exc:
        stderr.write(_http_error_message(exc))
        stderr.write("\n")
        return 1
    except URLError as exc:
        stderr.write(f"request failed: {exc.reason}\n")
        return 1
    stdout.write(json.dumps(payload, indent=2, sort_keys=True))
    stdout.write("\n")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="control-plane-kit", description="Read a control-plane instance API.")
    parser.add_argument("--base-url", help=f"Instance read API base URL. Defaults to ${DEFAULT_BASE_URL_ENV}.")
    parser.add_argument("--token", help=f"Bearer/control-plane token. Defaults to ${DEFAULT_TOKEN_ENV}.")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in (
        "workspace",
        "current-graph",
        "desired-graph",
        "operator-graph",
        "activity",
        "observed-state",
        "control-surface",
    ):
        subparser = subcommands.add_parser(command)
        subparser.add_argument("workspace_id")
    subcommands.choices["operator-graph"].add_argument("--pointer", default="current")
    subcommands.choices["control-surface"].add_argument("--pointer", default="current")
    subcommands.choices["activity"].add_argument("--limit", type=int, default=50)
    return parser


def _request_for(args: argparse.Namespace, *, base_url: str, token: str) -> Request:
    url = _url_for(args, base_url=base_url)
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return Request(url, headers=headers, method="GET")


def _url_for(args: argparse.Namespace, *, base_url: str) -> str:
    base = base_url.rstrip("/")
    workspace = _quote_path(args.workspace_id)
    match args.command:
        case "workspace":
            return f"{base}/workspaces/{workspace}"
        case "current-graph":
            return f"{base}/workspaces/{workspace}/graphs/current"
        case "desired-graph":
            return f"{base}/workspaces/{workspace}/graphs/desired"
        case "operator-graph":
            return f"{base}/workspaces/{workspace}/operator-graph?{urlencode({'pointer': args.pointer})}"
        case "activity":
            return f"{base}/workspaces/{workspace}/activity?{urlencode({'limit': args.limit})}"
        case "observed-state":
            return f"{base}/workspaces/{workspace}/observed-state"
        case "control-surface":
            return f"{base}/workspaces/{workspace}/control-surface?{urlencode({'pointer': args.pointer})}"
        case _:
            raise ValueError(f"unknown command {args.command!r}")


def _quote_path(value: str) -> str:
    return quote(value, safe="")


def _read_json(request: Request, *, opener: Opener) -> object:
    with opener(request) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_error_message(error: HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return f"request failed with HTTP {error.code}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if detail:
        return f"request failed with HTTP {error.code}: {detail}"
    return f"request failed with HTTP {error.code}"


if __name__ == "__main__":
    main()
