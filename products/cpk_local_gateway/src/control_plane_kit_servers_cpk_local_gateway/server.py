"""Local runtime-island gateway for closed CPK probes."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import sys
from typing import Mapping
from urllib import error, parse, request

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn


_TARGET_ID = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_MAX_TARGETS = 128
_MAX_RESPONSE_BYTES = 16_384
_DEFAULT_PORT = 8000


class GatewayConfigurationError(ValueError):
    """Raised when gateway configuration or a probe request is malformed."""


@dataclass(frozen=True, slots=True)
class GatewayTarget:
    target_id: str
    protocol: str
    url: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password_environment: str | None = None

    @classmethod
    def from_descriptor(
        cls,
        target_id: str,
        descriptor: Mapping[str, object],
    ) -> "GatewayTarget":
        _validate_target_id(target_id)
        protocol = _required_text(descriptor, "protocol")
        if protocol == "http":
            return cls(
                target_id=target_id,
                protocol=protocol,
                url=_http_url(_required_text(descriptor, "url"), "url"),
            )
        if protocol == "postgres":
            return cls(
                target_id=target_id,
                protocol=protocol,
                host=_host(_required_text(descriptor, "host"), "host"),
                port=_port(descriptor.get("port", 5432)),
                database=_optional_text(descriptor.get("database"), "database"),
                username=_optional_text(descriptor.get("username"), "username"),
                password_environment=_optional_environment_name(
                    descriptor.get("password_environment"),
                    "password_environment",
                ),
            )
        raise GatewayConfigurationError("unsupported target protocol")

    def descriptor(self) -> dict[str, object]:
        if self.protocol == "http":
            return {"protocol": "http", "url": self.url}
        if self.protocol == "postgres":
            descriptor = {
                "protocol": "postgres",
                "host": self.host,
                "port": self.port,
            }
            if self.database is not None:
                descriptor["database"] = self.database
            if self.username is not None:
                descriptor["username"] = self.username
            if self.password_environment is not None:
                descriptor["password_environment"] = self.password_environment
            return descriptor
        raise GatewayConfigurationError("unsupported target protocol")


@dataclass(frozen=True, slots=True)
class GatewayConfiguration:
    targets: Mapping[str, GatewayTarget]
    port: int = _DEFAULT_PORT

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "GatewayConfiguration":
        values = environment or os.environ
        raw_targets = values.get("CPK_GATEWAY_TARGETS_JSON", "{}")
        try:
            target_map = json.loads(raw_targets)
        except json.JSONDecodeError as exc:
            raise GatewayConfigurationError(
                "CPK_GATEWAY_TARGETS_JSON is invalid JSON"
            ) from exc
        port = _port(values.get("PORT", str(_DEFAULT_PORT)))
        return cls.from_target_map(target_map, port=port)

    @classmethod
    def from_target_map(
        cls,
        target_map: Mapping[str, object],
        *,
        port: int = _DEFAULT_PORT,
    ) -> "GatewayConfiguration":
        if not isinstance(target_map, Mapping):
            raise GatewayConfigurationError("gateway target map must be an object")
        if len(target_map) > _MAX_TARGETS:
            raise GatewayConfigurationError("gateway target map has too many targets")
        targets: dict[str, GatewayTarget] = {}
        for target_id, descriptor in target_map.items():
            if not isinstance(target_id, str) or not isinstance(descriptor, Mapping):
                raise GatewayConfigurationError("gateway target entry is malformed")
            targets[target_id] = GatewayTarget.from_descriptor(target_id, descriptor)
        return cls(targets=dict(sorted(targets.items())), port=_port(port))


class NoRedirects(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise error.HTTPError(req.full_url, code, "redirects are disabled", headers, fp)


def create_app(configuration: GatewayConfiguration | None = None) -> FastAPI:
    gateway = configuration or GatewayConfiguration.from_environment()
    app = FastAPI(title="cpk-local-gateway")

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready")
    def ready() -> dict[str, object]:
        return {"status": "ready", "targets": len(gateway.targets)}

    @app.post("/cpk/probes")
    async def probe(inbound: Request) -> JSONResponse:
        try:
            payload = await inbound.json()
            result = execute_probe(gateway, payload)
        except GatewayConfigurationError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "outcome": "failed",
                    "code": "gateway.probe-rejected",
                    "message": str(exc),
                },
            )
        return JSONResponse(status_code=200, content=result)

    return app


def execute_probe(
    configuration: GatewayConfiguration,
    payload: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise GatewayConfigurationError("probe request must be an object")
    kind = _required_text(payload, "kind")
    target_id = _required_text(payload, "target_id")
    target = configuration.targets.get(target_id)
    if target is None:
        raise GatewayConfigurationError("unknown target")
    if kind == "http-status":
        return _execute_http_probe(target, payload)
    if kind == "postgres-select-one":
        return _execute_postgres_probe(target)
    raise GatewayConfigurationError("unsupported probe kind")


def main() -> int:
    try:
        configuration = GatewayConfiguration.from_environment()
    except GatewayConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    uvicorn.run(create_app(configuration), host="0.0.0.0", port=configuration.port)
    return 0


def _execute_http_probe(
    target: GatewayTarget,
    payload: Mapping[str, object],
) -> dict[str, object]:
    if target.protocol != "http" or target.url is None:
        raise GatewayConfigurationError("target does not support HTTP probes")
    path = str(payload.get("path", "/health/ready"))
    if not path.startswith("/") or "\x00" in path or "://" in path:
        raise GatewayConfigurationError("HTTP probe path is malformed")
    result = _http_status(target.url.rstrip("/") + path)
    status = int(result["status"])
    return {
        "outcome": "passed" if 200 <= status < 400 else "failed",
        "target_id": target.target_id,
        "probe": "http-status",
        "status": status,
        "body_size": int(result["body_size"]),
    }


def _execute_postgres_probe(target: GatewayTarget) -> dict[str, object]:
    if target.protocol != "postgres" or target.host is None or target.port is None:
        raise GatewayConfigurationError("target does not support Postgres probes")
    _postgres_select_one(target)
    return {
        "outcome": "passed",
        "target_id": target.target_id,
        "probe": "postgres-select-one",
    }


def _http_status(url: str) -> dict[str, int]:
    opener = request.build_opener(NoRedirects)
    outbound = request.Request(url, method="GET")
    with opener.open(outbound, timeout=5) as response:
        payload = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(payload) > _MAX_RESPONSE_BYTES:
            raise GatewayConfigurationError("HTTP probe response is too large")
        return {"status": int(response.status), "body_size": len(payload)}


def _postgres_select_one(target: GatewayTarget) -> None:
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise GatewayConfigurationError(
            "Postgres probe requires psycopg in the gateway image"
        ) from exc
    password = (
        os.environ.get(target.password_environment)
        if target.password_environment is not None
        else None
    )
    try:
        with psycopg.connect(
            host=target.host,
            port=target.port,
            dbname=target.database,
            user=target.username,
            password=password,
            connect_timeout=5,
            autocommit=True,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    raise GatewayConfigurationError("Postgres probe returned unexpected row")
    except Exception as exc:
        raise GatewayConfigurationError(
            f"Postgres probe failed: {type(exc).__name__}"
        ) from exc


def _required_text(value: Mapping[str, object], key: str) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate.strip() or "\x00" in candidate:
        raise GatewayConfigurationError(f"{key} must be bounded text")
    _reject_secret_text(candidate, key)
    return candidate


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise GatewayConfigurationError(f"{name} must be bounded text")
    _reject_secret_text(value, name)
    return value


def _optional_environment_name(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", value):
        raise GatewayConfigurationError(f"{name} must be an environment name")
    return value


def _http_url(value: str, name: str) -> str:
    candidate = value.strip().rstrip("/")
    parsed = parse.urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise GatewayConfigurationError(f"{name} must be an absolute HTTP URL")
    if parsed.username or parsed.password:
        raise GatewayConfigurationError(f"{name} must not contain credentials")
    _host(parsed.hostname or "", name)
    return candidate


def _host(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise GatewayConfigurationError(f"{name} is malformed")
    if any(marker in value.lower() for marker in ("secret", "token", "password")):
        raise GatewayConfigurationError(f"{name} is secret-shaped")
    return value


def _port(value: object) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise GatewayConfigurationError("port must be an integer") from exc
    if not 1 <= port <= 65_535:
        raise GatewayConfigurationError("port must be between 1 and 65535")
    return port


def _validate_target_id(value: str) -> None:
    if not _TARGET_ID.fullmatch(value):
        raise GatewayConfigurationError("target id is malformed")


def _reject_secret_text(value: str, name: str) -> None:
    lowered = value.lower()
    if any(marker in lowered for marker in ("secret", "token", "password", "api_key")):
        raise GatewayConfigurationError(f"{name} is secret-shaped")


if __name__ == "__main__":
    raise SystemExit(main())
