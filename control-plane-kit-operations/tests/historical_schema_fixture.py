"""Immutable historical SQL and catalog observations for the existing upgrades."""

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from control_plane_kit_operations.postgres.current_schema_contract import CURRENT_POSTGRES_SCHEMA_CONTRACT
from control_plane_kit_operations.postgres.current_schema_verification import _CURRENT_SCHEMA_CONTRACT_QUERY


WORKSPACE_SOURCE_SHA256 = "c8cc42059c06c898275ba19aa2a6bf8df3d0e93474bb23340bab7b49bcfc9141"
PROVIDER_SOURCE_SHA256 = "7505500ae7c663a3cc1265a16fc19abc103c45284e38a2ab297a13cdf276e061"
HISTORICAL_TARGET_SHA256 = "7288fee837b51ffd6e470ed33395159aa143804665e7e9aac0f219cc57fb43de"
FRESH_CURRENT_SHA256 = "75b6b900f70a170376c1133af7a066df11b27a147c29075230c26a94ab219d25"


def historical_sql(name):
    expected = {
        "pre_workspace_permissions": "a38cb3590078ab33454ff26c14a9e1a3f3705aff01834533209aada14aa03dc1",
        "pre_provider_policy_commands": "fe63d2f4171a0672d4b0b3a111a25ba6e8bfd7258bc7c9853570eb2ea8c7bdde",
    }[name]
    data = (Path(__file__).parent / "fixtures" / "historical_schema" / (name + ".sql")).read_bytes()
    if sha256(data).hexdigest() != expected:
        raise AssertionError("historical schema fixture bytes changed: " + name)
    return data.decode("utf-8")


def contract_sha256(contract):
    return _fingerprint(asdict(contract))


def _fingerprint(contract):
    payload = {"domain": "control-plane-kit.operations.postgres.current-schema",
               "format_version": 1, "contract": contract}
    return sha256(json.dumps(payload, ensure_ascii=True, sort_keys=True,
                             separators=(",", ":")).encode("ascii")).hexdigest()


def catalog_sha256(connection):
    # Observe PostgreSQL through the owner's existing bounded semantic query;
    # do not synthesize the old contract by subtracting today's CHECK domains.
    query, separator, _ = _CURRENT_SCHEMA_CONTRACT_QUERY.partition("\nSELECT\n  COALESCE(")
    if not separator:
        raise AssertionError("semantic verifier query boundary changed")
    order = {"relations": "relname", "columns": "relname, attnum",
             "constraints": "relname, conname", "indexes": "relname, index_name"}
    bounds = tuple(len(getattr(CURRENT_POSTGRES_SCHEMA_CONTRACT, name)) + 1 for name in order)
    observed = {name: [row[0] for row in connection.execute(
        query + "\nSELECT value FROM semantic_" + name + " ORDER BY " + ordering,
        bounds,
    ).fetchall()] for name, ordering in order.items()}
    return _fingerprint(observed)


def namespace_state(connection):
    objects = connection.execute("SELECT relname,relkind,oid FROM pg_class "
        "WHERE relnamespace=current_schema()::regnamespace ORDER BY relname").fetchall()
    rows = {name: connection.execute(
        f'SELECT to_jsonb(retained) FROM "{name}" AS retained ORDER BY to_jsonb(retained)::text'
    ).fetchall() for name, kind, _ in objects if kind == "r"}
    return objects, rows, catalog_sha256(connection)
