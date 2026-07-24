"""Operations-owned runtime dispatcher bootstrap configuration."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit_core.types import RuntimeKind


class RuntimeDispatcherBootstrapError(ValueError):
    """Raised when runtime dispatcher bootstrap configuration is malformed."""


@dataclass(frozen=True)
class RuntimeDispatcherBootstrapConfiguration:
    """Process capability configuration for runtime interpreter dispatch.

    This is not workspace runtime authority. It describes which runtime
    interpreter families this operations process is allowed to compose when the
    corresponding provider package is available.
    """

    runtime_kinds: tuple[RuntimeKind, ...]

    def __post_init__(self) -> None:
        try:
            kinds = tuple(self.runtime_kinds)
        except TypeError as error:
            raise RuntimeDispatcherBootstrapError(
                "runtime dispatcher bootstrap requires runtime kinds"
            ) from error
        if not all(isinstance(kind, RuntimeKind) for kind in kinds):
            raise RuntimeDispatcherBootstrapError(
                "runtime dispatcher bootstrap values must be RuntimeKind"
            )
        normalized = tuple(
            sorted(
                dict.fromkeys(kinds),
                key=lambda kind: kind.value,
            )
        )
        object.__setattr__(self, "runtime_kinds", normalized)

    @classmethod
    def disabled(cls) -> "RuntimeDispatcherBootstrapConfiguration":
        """Return explicit no-runtime-dispatch process capability."""

        return cls(())

    @classmethod
    def allow(
        cls,
        runtime_kinds: tuple[RuntimeKind, ...],
    ) -> "RuntimeDispatcherBootstrapConfiguration":
        """Return explicit allowed runtime interpreter families."""

        return cls(runtime_kinds)

    @classmethod
    def from_process_value(
        cls,
        value: str,
    ) -> "RuntimeDispatcherBootstrapConfiguration":
        """Parse a cpk-server process value such as ``none`` or ``docker``."""

        if not isinstance(value, str):
            raise RuntimeDispatcherBootstrapError(
                "runtime dispatcher bootstrap value must be text"
            )
        parts = tuple(part.strip() for part in value.split(","))
        if not parts or any(not part for part in parts):
            raise RuntimeDispatcherBootstrapError(
                "runtime dispatcher bootstrap value must be nonempty"
            )
        if parts == ("none",):
            return cls.disabled()
        if "none" in parts:
            raise RuntimeDispatcherBootstrapError(
                "none cannot be combined with runtime interpreter kinds"
            )
        try:
            return cls(tuple(RuntimeKind(part) for part in parts))
        except ValueError as error:
            raise RuntimeDispatcherBootstrapError(
                "runtime dispatcher bootstrap includes an unknown runtime kind"
            ) from error

    @property
    def enabled(self) -> bool:
        """Whether this process can attempt runtime dispatch."""

        return bool(self.runtime_kinds)

    def descriptor(self) -> dict[str, object]:
        return {
            "runtime_interpreters": [kind.value for kind in self.runtime_kinds],
            "runtime_dispatch": "enabled" if self.enabled else "disabled",
        }

    def __str__(self) -> str:
        if not self.runtime_kinds:
            return "none"
        return ",".join(kind.value for kind in self.runtime_kinds)
