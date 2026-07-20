"""Typed observations and pure differential comparison."""

from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import json
import math
import re


class DifferentialError(ValueError):
    pass


OBSERVATION_SCHEMA = "cpk.raw-observation"
POLICY_SCHEMA = "cpk.normalization-policy"
RESULT_SCHEMA = "cpk.differential-result"
INCIDENTAL_KINDS = {
    "allocated-port",
    "generated-id",
    "timestamp",
    "container-name",
}
MAXIMUM_CAPTURE_BYTES = 1024 * 1024
MAXIMUM_VALUE_BYTES = 1024 * 1024
MAXIMUM_TEXT_BYTES = 4096
MAXIMUM_DEPTH = 32
MAXIMUM_ITEMS = 4096
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_CONTAINER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DifferentialError(f"{label} must be non-blank text")
    if len(value.encode("utf-8")) > MAXIMUM_TEXT_BYTES:
        raise DifferentialError(f"{label} exceeds the byte bound")
    return value


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def capture_payload(value: bytes) -> dict[str, object]:
    if not isinstance(value, bytes):
        raise DifferentialError("captured payload must be bytes")
    if len(value) > MAXIMUM_CAPTURE_BYTES:
        raise DifferentialError("captured payload exceeds the byte bound")
    return {
        "encoding": "base64",
        "content": base64.b64encode(value).decode("ascii"),
        "byte_length": len(value),
        "digest": _digest(value),
    }


def _decode_payload(value: object, label: str) -> bytes:
    if not isinstance(value, dict) or set(value) != {
        "encoding",
        "content",
        "byte_length",
        "digest",
    }:
        raise DifferentialError(f"{label} payload is not closed")
    if value["encoding"] != "base64":
        raise DifferentialError(f"{label} encoding is unsupported")
    if not isinstance(value["content"], str):
        raise DifferentialError(f"{label} content must be text")
    try:
        raw = base64.b64decode(value["content"], validate=True)
    except ValueError as error:
        raise DifferentialError(f"{label} content is not canonical base64") from error
    if len(raw) > MAXIMUM_CAPTURE_BYTES or value["byte_length"] != len(raw):
        raise DifferentialError(f"{label} byte length is invalid")
    if value["digest"] != _digest(raw):
        raise DifferentialError(f"{label} digest does not match content")
    return raw


def _validate_incidental(kind: object, value: object) -> str:
    if kind not in INCIDENTAL_KINDS:
        raise DifferentialError("unknown incidental kind")
    if kind == "allocated-port":
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
            raise DifferentialError("allocated-port must be an integer port")
    elif kind == "generated-id":
        _text(value, "generated-id")
    elif kind == "timestamp":
        text = _text(value, "timestamp")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as error:
            raise DifferentialError("timestamp must be ISO-8601") from error
        if parsed.tzinfo is None:
            raise DifferentialError("timestamp must include an offset")
    elif kind == "container-name":
        text = _text(value, "container-name")
        if _CONTAINER_NAME.fullmatch(text) is None:
            raise DifferentialError("container-name has invalid syntax")
    return str(kind)


