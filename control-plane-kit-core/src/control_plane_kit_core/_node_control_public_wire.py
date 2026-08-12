"""Private shared admissibility laws for public node-control wire values."""

from __future__ import annotations

from enum import StrEnum
import ipaddress
import re

import rfc8785


_MAX_IDENTIFIER = 128
_MAX_REFERENCE = 256
_MAX_SAFE_INTEGER = 2**53 - 1
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ASCII_PERCENT_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_AUTHORIZATION_ENVELOPE = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])(?:"
    r"authorization[ \t]*:[ \t]*[A-Za-z][A-Za-z0-9._+-]*[ \t]+"
    r"|bearer[ \t]+"
    r")[^\s,;]+"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])"
    r"(?:credential|password|secret|signature|token)"
    r"[ \t]*=[ \t]*[^\s,;]+"
)
_PRIVATE_KEY_ARMOR = re.compile(
    r"(?i)-----begin(?: [A-Za-z0-9]+)* private key-----"
)
_COMPACT_TOKEN = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:sk-|sg\.)[A-Za-z0-9][A-Za-z0-9._-]*"
)
_SCHEME_ENDPOINT = re.compile(r"(?i)[A-Za-z][A-Za-z0-9+.-]*://[^\s/]")
_PROTOCOL_RELATIVE_ENDPOINT = re.compile(r"(?:^|[\s(\"'=])//[^\s/]")
_HOST_PORT_ENDPOINT = re.compile(
    r"(?<![A-Za-z0-9._:\[\]-])"
    r"(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9][A-Za-z0-9.-]*):(\d{1,5})"
    r"(?![A-Za-z0-9])"
)
_ENDPOINT_TOKEN_SPLIT = re.compile(r"[\s,;(){}<>\"']+")


class NodeControlPublicWireViolation(StrEnum):
    IDENTIFIER_SHAPE = "identifier-shape"
    REFERENCE_SHAPE = "reference-shape"
    DIGEST_SHAPE = "digest-shape"
    EPOCH_BOUNDS = "epoch-bounds"
    CREDENTIAL_ENVELOPE = "credential-envelope"
    ENDPOINT_ENVELOPE = "endpoint-envelope"


class NodeControlCanonicalDomainError(ValueError):
    pass


def canonical_json_bytes(value: object) -> bytes:
    try:
        encoded = rfc8785.dumps(value)
    except rfc8785.CanonicalizationError:
        pass
    else:
        return encoded
    raise NodeControlCanonicalDomainError("outside the canonical JSON domain")


def public_material_violation(
    text: str,
) -> NodeControlPublicWireViolation | None:
    projections = (text, _ascii_percent_projection(text))
    if any(_contains_credential_envelope(candidate) for candidate in projections):
        return NodeControlPublicWireViolation.CREDENTIAL_ENVELOPE
    if any(_contains_endpoint_envelope(candidate) for candidate in projections):
        return NodeControlPublicWireViolation.ENDPOINT_ENVELOPE
    return None


def identifier_violation(value: object) -> NodeControlPublicWireViolation | None:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_IDENTIFIER
        or not _IDENTIFIER.fullmatch(value)
    ):
        return NodeControlPublicWireViolation.IDENTIFIER_SHAPE
    return public_material_violation(value)


def reference_violation(value: object) -> NodeControlPublicWireViolation | None:
    if (
        not isinstance(value, str)
        or len(value) > _MAX_REFERENCE
        or not _REFERENCE.fullmatch(value)
    ):
        return NodeControlPublicWireViolation.REFERENCE_SHAPE
    return public_material_violation(value)


def digest_violation(value: object) -> NodeControlPublicWireViolation | None:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        return NodeControlPublicWireViolation.DIGEST_SHAPE
    return None


def epoch_violation(value: object) -> NodeControlPublicWireViolation | None:
    if type(value) is not int or value < 0 or value > _MAX_SAFE_INTEGER:
        return NodeControlPublicWireViolation.EPOCH_BOUNDS
    return None


def _ascii_percent_projection(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        decoded = int(match.group(1), 16)
        return chr(decoded) if decoded <= 0x7F else match.group(0)

    return _ASCII_PERCENT_ESCAPE.sub(replace, value)


def _contains_credential_envelope(value: str) -> bool:
    return any(
        pattern.search(value) is not None
        for pattern in (
            _AUTHORIZATION_ENVELOPE,
            _CREDENTIAL_ASSIGNMENT,
            _PRIVATE_KEY_ARMOR,
            _COMPACT_TOKEN,
        )
    )


def _contains_endpoint_envelope(value: str) -> bool:
    if (
        _SCHEME_ENDPOINT.search(value) is not None
        or _PROTOCOL_RELATIVE_ENDPOINT.search(value) is not None
    ):
        return True
    for match in _HOST_PORT_ENDPOINT.finditer(value):
        if 1 <= int(match.group(2)) <= 65_535:
            return True
    for token in _ENDPOINT_TOKEN_SPLIT.split(value):
        atom = token.strip("[]").rstrip(".")
        if not atom:
            continue
        if _is_localhost_endpoint(atom):
            return True
        try:
            ipaddress.ip_address(atom)
        except ValueError:
            continue
        return True
    return False


def _is_localhost_endpoint(atom: str) -> bool:
    lowered = atom.lower().rstrip(".")
    if ":" in lowered:
        host, separator, port = lowered.rpartition(":")
        if (
            not separator
            or not port.isdigit()
            or not 1 <= int(port) <= 65_535
        ):
            return False
        lowered = host.rstrip(".")
    return lowered == "localhost" or lowered.endswith(".localhost")
