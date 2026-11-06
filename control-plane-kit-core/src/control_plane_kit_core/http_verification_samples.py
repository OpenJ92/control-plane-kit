"""Pure named HTTP sample contracts; no signing, current authority or delivery.

Operations admits the whole check. Its live interpreter owns the volatile
ordinal; these values neither admit nor resume that sequence. Receivers resolve
the installed check independently. An authenticated repeat may execute again.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from typing import ClassVar

from control_plane_kit_core._node_control_public_wire import (
    canonical_json_bytes, digest_violation, epoch_violation,
    identifier_violation, reference_violation,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.receiver_identity import (
    NodeControlAuthorityContext, NodeControlAuthorityContextCodec,
    NodeControlReceiverTarget, NodeControlReceiverTargetCodec,
)
from control_plane_kit_core.verification import (
    HttpCheck, HttpVerificationEvidence, VerificationOutcome, VerificationPolicy,
    verification_check_from_descriptor,
)

MAX_HTTP_VERIFICATION_SAMPLE_REQUEST_BYTES = 4096
MAX_HTTP_VERIFICATION_SAMPLE_GRANT_BYTES = 8192
MAX_HTTP_VERIFICATION_SAMPLE_RESULT_BYTES = 2048
MAX_HTTP_VERIFICATION_SAMPLE_GRANT_LIFETIME_SECONDS = 20
_CANONICALIZATION = "jcs-rfc8785.v1"
_ERROR = "HTTP verification sample is invalid"
_INPUT_ERRORS = (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError)
_PURPOSES = (DelegationKeyPurpose.WORKLOAD_HTTP_VERIFICATION_SAMPLE,
             DelegationKeyPurpose.GATEWAY_HTTP_VERIFICATION_SAMPLE_TRANSIT)
_REQUEST_FIELDS = (
    "declaration_identity", "route_id", "run_id", "activity_id", "outer_attempt",
    "effect_id", "request_fingerprint", "check_id", "check_digest", "sample_ordinal", "request_id",
)


class HttpVerificationSampleError(ValueError):
    """Fixed refusal text with detached parser cause and context."""


def _require(condition):
    if not condition:
        raise HttpVerificationSampleError(_ERROR)


def _checked(action):
    try:
        return action()
    except _INPUT_ERRORS:
        pass
    raise HttpVerificationSampleError(_ERROR)


def _text(value, validator=reference_violation):
    _require(type(value) is str and validator(value) is None)


def _bounded(document, maximum):
    _require(len(canonical_json_bytes(document)) <= maximum)
    return document


def _closed(document, keys, maximum):
    _require(type(document) is dict and all(type(key) is str for key in document)
             and set(document) == set(keys))
    return _bounded(document, maximum)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _raw(raw, maximum):
    _require(type(raw) is bytes and 1 <= len(raw) <= maximum)
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    _require(canonical_json_bytes(document) == raw)
    return document


def _check(check):
    _require(type(check) is HttpCheck and type(check.policy) is VerificationPolicy)
    # Reuse the complete existing descriptor, including cadence and body intent.
    _require(verification_check_from_descriptor(check.descriptor()) == check)
    return check


def http_verification_check_digest(check: HttpCheck) -> str:
    """SHA-256 of the complete original canonical HttpCheck descriptor."""
    return _checked(lambda: sha256(canonical_json_bytes(_check(check).descriptor())).hexdigest())


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class HttpVerificationSampleRequest:
    target: NodeControlReceiverTarget
    authority_context: NodeControlAuthorityContext
    declaration_identity: str
    gateway_target: NodeControlReceiverTarget
    route_id: str
    run_id: str
    activity_id: str
    outer_attempt: int
    effect_id: str
    request_fingerprint: str
    check_id: str
    check_digest: str
    sample_ordinal: int
    request_id: str
    profile: ClassVar[str] = "http-verification-sample-request.v1"
    operation: ClassVar[str] = "http-verification-sample"

    def __post_init__(self):
        _checked(lambda: _validate_request(self))

    def descriptor(self):
        return HttpVerificationSampleRequestCodec().encode(self)

    def canonical_bytes(self):
        return canonical_json_bytes(self.descriptor())

    def canonical_digest(self):
        return sha256(self.canonical_bytes()).hexdigest()


def _request_document(request):
    return dict(profile=HttpVerificationSampleRequest.profile,
                canonicalization=_CANONICALIZATION, operation=HttpVerificationSampleRequest.operation,
                target=NodeControlReceiverTargetCodec().encode(request.target),
                authority_context=NodeControlAuthorityContextCodec().encode(request.authority_context),
                gateway_target=NodeControlReceiverTargetCodec().encode(request.gateway_target),
                **{name: getattr(request, name) for name in _REQUEST_FIELDS})


def _validate_request(request):
    _require(type(request) is HttpVerificationSampleRequest)
    for name in ("declaration_identity", "request_fingerprint", "check_digest"):
        _text(getattr(request, name), digest_violation)
    for name in ("route_id", "run_id", "activity_id", "effect_id", "check_id", "request_id"):
        _text(getattr(request, name))
    _require(type(request.outer_attempt) is int and 1 <= request.outer_attempt <= 2**31 - 1)
    _require(type(request.sample_ordinal) is int and 1 <= request.sample_ordinal <= 10)
    document = _request_document(request)
    _require(request.target.workspace_id == request.gateway_target.workspace_id
             and request.target.runtime_id == request.gateway_target.runtime_id)
    _bounded(document, MAX_HTTP_VERIFICATION_SAMPLE_REQUEST_BYTES)


class HttpVerificationSampleRequestCodec:
    def encode(self, request):
        def encode():
            _validate_request(request)
            return _request_document(request)
        return _checked(encode)

    def decode(self, document):
        def decode():
            value = _closed(document, (*_REQUEST_FIELDS, "profile", "canonicalization", "operation",
                "target", "authority_context", "gateway_target"), MAX_HTTP_VERIFICATION_SAMPLE_REQUEST_BYTES)
            _require(value["profile"] == HttpVerificationSampleRequest.profile
                     and value["canonicalization"] == _CANONICALIZATION
                     and value["operation"] == HttpVerificationSampleRequest.operation)
            return HttpVerificationSampleRequest(
                target=NodeControlReceiverTargetCodec().decode(value["target"]),
                authority_context=NodeControlAuthorityContextCodec().decode(value["authority_context"]),
                gateway_target=NodeControlReceiverTargetCodec().decode(value["gateway_target"]),
                **{name: value[name] for name in _REQUEST_FIELDS})
        return _checked(decode)

    def decode_canonical_bytes(self, raw):
        return _checked(lambda: self.decode(_raw(raw, MAX_HTTP_VERIFICATION_SAMPLE_REQUEST_BYTES)))


def require_http_verification_sample_check(request, *, expected_target,
        expected_declaration_identity, installed_check):
    """Validate against independent installed values, without current permission."""
    def require():
        _validate_request(request)
        NodeControlReceiverTargetCodec().encode(expected_target)
        _text(expected_declaration_identity, digest_violation)
        check = _check(installed_check)
        _require(request.target == expected_target
                 and request.declaration_identity == expected_declaration_identity
                 and request.check_id == check.check_id
                 and request.check_digest == http_verification_check_digest(check)
                 and request.sample_ordinal <= check.policy.maximum_attempts
                 and not any(300 <= status < 400 for status in check.expected_statuses))
        return check
    return _checked(require)


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class HttpVerificationSampleGrant:
    request: HttpVerificationSampleRequest
    purpose: DelegationKeyPurpose
    issuer: str
    key_id: str
    audience: str
    issued_at: int
    not_before: int
    expires_at: int
    jti: str
    profile: ClassVar[str] = "http-verification-sample-grant.v1"

    def __post_init__(self):
        _checked(lambda: _validate_grant(self))

    def descriptor(self):
        return HttpVerificationSampleGrantCodec().encode(self)

    def canonical_bytes(self):
        return canonical_json_bytes(self.descriptor())


def _grant_document(grant):
    return dict(profile=HttpVerificationSampleGrant.profile, canonicalization=_CANONICALIZATION,
                purpose=grant.purpose.value, request=grant.request.descriptor(),
                request_digest=grant.request.canonical_digest(), issuer=grant.issuer,
                key_id=grant.key_id, audience=grant.audience, issued_at=grant.issued_at,
                not_before=grant.not_before, expires_at=grant.expires_at, jti=grant.jti)


def _validate_grant(grant):
    _require(type(grant) is HttpVerificationSampleGrant
             and type(grant.purpose) is DelegationKeyPurpose and grant.purpose in _PURPOSES)
    _validate_request(grant.request)
    for name in ("issuer", "audience"):
        _text(getattr(grant, name))
    for name in ("key_id", "jti"):
        _text(getattr(grant, name), identifier_violation)
    for name in ("issued_at", "not_before", "expires_at"):
        _require(epoch_violation(getattr(grant, name)) is None)
    _require(grant.issued_at <= grant.not_before < grant.expires_at
             and grant.expires_at - grant.issued_at <= MAX_HTTP_VERIFICATION_SAMPLE_GRANT_LIFETIME_SECONDS)
    _bounded(_grant_document(grant), MAX_HTTP_VERIFICATION_SAMPLE_GRANT_BYTES)


class HttpVerificationSampleGrantCodec:
    def encode(self, grant):
        def encode():
            _validate_grant(grant)
            return _grant_document(grant)
        return _checked(encode)

    def decode(self, document):
        def decode():
            value = _closed(document, ("profile", "canonicalization", "purpose", "request",
                "request_digest", "issuer", "key_id", "audience", "issued_at", "not_before",
                "expires_at", "jti"), MAX_HTTP_VERIFICATION_SAMPLE_GRANT_BYTES)
            _require(value["profile"] == HttpVerificationSampleGrant.profile
                     and value["canonicalization"] == _CANONICALIZATION)
            request = HttpVerificationSampleRequestCodec().decode(value["request"])
            _require(type(value["purpose"]) is str and value["request_digest"] == request.canonical_digest())
            return HttpVerificationSampleGrant(request=request,
                purpose=DelegationKeyPurpose(value["purpose"]),
                **{name: value[name] for name in ("issuer", "key_id", "audience",
                    "issued_at", "not_before", "expires_at", "jti")})
        return _checked(decode)

    def decode_canonical_bytes(self, raw):
        return _checked(lambda: self.decode(_raw(raw, MAX_HTTP_VERIFICATION_SAMPLE_GRANT_BYTES)))


def verify_http_verification_sample_grant(grant, request, *, expected_purpose,
        expected_issuer, expected_key_id, expected_audience, now):
    """Compare authenticated claims to independent expected scope/window.

    This does not authenticate a signature or establish current controller
    permission, installed provenance, an outstanding ordinal, or delivery.
    """
    def expected():
        _validate_request(request)
        _require(type(expected_purpose) is DelegationKeyPurpose and expected_purpose in _PURPOSES)
        _text(expected_issuer)
        _text(expected_key_id, identifier_violation)
        _text(expected_audience)
        _require(epoch_violation(now) is None)
    _checked(expected)
    try:
        _validate_grant(grant)
    except _INPUT_ERRORS:
        return False
    return (grant.request == request and grant.purpose is expected_purpose
            and grant.issuer == expected_issuer and grant.key_id == expected_key_id
            and grant.audience == expected_audience and grant.not_before <= now < grant.expires_at)


@dataclass(frozen=True, slots=True, repr=False)
class HttpVerificationSampleResult:
    request: HttpVerificationSampleRequest
    outcome: VerificationOutcome
    evidence: HttpVerificationEvidence | None = None
    profile: ClassVar[str] = "http-verification-sample-result.v1"

    def __post_init__(self):
        _checked(lambda: _validate_result(self))

    def descriptor(self, *, installed_check=None):
        return _checked(lambda: HttpVerificationSampleResultCodec(
            self.request, installed_check).encode(self))

    def canonical_bytes(self, *, installed_check=None):
        return canonical_json_bytes(self.descriptor(installed_check=installed_check))


def _result_document(result):
    return dict(profile=HttpVerificationSampleResult.profile, canonicalization=_CANONICALIZATION,
                request_id=result.request.request_id, request_digest=result.request.canonical_digest(),
                check_digest=result.request.check_digest, sample_ordinal=result.request.sample_ordinal,
                declaration_identity=result.request.declaration_identity,
                kind="sample-unavailable" if result.evidence is None else "service-sample",
                outcome=result.outcome.value,
                evidence=None if result.evidence is None else result.evidence.descriptor())


def _validate_result(result):
    _require(type(result) is HttpVerificationSampleResult and type(result.outcome) is VerificationOutcome)
    _validate_request(result.request)
    if result.evidence is None:
        _require(result.outcome in (VerificationOutcome.FAILED, VerificationOutcome.TIMED_OUT,
                                   VerificationOutcome.REJECTED))
    else:
        _require(type(result.evidence) is HttpVerificationEvidence
                 and result.outcome in (VerificationOutcome.PASSED, VerificationOutcome.FAILED,
                                        VerificationOutcome.MALFORMED))
        replace(result.evidence)
    _bounded(_result_document(result), MAX_HTTP_VERIFICATION_SAMPLE_RESULT_BYTES)


@dataclass(frozen=True, slots=True, repr=False)
class HttpVerificationSampleResultCodec:
    request: HttpVerificationSampleRequest
    installed_check: HttpCheck

    def __post_init__(self):
        self._check()

    def _check(self):
        return _checked(lambda: require_http_verification_sample_check(self.request,
            expected_target=self.request.target,
            expected_declaration_identity=self.request.declaration_identity,
            installed_check=self.installed_check))

    def encode(self, result):
        def encode():
            check = self._check()
            _validate_result(result)
            _require(result.request == self.request)
            evidence = result.evidence
            if evidence is not None:
                _require(evidence.response_bytes <= check.policy.maximum_evidence_bytes
                         and evidence.expected_body_sha256 == check.expected_body_sha256)
                passed = (evidence.status_code in check.expected_statuses
                          and (check.expected_body_sha256 is None or evidence.body_sha256_matches is True))
                if result.outcome is VerificationOutcome.PASSED:
                    _require(passed)
                elif result.outcome is VerificationOutcome.FAILED:
                    _require(not passed)
                elif result.outcome is VerificationOutcome.MALFORMED:
                    _require(evidence.response_bytes == check.policy.maximum_evidence_bytes)
                # A capped matching prefix must remain malformed, never pass.
            return _result_document(result)
        return _checked(encode)

    def decode(self, document):
        def decode():
            self._check()
            expected = _result_document(HttpVerificationSampleResult(self.request, VerificationOutcome.FAILED))
            value = _closed(document, expected, MAX_HTTP_VERIFICATION_SAMPLE_RESULT_BYTES)
            for name in expected.keys() - {"kind", "outcome", "evidence"}:
                _require(type(value[name]) is type(expected[name]) and value[name] == expected[name])
            _require(type(value["outcome"]) is str)
            evidence = value["evidence"]
            if evidence is not None:
                fields = {"kind", "status_code", "response_bytes"}
                if self.installed_check.expected_body_sha256 is not None:
                    fields |= {"expected_body_sha256", "body_sha256_matches"}
                evidence = _closed(evidence, fields, MAX_HTTP_VERIFICATION_SAMPLE_RESULT_BYTES)
                _require(evidence["kind"] == "http")
                evidence = HttpVerificationEvidence(**{key: item for key, item in evidence.items() if key != "kind"})
            result = HttpVerificationSampleResult(self.request, VerificationOutcome(value["outcome"]), evidence)
            _require(value["kind"] == ("sample-unavailable" if evidence is None else "service-sample"))
            self.encode(result)
            return result
        return _checked(decode)

    def decode_canonical_bytes(self, raw):
        return _checked(lambda: self.decode(_raw(raw, MAX_HTTP_VERIFICATION_SAMPLE_RESULT_BYTES)))


__all__ = [
    "MAX_HTTP_VERIFICATION_SAMPLE_REQUEST_BYTES", "MAX_HTTP_VERIFICATION_SAMPLE_GRANT_BYTES",
    "MAX_HTTP_VERIFICATION_SAMPLE_RESULT_BYTES", "MAX_HTTP_VERIFICATION_SAMPLE_GRANT_LIFETIME_SECONDS",
    "HttpVerificationSampleError", "HttpVerificationSampleRequest", "HttpVerificationSampleRequestCodec",
    "HttpVerificationSampleGrant", "HttpVerificationSampleGrantCodec",
    "HttpVerificationSampleResult", "HttpVerificationSampleResultCodec",
    "http_verification_check_digest", "require_http_verification_sample_check",
    "verify_http_verification_sample_grant",
]
