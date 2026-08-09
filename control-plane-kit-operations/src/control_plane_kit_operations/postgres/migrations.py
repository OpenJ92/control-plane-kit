"""Pure values and planning laws for CPK-owned Postgres migrations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
import json
import re


_MIGRATION_NAME = re.compile(r"^[a-z][a-z0-9-]{0,127}$")
_LOWERCASE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROGRAM_CHECKSUM_DOMAIN = "control-plane-kit.operations.schema-migration-program"
_PROGRAM_CHECKSUM_FORMAT_VERSION = 1


class SchemaMigrationError(ValueError):
    """Raised when CPK schema migration values do not compose."""


@dataclass(frozen=True)
class SqlMigrationStep:
    """One immutable SQL effect inside a migration program."""

    sql: str = field(repr=False)
    checksum_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        content = _validated_sql_content(self.sql, subject="migration step sql")
        object.__setattr__(self, "checksum_sha256", sha256(content).hexdigest())


class SchemaBackfillKind(StrEnum):
    """Closed deterministic backfill vocabulary understood by migrations."""

    PRODUCT_DESCRIPTOR_CONTENT = "product-descriptor-content"


@dataclass(frozen=True)
class DeterministicBackfillStep:
    """Versioned identity of one deterministic application backfill."""

    kind: SchemaBackfillKind
    algorithm_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SchemaBackfillKind):
            raise SchemaMigrationError("backfill kind must be closed")
        if type(self.algorithm_version) is not int or self.algorithm_version < 1:
            raise SchemaMigrationError(
                "backfill algorithm version must be a positive integer"
            )


@dataclass(frozen=True)
class SchemaMigration:
    """One immutable SQL or ordered-program migration of the CPK schema."""

    version: int
    name: str
    sql: str | None = field(default=None, repr=False)
    steps: tuple[SqlMigrationStep | DeterministicBackfillStep, ...] | None = None
    checksum_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _validate_version(self.version)
        _validate_name(self.name)
        has_sql = self.sql is not None
        has_steps = self.steps is not None
        if has_sql == has_steps:
            raise SchemaMigrationError(
                "migration requires exactly one of sql or ordered steps"
            )
        if has_sql:
            content = _validated_sql_content(self.sql, subject="migration sql")
        else:
            content = _program_checksum_content(self.steps)
        object.__setattr__(self, "checksum_sha256", sha256(content).hexdigest())


@dataclass(frozen=True)
class AppliedSchemaMigration:
    """Durable public identity of one applied migration."""

    version: int
    name: str
    checksum_sha256: str

    def __post_init__(self) -> None:
        _validate_version(self.version)
        _validate_name(self.name)
        if (
            not isinstance(self.checksum_sha256, str)
            or _LOWERCASE_SHA256.fullmatch(self.checksum_sha256) is None
        ):
            raise SchemaMigrationError(
                "applied migration checksum must be lowercase sha256"
            )


class ObservedSchemaKind(StrEnum):
    """Closed classifications produced by the future Postgres inspector."""

    EMPTY = "empty"
    CURRENT_BASELINE = "current-baseline"
    VERSIONED = "versioned"


@dataclass(frozen=True)
class ObservedSchemaState:
    """Schema state supplied to the pure migration planner."""

    kind: ObservedSchemaKind
    applied_migrations: tuple[AppliedSchemaMigration, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ObservedSchemaKind):
            raise SchemaMigrationError("observed schema kind must be closed")
        if not isinstance(self.applied_migrations, tuple):
            raise SchemaMigrationError("applied migrations must be a tuple")
        for expected_version, migration in enumerate(
            self.applied_migrations,
            start=1,
        ):
            if not isinstance(migration, AppliedSchemaMigration):
                raise SchemaMigrationError(
                    "applied migration history must contain migration values"
                )
            if migration.version != expected_version:
                raise SchemaMigrationError(
                    "applied migration history must be contiguous from version 1"
                )
        if self.kind is ObservedSchemaKind.VERSIONED:
            if not self.applied_migrations:
                raise SchemaMigrationError(
                    "versioned schema requires applied migration history"
                )
        elif self.applied_migrations:
            raise SchemaMigrationError(
                "unversioned schema state cannot contain applied migrations"
            )


class SchemaMigrationActionKind(StrEnum):
    """Closed effects that a Postgres migration interpreter may perform."""

    APPLY = "apply"
    RECORD_BASELINE = "record-baseline"


@dataclass(frozen=True)
class SchemaMigrationAction:
    """One planned migration effect."""

    kind: SchemaMigrationActionKind
    migration: SchemaMigration

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SchemaMigrationActionKind):
            raise SchemaMigrationError("migration action kind must be closed")
        if not isinstance(self.migration, SchemaMigration):
            raise SchemaMigrationError(
                "migration action requires a schema migration"
            )


@dataclass(frozen=True)
class SchemaMigrationPlan:
    """Inspectable result of validating observed truth against a registry."""

    observed: ObservedSchemaState
    actions: tuple[SchemaMigrationAction, ...]
    target_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.observed, ObservedSchemaState):
            raise SchemaMigrationError("migration plan requires observed schema")
        if not isinstance(self.actions, tuple) or any(
            not isinstance(action, SchemaMigrationAction) for action in self.actions
        ):
            raise SchemaMigrationError("migration plan actions must be a tuple")
        _validate_version(self.target_version)
        self._validate_action_sequence()

    def _validate_action_sequence(self) -> None:
        if self.observed.kind is ObservedSchemaKind.VERSIONED:
            current_version = len(self.observed.applied_migrations)
            if not self.actions:
                if self.target_version != current_version:
                    raise SchemaMigrationError(
                        "current migration plan target must match applied history"
                    )
                return
            expected_versions = tuple(
                range(current_version + 1, self.target_version + 1)
            )
            expected_kinds = (SchemaMigrationActionKind.APPLY,) * len(
                expected_versions
            )
        else:
            expected_versions = tuple(range(1, self.target_version + 1))
            if self.observed.kind is ObservedSchemaKind.EMPTY:
                expected_kinds = (SchemaMigrationActionKind.APPLY,) * len(
                    expected_versions
                )
            else:
                expected_kinds = (
                    SchemaMigrationActionKind.RECORD_BASELINE,
                    *(
                        SchemaMigrationActionKind.APPLY
                        for _ in expected_versions[1:]
                    ),
                )
        actual_versions = tuple(
            action.migration.version for action in self.actions
        )
        actual_kinds = tuple(action.kind for action in self.actions)
        if (
            actual_versions != expected_versions
            or actual_kinds != expected_kinds
        ):
            raise SchemaMigrationError(
                "migration plan actions do not match observed schema and target"
            )


@dataclass(frozen=True)
class SchemaMigrationRegistry:
    """Ordered immutable language of all known CPK schema migrations."""

    migrations: tuple[SchemaMigration, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.migrations, tuple) or not self.migrations:
            raise SchemaMigrationError(
                "migration registry must be a nonempty tuple"
            )
        for expected_version, migration in enumerate(self.migrations, start=1):
            if not isinstance(migration, SchemaMigration):
                raise SchemaMigrationError(
                    "migration registry must contain schema migrations"
                )
            if migration.version != expected_version:
                raise SchemaMigrationError(
                    "migration registry must be contiguous from version 1"
                )
        names = tuple(migration.name for migration in self.migrations)
        if len(set(names)) != len(names):
            raise SchemaMigrationError("migration registry names must be unique")

    @property
    def target_version(self) -> int:
        """Return the latest version understood by this package."""

        return self.migrations[-1].version

    def plan(self, observed: ObservedSchemaState) -> SchemaMigrationPlan:
        """Validate observed history and return the remaining pure actions."""

        if not isinstance(observed, ObservedSchemaState):
            raise SchemaMigrationError("migration planning requires observed schema")
        if observed.kind is ObservedSchemaKind.EMPTY:
            actions = tuple(
                SchemaMigrationAction(SchemaMigrationActionKind.APPLY, migration)
                for migration in self.migrations
            )
        elif observed.kind is ObservedSchemaKind.CURRENT_BASELINE:
            actions = (
                SchemaMigrationAction(
                    SchemaMigrationActionKind.RECORD_BASELINE,
                    self.migrations[0],
                ),
                *(
                    SchemaMigrationAction(
                        SchemaMigrationActionKind.APPLY,
                        migration,
                    )
                    for migration in self.migrations[1:]
                ),
            )
        else:
            self._validate_applied_prefix(observed.applied_migrations)
            actions = tuple(
                SchemaMigrationAction(SchemaMigrationActionKind.APPLY, migration)
                for migration in self.migrations[len(observed.applied_migrations) :]
            )
        return SchemaMigrationPlan(
            observed=observed,
            actions=actions,
            target_version=self.target_version,
        )

    def _validate_applied_prefix(
        self,
        applied_migrations: tuple[AppliedSchemaMigration, ...],
    ) -> None:
        if len(applied_migrations) > len(self.migrations):
            raise SchemaMigrationError(
                "database schema version is newer than this package"
            )
        for applied, expected in zip(applied_migrations, self.migrations):
            if applied.name != expected.name:
                raise SchemaMigrationError(
                    f"applied migration name differs at version {expected.version}"
                )
            if applied.checksum_sha256 != expected.checksum_sha256:
                raise SchemaMigrationError(
                    f"applied migration checksum differs at version {expected.version}"
                )


def _validate_version(value: object) -> None:
    if type(value) is not int or value < 1:
        raise SchemaMigrationError("migration version must be a positive integer")


def _validate_name(value: object) -> None:
    if not isinstance(value, str) or _MIGRATION_NAME.fullmatch(value) is None:
        raise SchemaMigrationError(
            "migration name must be a bounded lowercase slug"
        )


def _validated_sql_content(value: object, *, subject: str) -> bytes:
    if not isinstance(value, str) or not str.strip(value):
        raise SchemaMigrationError(f"{subject} must be nonempty text")
    if str.__contains__(value, "\x00"):
        raise SchemaMigrationError(f"{subject} must not contain NUL")
    content = None
    try:
        content = str.encode(value, "utf-8")
    except UnicodeEncodeError:
        pass
    if content is None:
        raise SchemaMigrationError(f"{subject} must be valid UTF-8")
    return content


def _program_checksum_content(
    steps: object,
) -> bytes:
    if not isinstance(steps, tuple) or not steps:
        raise SchemaMigrationError(
            "migration program steps must be a nonempty tuple"
        )
    descriptors: list[dict[str, object]] = []
    for step in steps:
        if type(step) is SqlMigrationStep:
            descriptors.append(
                {
                    "kind": "sql",
                    "sha256": step.checksum_sha256,
                }
            )
        elif type(step) is DeterministicBackfillStep:
            descriptors.append(
                {
                    "algorithm_version": step.algorithm_version,
                    "backfill_kind": step.kind.value,
                    "kind": "deterministic-backfill",
                }
            )
        else:
            raise SchemaMigrationError(
                "migration program contains an unsupported step value"
            )
    descriptor = {
        "domain": _PROGRAM_CHECKSUM_DOMAIN,
        "format_version": _PROGRAM_CHECKSUM_FORMAT_VERSION,
        "steps": descriptors,
    }
    return json.dumps(
        descriptor,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
