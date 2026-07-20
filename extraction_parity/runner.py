"""Bounded process interpreter for differential observations."""

from __future__ import annotations

import argparse
import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
from tempfile import TemporaryDirectory
import time
from typing import Iterable

from extraction_parity.differential import (
    DifferentialError,
    MAXIMUM_CAPTURE_BYTES,
    capture_payload,
    compare_observations,
    decode_observation,
    decode_result,
)
from extraction_parity.validation import decode_evidence_index, read_bounded_json


class RunnerError(ValueError):
    pass


_SECRET_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class ArtifactInput:
    name: str
    media_type: str
    path: Path


def _redact(payload: bytes, secrets: Mapping[str, bytes]) -> bytes:
    redacted = payload
    for name, value in sorted(secrets.items(), key=lambda item: len(item[1]), reverse=True):
        redacted = redacted.replace(value, f"[REDACTED:{name}]".encode("ascii"))
    return redacted


def _bounded_redact(payload: bytes, secrets: Mapping[str, bytes], limit: int) -> bytes:
    redacted = _redact(payload, secrets)
    bound = min(limit, MAXIMUM_CAPTURE_BYTES)
    if len(redacted) <= bound:
        return redacted
    marker = b"[REDACTED:output-limit]"
    if len(marker) <= bound:
        return marker
    return b""


def _read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise RunnerError(f"bounded file exceeds limit: {path.name}")
    return payload


def _run_bounded(
    command: Sequence[str],
    environment: dict[str, str],
    timeout_seconds: float,
    output_limit: int,
) -> tuple[str, int | None, bytes, bytes]:
    try:
        process = subprocess.Popen(
            list(command),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError:
        return "infrastructure-failure", None, b"", b"process launch failed"
    selector = selectors.DefaultSelector()
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    for stream in streams:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout_seconds
    kind = "completed"
    total = 0
    while selector.get_map():
        remaining = deadline - time.monotonic()
        if remaining <= 0 and kind == "completed":
            kind = "timeout"
            process.kill()
        for key, _ in selector.select(max(0.0, min(remaining, 0.05))):
            stream = key.fileobj
            try:
                chunk = os.read(stream.fileno(), 65536)
            except BlockingIOError:
                continue
            if not chunk:
                selector.unregister(stream)
                stream.close()
                continue
            available = max(0, output_limit - total)
            streams[stream].extend(chunk[:available])
            total += min(len(chunk), available)
            if len(chunk) > available and kind == "completed":
                kind = "output-limit"
                process.kill()
        if process.poll() is not None and not selector.get_map():
            break
    return_code = process.wait()
    stdout = bytes(streams[process.stdout])
    stderr = bytes(streams[process.stderr])
    return kind, return_code if kind == "completed" else None, stdout, stderr


def _secrets(values: Mapping[str, str] | None) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for name, value in (values or {}).items():
        if _SECRET_NAME.fullmatch(name) is None:
            raise RunnerError("secret names must be environment-safe identities")
        if not isinstance(value, str) or len(value.encode("utf-8")) < 8:
            raise RunnerError("secret values must be text with at least eight bytes")
        result[name] = value.encode("utf-8")
    return result


def capture_command(
    command: Sequence[str],
    *,
    role: str,
    identity: str,
    source_digest: str,
    timeout_seconds: float,
    output_limit: int,
    secrets: Mapping[str, str] | None = None,
    artifacts: Sequence[ArtifactInput] = (),
) -> dict[str, object]:
    if not command or not all(isinstance(argument, str) and argument for argument in command):
        raise RunnerError("command must be a non-empty string sequence")
    if timeout_seconds <= 0 or timeout_seconds > 300:
        raise RunnerError("timeout must be between zero and 300 seconds")
    if output_limit <= 0 or output_limit > 1024 * 1024:
        raise RunnerError("output limit is outside the supported bound")
    secret_bytes = _secrets(secrets)
    for argument in command:
        encoded = argument.encode("utf-8")
        if any(secret in encoded for secret in secret_bytes.values()):
            raise RunnerError("secret values cannot enter command arguments")
    with TemporaryDirectory(prefix="cpk-parity-") as directory:
        behavior_path = Path(directory) / "behavior.json"
        environment = dict(os.environ)
        environment["CPK_PARITY_BEHAVIOR_PATH"] = str(behavior_path)
        for name, value in (secrets or {}).items():
            environment[name] = value
        kind, exit_code, stdout, stderr = _run_bounded(
            command,
            environment,
            timeout_seconds,
            output_limit,
        )
        behavior: object = None
        artifact_values: list[dict[str, object]] = []
        if kind == "completed":
            try:
                raw_behavior = _read_bounded(behavior_path, 1024 * 1024)
                if any(secret in raw_behavior for secret in secret_bytes.values()):
                    raise RunnerError("structured behavior contains declared secret material")
                behavior = json.loads(raw_behavior)
                for artifact in artifacts:
                    payload = _read_bounded(artifact.path, 1024 * 1024)
                    if any(secret in payload for secret in secret_bytes.values()):
                        raise RunnerError("declared artifact contains secret material")
                    artifact_values.append(
                        {
                            "name": artifact.name,
                            "media_type": artifact.media_type,
                            "payload": capture_payload(payload),
                        }
                    )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, RunnerError):
                kind = "infrastructure-failure"
                exit_code = None
                behavior = None
                artifact_values = []
        observation = {
            "schema": "cpk.raw-observation",
            "implementation": {
                "role": role,
                "identity": identity,
                "source_digest": source_digest,
            },
            "command": list(command),
            "process": {"kind": kind, "exit_code": exit_code},
            "stdout": capture_payload(_bounded_redact(stdout, secret_bytes, output_limit)),
            "stderr": capture_payload(_bounded_redact(stderr, secret_bytes, output_limit)),
            "behavior": behavior,
            "artifacts": sorted(artifact_values, key=lambda item: str(item["name"])),
        }
        try:
            return decode_observation(observation)
        except DifferentialError as error:
            raise RunnerError(str(error)) from error


