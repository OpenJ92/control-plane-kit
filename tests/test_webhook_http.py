from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import unittest

import httpx

from control_plane_kit.secrets import (
    SecretProviderAuthority,
    SecretProviderId,
    SecretReference,
    SecretResolved,
    SecretValue,
)
from control_plane_kit.webhook import (
    HttpWebhookDelivery,
    WebhookAddressPolicy,
    WebhookAttemptOutcome,
    WebhookContentType,
    WebhookDeliveryIdentity,
    WebhookEndpoint,
    WebhookEndpointGrant,
    WebhookEndpointScope,
    WebhookHttpLimits,
    WebhookOutboundRequest,
    WebhookPayload,
    WebhookSigning,
)


@dataclass
class Resolver:
    value: str = "signing-secret"

    authority = SecretProviderAuthority(SecretProviderId("test"))

    def __post_init__(self) -> None:
        self.references: list[SecretReference] = []

    def resolve(self, reference: SecretReference) -> SecretResolved:
        self.references.append(reference)
        return SecretResolved(reference, SecretValue(self.value))


@dataclass
class PublicResolver:
    addresses: tuple[str, ...]

    def resolve(self, hostname: str) -> tuple[str, ...]:
        self.hostname = hostname
        return self.addresses


class WebhookHttpTests(unittest.TestCase):
    def test_exact_local_grant_signs_body_only_at_dispatch_boundary(self) -> None:
        seen: list[httpx.Request] = []
        resolver = Resolver()

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(204, content=b"")

        delivery = HttpWebhookDelivery(
            resolver,
            _policy("http://127.0.0.1:8080/hooks/orders", WebhookEndpointScope.HOST_LOCAL),
            transport=httpx.MockTransport(handler),
        )
        request = _request("http://127.0.0.1:8080/hooks/orders", signed=True)

        result = delivery.deliver(request)

        self.assertIs(result.outcome, WebhookAttemptOutcome.SUCCEEDED)
        self.assertEqual(result.response_status, 204)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].content, request.payload.body)
        expected = hmac.new(b"signing-secret", request.payload.body, hashlib.sha256).hexdigest()
        self.assertEqual(seen[0].headers["x-cpk-webhook-signature"], f"sha256={expected}")
        self.assertEqual(resolver.references, [SecretReference("secret://test/webhook-key")])
        self.assertNotIn("signing-secret", repr(delivery))

    def test_endpoint_must_match_exact_grant_before_secret_resolution(self) -> None:
        resolver = Resolver("never-resolve")
        delivery = HttpWebhookDelivery(
            resolver,
            _policy("http://127.0.0.1:8080/hooks/allowed", WebhookEndpointScope.HOST_LOCAL),
            transport=httpx.MockTransport(lambda _request: httpx.Response(204)),
        )

        result = delivery.deliver(
            _request("http://127.0.0.1:8080/hooks/not-allowed", signed=True)
        )

        self.assertIs(result.outcome, WebhookAttemptOutcome.TERMINAL_FAILURE)
        self.assertEqual(result.failure_code, "webhook.destination-or-signing-rejected")
        self.assertEqual(resolver.references, [])
        self.assertNotIn("not-allowed", str(result))
        self.assertNotIn("never-resolve", str(result))

    def test_public_hostname_is_resolved_and_pinned_for_same_request(self) -> None:
        seen: list[httpx.Request] = []
        public = PublicResolver(("93.184.216.34",))
        delivery = HttpWebhookDelivery(
            Resolver(),
            _policy("https://hooks.example.test/orders", WebhookEndpointScope.PUBLIC),
            public_resolver=public,
            transport=httpx.MockTransport(
                lambda request: seen.append(request) or httpx.Response(202)
            ),
        )

        result = delivery.deliver(_request("https://hooks.example.test/orders"))

        self.assertIs(result.outcome, WebhookAttemptOutcome.SUCCEEDED)
        self.assertEqual(seen[0].url.host, "93.184.216.34")
        self.assertEqual(seen[0].headers["host"], "hooks.example.test")
        self.assertEqual(seen[0].extensions["sni_hostname"], "hooks.example.test")
        self.assertEqual(public.hostname, "hooks.example.test")

    def test_public_dns_rebinding_and_public_plaintext_fail_closed(self) -> None:
        cases = (
            (
                "https://hooks.example.test/orders",
                PublicResolver(("93.184.216.34", "127.0.0.1")),
            ),
            ("http://hooks.example.test/orders", PublicResolver(("93.184.216.34",))),
        )
        for url, resolver in cases:
            with self.subTest(url=url):
                delivery = HttpWebhookDelivery(
                    Resolver(),
                    _policy(url, WebhookEndpointScope.PUBLIC),
                    public_resolver=resolver,
                    transport=httpx.MockTransport(lambda _request: httpx.Response(204)),
                )
                result = delivery.deliver(_request(url))
                self.assertIs(result.outcome, WebhookAttemptOutcome.TERMINAL_FAILURE)

    def test_runtime_private_scope_rejects_other_address_classes(self) -> None:
        for host in ("127.0.0.1", "169.254.169.254", "93.184.216.34", "0.0.0.0"):
            url = f"http://{host}:8080/hook"
            with self.subTest(host=host):
                result = HttpWebhookDelivery(
                    Resolver(),
                    _policy(url, WebhookEndpointScope.RUNTIME_PRIVATE),
                    transport=httpx.MockTransport(lambda _request: httpx.Response(204)),
                ).deliver(_request(url))
                self.assertIs(result.outcome, WebhookAttemptOutcome.TERMINAL_FAILURE)

        private_url = "http://10.0.0.2:8080/hook"
        permitted = HttpWebhookDelivery(
            Resolver(),
            _policy(private_url, WebhookEndpointScope.RUNTIME_PRIVATE),
            transport=httpx.MockTransport(lambda _request: httpx.Response(204)),
        ).deliver(_request(private_url))
        self.assertIs(permitted.outcome, WebhookAttemptOutcome.SUCCEEDED)

    def test_http_statuses_redirects_and_transport_failures_are_distinct(self) -> None:
        cases = (
            (httpx.Response(302), WebhookAttemptOutcome.TERMINAL_FAILURE, "http.redirect-rejected"),
            (httpx.Response(400), WebhookAttemptOutcome.TERMINAL_FAILURE, "http.rejected"),
            (httpx.Response(429), WebhookAttemptOutcome.RETRYABLE_FAILURE, "http.retryable-response"),
            (httpx.Response(503), WebhookAttemptOutcome.RETRYABLE_FAILURE, "http.retryable-response"),
            (httpx.ConnectError("secret connect detail"), WebhookAttemptOutcome.RETRYABLE_FAILURE, "http.connect-failure"),
            (httpx.ReadTimeout("secret timeout detail"), WebhookAttemptOutcome.UNCERTAIN, "http.timeout"),
        )
        for response, outcome, code in cases:
            with self.subTest(response=type(response).__name__):
                def handler(_request, response=response):
                    if isinstance(response, BaseException):
                        raise response
                    return response

                result = HttpWebhookDelivery(
                    Resolver(),
                    _policy("http://127.0.0.1:8080/hook", WebhookEndpointScope.HOST_LOCAL),
                    transport=httpx.MockTransport(handler),
                ).deliver(_request("http://127.0.0.1:8080/hook"))
                self.assertIs(result.outcome, outcome)
                self.assertEqual(result.failure_code, code)
                self.assertNotIn("secret", str(result))

    def test_response_body_and_headers_are_bounded_after_send(self) -> None:
        responses = (
            httpx.Response(200, content=b"x" * 33),
            httpx.Response(200, headers={"x-large": "x" * 65}),
        )
        for response in responses:
            with self.subTest(headers=dict(response.headers)):
                result = HttpWebhookDelivery(
                    Resolver(),
                    _policy("http://127.0.0.1:8080/hook", WebhookEndpointScope.HOST_LOCAL),
                    limits=WebhookHttpLimits(response_bytes=32, response_header_bytes=64),
                    transport=httpx.MockTransport(lambda _request, response=response: response),
                ).deliver(_request("http://127.0.0.1:8080/hook"))
                self.assertIs(result.outcome, WebhookAttemptOutcome.UNCERTAIN)


def _policy(url: str, scope: WebhookEndpointScope) -> WebhookAddressPolicy:
    return WebhookAddressPolicy((WebhookEndpointGrant("orders", url, scope),))


def _request(url: str, *, signed: bool = False) -> WebhookOutboundRequest:
    return WebhookOutboundRequest(
        WebhookDeliveryIdentity("workspace-a", "delivery-a"),
        WebhookEndpoint("orders", url),
        WebhookPayload(WebhookContentType.JSON, b'{"order_id":42}'),
        WebhookSigning(SecretReference("secret://test/webhook-key")) if signed else None,
        "claim-a",
        1,
    )


if __name__ == "__main__":
    unittest.main()