def _validate_value(value: object, *, depth: int = 0, allow_normalized: bool = False) -> None:
    if depth > MAXIMUM_DEPTH:
        raise DifferentialError("behavior exceeds the depth bound")
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DifferentialError("behavior numbers must be finite")
        return
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAXIMUM_TEXT_BYTES:
            raise DifferentialError("behavior text exceeds the byte bound")
        return
    if isinstance(value, list):
        if len(value) > MAXIMUM_ITEMS:
            raise DifferentialError("behavior list exceeds the item bound")
        for item in value:
            _validate_value(item, depth=depth + 1, allow_normalized=allow_normalized)
        return
    if not isinstance(value, dict) or len(value) > MAXIMUM_ITEMS:
        raise DifferentialError("behavior contains an unsupported value")
    if "$incidental" in value:
        if set(value) != {"$incidental"}:
            raise DifferentialError("incidental syntax cannot have sibling fields")
        tagged = value["$incidental"]
        if not isinstance(tagged, dict) or set(tagged) != {"kind", "value"}:
            raise DifferentialError("incidental syntax is not closed")
        _validate_incidental(tagged["kind"], tagged["value"])
        return
    if "$normalized" in value:
        if not allow_normalized or set(value) != {"$normalized"}:
            raise DifferentialError("normalized syntax is interpreter-owned")
        if value["$normalized"] not in INCIDENTAL_KINDS:
            raise DifferentialError("unknown normalized kind")
        return
    for key, item in value.items():
        _text(key, "behavior key")
        if key.startswith("$"):
            raise DifferentialError("unknown reserved behavior syntax")
        _validate_value(item, depth=depth + 1, allow_normalized=allow_normalized)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def decode_observation(document: dict[str, object]) -> dict[str, object]:
    required = {
        "schema",
        "implementation",
        "command",
        "process",
        "stdout",
        "stderr",
        "behavior",
        "artifacts",
    }
    if set(document) != required or document["schema"] != OBSERVATION_SCHEMA:
        raise DifferentialError("observation root is not closed")
    implementation = document["implementation"]
    if not isinstance(implementation, dict) or set(implementation) != {
        "role",
        "identity",
        "source_digest",
    }:
        raise DifferentialError("implementation identity is not closed")
    if implementation["role"] not in {"reference", "successor"}:
        raise DifferentialError("unknown implementation role")
    _text(implementation["identity"], "implementation identity")
    if not isinstance(implementation["source_digest"], str) or _DIGEST.fullmatch(implementation["source_digest"]) is None:
        raise DifferentialError("source digest must be canonical SHA-256")
    command = document["command"]
    if not isinstance(command, list) or not command or len(command) > 128:
        raise DifferentialError("command must be a bounded non-empty list")
    for argument in command:
        _text(argument, "command argument")
    process = document["process"]
    if not isinstance(process, dict) or set(process) != {"kind", "exit_code"}:
        raise DifferentialError("process result is not closed")
    if process["kind"] not in {"completed", "timeout", "output-limit", "infrastructure-failure"}:
        raise DifferentialError("unknown process result kind")
    if process["kind"] == "completed":
        if not isinstance(process["exit_code"], int) or isinstance(process["exit_code"], bool):
            raise DifferentialError("completed process requires an integer exit code")
    elif process["exit_code"] is not None:
        raise DifferentialError("incomplete process cannot claim an exit code")
    _decode_payload(document["stdout"], "stdout")
    _decode_payload(document["stderr"], "stderr")
    _validate_value(document["behavior"])
    artifacts = document["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) > 128:
        raise DifferentialError("artifacts must be a bounded list")
    names: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"name", "media_type", "payload"}:
            raise DifferentialError("artifact descriptor is not closed")
        name = _text(artifact["name"], "artifact name")
        if name in names:
            raise DifferentialError("artifact names must be unique")
        names.add(name)
        _text(artifact["media_type"], "artifact media type")
        _decode_payload(artifact["payload"], f"artifact {name}")
    if len(_canonical_bytes(document["behavior"])) > MAXIMUM_VALUE_BYTES:
        raise DifferentialError("behavior exceeds the byte bound")
    return document


def decode_policy(document: dict[str, object]) -> dict[str, object]:
    if set(document) != {"schema", "allowed"} or document["schema"] != POLICY_SCHEMA:
        raise DifferentialError("normalization policy is not closed")
    allowed = document["allowed"]
    if not isinstance(allowed, list) or not all(isinstance(kind, str) for kind in allowed):
        raise DifferentialError("normalization kinds must be a string list")
    if len(allowed) != len(set(allowed)):
        raise DifferentialError("normalization kinds must be a unique list")
    if not all(kind in INCIDENTAL_KINDS for kind in allowed):
        raise DifferentialError("normalization policy contains an unknown kind")
    return document


def normalize_value(value: object, policy: dict[str, object]) -> object:
    decode_policy(policy)
    _validate_value(value, allow_normalized=True)
    allowed = frozenset(policy["allowed"])

    def visit(current: object) -> object:
        if isinstance(current, list):
            return [visit(item) for item in current]
        if not isinstance(current, dict):
            return current
        if "$normalized" in current:
            return dict(current)
        if "$incidental" in current:
            tagged = current["$incidental"]
            if tagged["kind"] in allowed:
                return {"$normalized": tagged["kind"]}
            return {"$incidental": dict(tagged)}
        return {key: visit(item) for key, item in current.items()}

    return visit(value)


