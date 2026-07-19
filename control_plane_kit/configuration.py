"""Pure immutable configuration artifacts retained in deployment graphs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Mapping


MAX_CONFIGURATION_BYTES = 262_144
_ARTIFACT_ID = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
_SECRET_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:password|secret|token|credential|private_key|api_key)\s*[:=]"
)
_SECRET_KEYS = ("password", "secret", "token", "credential", "private_key", "api_key")
_FORBIDDEN_ROOTS = ("/dev", "/proc", "/run/secrets", "/sys", "/var/run/docker.sock")


class ConfigurationMediaType(StrEnum):
    TEXT = "text/plain"
    JSON = "application/json"
    YAML = "application/yaml"
    TOML = "application/toml"


class ConfigurationFileMode(StrEnum):
    OWNER_READ_ONLY = "0400"
    READ_ONLY = "0444"


class ConfigurationArtifactError(ValueError):
    """A configuration artifact cannot enter durable topology."""


@dataclass(frozen=True, order=True)
class ConfigurationArtifact:
    """One bounded, secret-free file intended for a container path."""

    artifact_id: str
    target_path: str
    media_type: ConfigurationMediaType
    content: str = field(compare=False, repr=False)
    file_mode: ConfigurationFileMode = ConfigurationFileMode.READ_ONLY
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if not _ARTIFACT_ID.fullmatch(self.artifact_id):
            raise ConfigurationArtifactError("configuration artifact identity is invalid")
        _validate_target_path(self.target_path)
        if not isinstance(self.media_type, ConfigurationMediaType):
            raise TypeError("configuration media type must be ConfigurationMediaType")
        if not isinstance(self.file_mode, ConfigurationFileMode):
            raise TypeError("configuration file mode must be ConfigurationFileMode")
        if not isinstance(self.content, str):
            raise TypeError("configuration content must be text")
        encoded = self.content.encode("utf-8")
        if not encoded or len(encoded) > MAX_CONFIGURATION_BYTES or "\x00" in self.content:
            raise ConfigurationArtifactError("configuration content is empty or exceeds its bound")
        _reject_secret_content(self.content, self.media_type)
        object.__setattr__(self, "content_digest", hashlib.sha256(encoded).hexdigest())

    def descriptor(self) -> dict[str, str]:
        return {
            "artifact_id": self.artifact_id,
            "target_path": self.target_path,
            "media_type": self.media_type.value,
            "content": self.content,
            "content_digest": self.content_digest,
            "file_mode": self.file_mode.value,
        }

    @classmethod
    def from_descriptor(cls, value: Mapping[str, object]) -> "ConfigurationArtifact":
        expected = {
            "artifact_id",
            "target_path",
            "media_type",
            "content",
            "content_digest",
            "file_mode",
        }
        if set(value) != expected or not all(
            isinstance(value[name], str) for name in expected
        ):
            raise ConfigurationArtifactError("configuration artifact descriptor is malformed")
        try:
            artifact = cls(
                artifact_id=value["artifact_id"],
                target_path=value["target_path"],
                media_type=ConfigurationMediaType(value["media_type"]),
                content=value["content"],
                file_mode=ConfigurationFileMode(value["file_mode"]),
            )
        except (TypeError, ValueError) as error:
            raise ConfigurationArtifactError(
                "configuration artifact descriptor is malformed"
            ) from error
        if artifact.content_digest != value["content_digest"]:
            raise ConfigurationArtifactError("configuration artifact digest does not match content")
        return artifact


def _validate_target_path(value: str) -> None:
    if not isinstance(value, str) or not value.startswith("/") or value.endswith("/"):
        raise ConfigurationArtifactError("configuration target must be an absolute file path")
    path = PurePosixPath(value)
    if str(path) != value or ".." in path.parts or value == "/":
        raise ConfigurationArtifactError("configuration target path is not normalized")
    if any(value == root or value.startswith(f"{root}/") for root in _FORBIDDEN_ROOTS):
        raise ConfigurationArtifactError("configuration target path is reserved")


def _reject_secret_content(content: str, media_type: ConfigurationMediaType) -> None:
    if "-----BEGIN PRIVATE KEY-----" in content or re.search(r"://[^/@\s]+:[^/@\s]+@", content):
        raise ConfigurationArtifactError("configuration content contains secret-shaped data")
    if media_type is ConfigurationMediaType.JSON:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as error:
            raise ConfigurationArtifactError("JSON configuration content is malformed") from error
        if _contains_secret_key(parsed):
            raise ConfigurationArtifactError("configuration content contains secret-shaped data")
    elif _SECRET_ASSIGNMENT.search(content):
        raise ConfigurationArtifactError("configuration content contains secret-shaped data")


def _contains_secret_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            any(marker in str(key).lower() for marker in _SECRET_KEYS)
            or _contains_secret_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False
