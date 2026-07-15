"""Read-only command-line interface for control-plane instance projections."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from typing import Any

from control_plane_kit.read_services import InstanceReadService
from control_plane_kit.stores import PostgresStoreBundle


def main(argv: Sequence[str] | None = None) -> int:
    """Run the `cpk-read` command."""

    parser = _parser()
    args = parser.parse_args(argv)
    database_url = args.database_url or os.environ.get("CPK_DATABASE_URL")
    if not database_url:
        parser.error("database URL is required via --database-url or CPK_DATABASE_URL")

    try:
        descriptor = _read_descriptor(
            database_url=database_url,
            workspace_id=args.workspace_id,
            command=args.command,
            limit=getattr(args, "limit", None),
            include_addresses=args.include_addresses,
        )
    except KeyError as exc:
        print(f"cpk-read: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"cpk-read: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"cpk-read: {exc}", file=sys.stderr)
        return 2
    if descriptor is None:
        print(f"cpk-read: {args.command} is not assigned for workspace {args.workspace_id!r}", file=sys.stderr)
        return 1
    print(json.dumps(descriptor, indent=2, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cpk-read",
        description="Read control-plane instance projections from Postgres.",
    )
    parser.add_argument("--database-url", help="Postgres DSN. Defaults to CPK_DATABASE_URL.")
    parser.add_argument(
        "--include-addresses",
        action="store_true",
        help="Include graph endpoint URLs and env assignments where supported.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in (
        "workspace",
        "current-graph",
        "desired-graph",
        "control-surface",
    ):
        subparser = subcommands.add_parser(command)
        subparser.add_argument("workspace_id")
    activity = subcommands.add_parser("activity")
    activity.add_argument("workspace_id")
    activity.add_argument("--limit", type=int, default=50)
    observed = subcommands.add_parser("observed-state")
    observed.add_argument("workspace_id")
    observed.add_argument("--limit", type=int, default=100)
    return parser


def _read_descriptor(
    *,
    database_url: str,
    workspace_id: str,
    command: str,
    limit: int | None,
    include_addresses: bool,
) -> dict[str, object] | None:
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Postgres CLI reads require psycopg. Install a package extra that includes psycopg."
        ) from exc

    with psycopg.connect(database_url, autocommit=True) as connection:
        stores = PostgresStoreBundle(connection)
        service = InstanceReadService(
            workspace_store=stores.workspace,
            graph_store=stores.graph_topology,
            activity_history_store=stores.activity_history,
            observed_state_store=stores.observed_state,
            include_addresses=include_addresses,
        )
        read_model = _dispatch(service, workspace_id=workspace_id, command=command, limit=limit)
        return read_model.descriptor() if read_model is not None else None


def _dispatch(
    service: InstanceReadService,
    *,
    workspace_id: str,
    command: str,
    limit: int | None,
) -> Any:
    match command:
        case "workspace":
            return service.workspace(workspace_id)
        case "current-graph":
            return service.current_graph(workspace_id)
        case "desired-graph":
            return service.desired_graph(workspace_id)
        case "activity":
            return service.activity_timeline(workspace_id, limit=50 if limit is None else limit)
        case "observed-state":
            return service.observed_state(workspace_id, limit=100 if limit is None else limit)
        case "control-surface":
            return service.control_surface(workspace_id)
        case _:
            raise RuntimeError(f"unknown command {command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