def _comparison(
    reference: dict[str, object],
    successor: dict[str, object],
    normalized_reference: object,
    normalized_successor: object,
) -> tuple[str, list[str]]:
    differences: list[str] = []
    infrastructure = False
    if reference["process"]["kind"] != "completed":
        differences.append("reference-process")
        infrastructure = True
    if successor["process"]["kind"] != "completed":
        differences.append("successor-process")
        infrastructure = True
    if not infrastructure:
        if reference["process"]["exit_code"] != successor["process"]["exit_code"]:
            differences.append("exit-code")
        if normalized_reference != normalized_successor:
            differences.append("behavior")
        reference_artifacts = sorted(reference["artifacts"], key=lambda item: item["name"])
        successor_artifacts = sorted(successor["artifacts"], key=lambda item: item["name"])
        if reference_artifacts != successor_artifacts:
            differences.append("artifacts")
    status = "infrastructure-failure" if infrastructure else ("equivalent" if not differences else "drift")
    return status, differences


def decode_result(document: dict[str, object]) -> dict[str, object]:
    required = {
        "schema",
        "status",
        "reference_identity",
        "successor_identity",
        "reference_digest",
        "successor_digest",
        "policy",
        "raw",
        "normalized",
        "differences",
    }
    if set(document) != required or document["schema"] != RESULT_SCHEMA:
        raise DifferentialError("differential result root is not closed")
    raw = document["raw"]
    if not isinstance(raw, dict) or set(raw) != {"reference", "successor"}:
        raise DifferentialError("raw observation pair is not closed")
    reference = decode_observation(raw["reference"])
    successor = decode_observation(raw["successor"])
    if reference["implementation"]["role"] != "reference" or successor["implementation"]["role"] != "successor":
        raise DifferentialError("differential result roles are reversed")
    if document["reference_identity"] != reference["implementation"] or document["successor_identity"] != successor["implementation"]:
        raise DifferentialError("differential identities do not match raw observations")
    if document["reference_digest"] != _digest(_canonical_bytes(reference)) or document["successor_digest"] != _digest(_canonical_bytes(successor)):
        raise DifferentialError("differential digest does not match raw observation")
    policy = decode_policy(document["policy"])
    normalized = document["normalized"]
    if not isinstance(normalized, dict) or set(normalized) != {"reference", "successor"}:
        raise DifferentialError("normalized observation pair is not closed")
    expected_reference = normalize_value(reference["behavior"], policy)
    expected_successor = normalize_value(successor["behavior"], policy)
    if normalized != {"reference": expected_reference, "successor": expected_successor}:
        raise DifferentialError("normalized values do not match raw observations and policy")
    status, differences = _comparison(reference, successor, expected_reference, expected_successor)
    if document["status"] != status or document["differences"] != differences:
        raise DifferentialError("differential status or differences are inconsistent")
    return document


def compare_observations(
    reference: dict[str, object],
    successor: dict[str, object],
    policy: dict[str, object],
) -> dict[str, object]:
    decode_observation(reference)
    decode_observation(successor)
    decode_policy(policy)
    if reference["implementation"]["role"] != "reference":
        raise DifferentialError("left observation must have reference role")
    if successor["implementation"]["role"] != "successor":
        raise DifferentialError("right observation must have successor role")
    normalized_reference = normalize_value(reference["behavior"], policy)
    normalized_successor = normalize_value(successor["behavior"], policy)
    status, differences = _comparison(
        reference,
        successor,
        normalized_reference,
        normalized_successor,
    )
    result = {
        "schema": RESULT_SCHEMA,
        "status": status,
        "reference_identity": dict(reference["implementation"]),
        "successor_identity": dict(successor["implementation"]),
        "reference_digest": _digest(_canonical_bytes(reference)),
        "successor_digest": _digest(_canonical_bytes(successor)),
        "policy": {"schema": POLICY_SCHEMA, "allowed": sorted(policy["allowed"])},
        "raw": {"reference": reference, "successor": successor},
        "normalized": {"reference": normalized_reference, "successor": normalized_successor},
        "differences": differences,
    }
    return decode_result(result)
