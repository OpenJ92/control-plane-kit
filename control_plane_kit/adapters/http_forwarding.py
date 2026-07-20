"""Bounded HTTP forwarding owned by the transport adapter layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import httpx


@dataclass(frozen=True)
class ForwardedHttpResponse:
    status_code: int
    body: bytes
    content_type: str | None
    headers: Mapping[str, str]


async def forward_http_request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    body: bytes,
    timeout_seconds: float = 10.0,
    max_response_bytes: int = 1_048_576,
) -> ForwardedHttpResponse:
    """Forward one request without redirects and return bounded response data."""

    try:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(timeout_seconds),
        ) as client:
            response = await client.request(method, url, headers=headers, content=body)
    except httpx.TimeoutException as exc:
        raise TimeoutError("HTTP request timed out") from exc
    if len(response.content) > max_response_bytes:
        raise ValueError("active target response too large")
    return ForwardedHttpResponse(
        response.status_code,
        response.content,
        response.headers.get("content-type"),
        dict(response.headers),
    )


def forward_http_request_sync(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    body: bytes,
    timeout_seconds: float = 10.0,
    max_response_bytes: int = 1_048_576,
) -> ForwardedHttpResponse:
    """Synchronously forward one bounded request without following redirects."""

    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(timeout_seconds),
        ) as client:
            with client.stream(method, url, headers=headers, content=body) as response:
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_response_bytes:
                        raise ValueError("active target response too large")
                    chunks.append(chunk)
                return ForwardedHttpResponse(
                    response.status_code,
                    b"".join(chunks),
                    response.headers.get("content-type"),
                    dict(response.headers),
                )
    except httpx.TimeoutException as exc:
        raise TimeoutError("HTTP request timed out") from exc