def evidence_record(evidence_id: str, result: dict[str, object]) -> dict[str, str]:
    decode_result(result)
    payload = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    record = {
        "id": evidence_id,
        "status": "passing" if result["status"] == "equivalent" else "failed",
        "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
    }
    decode_evidence_index({"schema": "cpk.successor-evidence-index", "evidence": [record]})
    return record


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load_secrets(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    value = read_bounded_json(path)
    if set(value) != {"secrets"} or not isinstance(value["secrets"], dict):
        raise RunnerError("secret input must be a closed secrets object")
    return {str(name): value for name, value in value["secrets"].items()}


def _parse_artifacts(values: Sequence[Sequence[str]] | None) -> tuple[ArtifactInput, ...]:
    artifacts: list[ArtifactInput] = []
    for name, media_type, path in values or ():
        artifacts.append(ArtifactInput(name=name, media_type=media_type, path=Path(path)))
    return tuple(artifacts)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action", required=True)
    capture = subparsers.add_parser("capture")
    capture.add_argument("--role", choices=["reference", "successor"], required=True)
    capture.add_argument("--identity", required=True)
    capture.add_argument("--source-digest", required=True)
    capture.add_argument("--timeout", type=float, default=30)
    capture.add_argument("--output-limit", type=int, default=1024 * 1024)
    capture.add_argument("--secret-file", type=Path)
    capture.add_argument("--artifact", nargs=3, action="append", metavar=("NAME", "MEDIA_TYPE", "PATH"))
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("command", nargs=argparse.REMAINDER)
    compare = subparsers.add_parser("compare")
    compare.add_argument("--reference", type=Path, required=True)
    compare.add_argument("--successor", type=Path, required=True)
    compare.add_argument("--policy", type=Path, required=True)
    compare.add_argument("--result", type=Path, required=True)
    compare.add_argument("--evidence", type=Path, required=True)
    compare.add_argument("--evidence-id", required=True)
    arguments = parser.parse_args(argv)
    if arguments.action == "capture":
        command = arguments.command[1:] if arguments.command[:1] == ["--"] else arguments.command
        observation = capture_command(
            command,
            role=arguments.role,
            identity=arguments.identity,
            source_digest=arguments.source_digest,
            timeout_seconds=arguments.timeout,
            output_limit=arguments.output_limit,
            secrets=_load_secrets(arguments.secret_file),
            artifacts=_parse_artifacts(arguments.artifact),
        )
        _write_json(arguments.output, observation)
        return 0
    result = compare_observations(
        read_bounded_json(arguments.reference),
        read_bounded_json(arguments.successor),
        read_bounded_json(arguments.policy),
    )
    _write_json(arguments.result, result)
    _write_json(
        arguments.evidence,
        {
            "schema": "cpk.successor-evidence-index",
            "evidence": [evidence_record(arguments.evidence_id, result)],
        },
    )
    print(f"status={result['status']} evidence={arguments.evidence_id}")
    return 0 if result["status"] == "equivalent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
