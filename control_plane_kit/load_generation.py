"""Closed bounded values for test-only HTTP load generation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Mapping


class LoadMethod(StrEnum):
    GET = "GET"
    HEAD = "HEAD"


class LoadRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DEADLINE_REACHED = "deadline-reached"


class LoadRequestOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    TIMED_OUT = "timed-out"
    FAILED = "failed"


@dataclass(frozen=True)
class LoadGeneratorPolicy:
    allowed_paths: tuple[str, ...]
    max_requests: int = 1_000
    max_concurrency: int = 32
    max_requests_per_second: int = 100
    max_duration_ms: int = 60_000
    max_response_bytes: int = 65_536
    max_retained_runs: int = 32

    def __post_init__(self) -> None:
        if not self.allowed_paths or len(set(self.allowed_paths)) != len(self.allowed_paths):
            raise ValueError("load-generator allowed paths must be nonempty and unique")
        for path in self.allowed_paths:
            _validate_target_path(path)
        _bounded("load-generator request limit", self.max_requests, 1, 100_000)
        _bounded("load-generator concurrency limit", self.max_concurrency, 1, 1_000)
        _bounded("load-generator rate limit", self.max_requests_per_second, 1, 10_000)
        _bounded("load-generator duration limit", self.max_duration_ms, 1, 600_000)
        _bounded("load-generator response limit", self.max_response_bytes, 1, 1_048_576)
        _bounded("load-generator retained-run limit", self.max_retained_runs, 1, 1_000)

    def descriptor(self) -> dict[str, object]:
        return {
            "allowed_paths": list(self.allowed_paths),
            "max_requests": self.max_requests,
            "max_concurrency": self.max_concurrency,
            "max_requests_per_second": self.max_requests_per_second,
            "max_duration_ms": self.max_duration_ms,
            "max_response_bytes": self.max_response_bytes,
            "max_retained_runs": self.max_retained_runs,
        }


@dataclass(frozen=True)
class LoadRunCommand:
    run_id: str
    method: LoadMethod
    path: str
    request_count: int
    concurrency: int
    requests_per_second: int
    duration_ms: int
    timeout_ms: int

    def __post_init__(self) -> None:
        _bounded_name("load run id", self.run_id)
        if not isinstance(self.method, LoadMethod):
            raise TypeError("load run method must be typed")
        _validate_target_path(self.path)
        _bounded("load run request count", self.request_count, 1, 100_000)
        _bounded("load run concurrency", self.concurrency, 1, 1_000)
        _bounded("load run rate", self.requests_per_second, 1, 10_000)
        _bounded("load run duration", self.duration_ms, 1, 600_000)
        _bounded("load run timeout", self.timeout_ms, 1, 60_000)

    def descriptor(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "method": self.method.value,
            "path": self.path,
            "request_count": self.request_count,
            "concurrency": self.concurrency,
            "requests_per_second": self.requests_per_second,
            "duration_ms": self.duration_ms,
            "timeout_ms": self.timeout_ms,
        }

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(self.descriptor(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class LoadRunEvidence:
    planned: int = 0
    dispatched: int = 0
    succeeded: int = 0
    rejected: int = 0
    timed_out: int = 0
    failed: int = 0
    cancelled_before_dispatch: int = 0
    deadline_skipped: int = 0

    def __post_init__(self) -> None:
        values = (
            self.planned,
            self.dispatched,
            self.succeeded,
            self.rejected,
            self.timed_out,
            self.failed,
            self.cancelled_before_dispatch,
            self.deadline_skipped,
        )
        if any(type(value) is not int or value < 0 or value > 100_000 for value in values):
            raise ValueError("load-run evidence counts must be bounded nonnegative integers")
        if self.dispatched > self.planned:
            raise ValueError("load-run dispatch count cannot exceed planned count")
        if self.succeeded + self.rejected + self.timed_out + self.failed > self.dispatched:
            raise ValueError("load-run terminal outcomes cannot exceed dispatch count")
        if self.dispatched + self.cancelled_before_dispatch + self.deadline_skipped > self.planned:
            raise ValueError("load-run dispatch and skipped counts cannot exceed planned count")

    def descriptor(self) -> dict[str, int]:
        return {
            "planned": self.planned,
            "dispatched": self.dispatched,
            "succeeded": self.succeeded,
            "rejected": self.rejected,
            "timed_out": self.timed_out,
            "failed": self.failed,
            "cancelled_before_dispatch": self.cancelled_before_dispatch,
            "deadline_skipped": self.deadline_skipped,
        }


@dataclass(frozen=True)
class LoadRunRecord:
    command: LoadRunCommand
    status: LoadRunStatus
    evidence: LoadRunEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.command, LoadRunCommand):
            raise TypeError("load-run record command must be typed")
        if not isinstance(self.status, LoadRunStatus):
            raise TypeError("load-run record status must be typed")

    def descriptor(self) -> dict[str, object]:
        return {
            "command": self.command.descriptor(),
            "command_fingerprint": self.command.fingerprint,
            "status": self.status.value,
            "evidence": self.evidence.descriptor(),
        }


def load_generator_policy_from_descriptor(value: object) -> LoadGeneratorPolicy:
    expected = {
        "allowed_paths", "max_requests", "max_concurrency",
        "max_requests_per_second", "max_duration_ms", "max_response_bytes",
        "max_retained_runs",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("load-generator policy descriptor has unknown or missing fields")
    paths = value["allowed_paths"]
    if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
        raise TypeError("load-generator allowed paths must be a string list")
    integers = {name: value[name] for name in expected - {"allowed_paths"}}
    if any(type(item) is not int for item in integers.values()):
        raise TypeError("load-generator policy bounds must be integers")
    return LoadGeneratorPolicy(tuple(paths), **integers)


def load_run_command_from_descriptor(value: object) -> LoadRunCommand:
    expected = {
        "run_id", "method", "path", "request_count", "concurrency",
        "requests_per_second", "duration_ms", "timeout_ms",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("load-run descriptor has unknown or missing fields")
    if not isinstance(value["run_id"], str) or not isinstance(value["path"], str) or not isinstance(value["method"], str):
        raise TypeError("load-run identity fields must be strings")
    integers = {name: value[name] for name in expected - {"run_id", "method", "path"}}
    if any(type(item) is not int for item in integers.values()):
        raise TypeError("load-run bounds must be integers")
    return LoadRunCommand(
        value["run_id"], LoadMethod(value["method"]), value["path"], **integers
    )


def validate_load_command(policy: LoadGeneratorPolicy, command: LoadRunCommand) -> None:
    if command.path not in policy.allowed_paths:
        raise ValueError("load-run path is not declared by startup policy")
    bounds = (
        (command.request_count, policy.max_requests, "request count"),
        (command.concurrency, policy.max_concurrency, "concurrency"),
        (command.requests_per_second, policy.max_requests_per_second, "rate"),
        (command.duration_ms, policy.max_duration_ms, "duration"),
    )
    for actual, maximum, label in bounds:
        if actual > maximum:
            raise ValueError(f"load-run {label} exceeds startup policy")
    if command.timeout_ms > command.duration_ms:
        raise ValueError("load-run timeout cannot exceed run duration")


def scheduled_offsets_ms(command: LoadRunCommand) -> tuple[int, ...]:
    """Return deterministic dispatch offsets admitted by count, rate, and duration."""

    return tuple(
        offset
        for index in range(command.request_count)
        if (offset := index * 1_000 // command.requests_per_second) < command.duration_ms
    )


def _validate_target_path(path: str) -> None:
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("/__deploy")
        or "?" in path
        or "#" in path
        or "\\" in path
        or len(path.encode()) > 2_048
    ):
        raise ValueError("load-generator target path must be a safe non-control absolute path")


def _bounded_name(label: str, value: str) -> None:
    if not isinstance(value, str) or not value or len(value.encode()) > 256:
        raise ValueError(f"{label} must be nonempty and bounded")


def _bounded(label: str, value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or value < minimum or value > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
