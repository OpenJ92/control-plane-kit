"""One explicit history-preserving transition; never called by installation."""
from dataclasses import dataclass
from hashlib import sha256
import json

from psycopg.pq import TransactionStatus
from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity
from .current_schema_contract import (
    PRE_COMMAND_DIAGNOSTIC_SCHEMA_CONTRACT as SOURCE,
    PRE_COMMAND_DIAGNOSTIC_SCHEMA_CONTRACT_SHA256 as SOURCE_SHA256,
    COMMAND_DIAGNOSTIC_UPGRADE_TARGET_SCHEMA_CONTRACT as TARGET,
    COMMAND_DIAGNOSTIC_UPGRADE_TARGET_SCHEMA_CONTRACT_SHA256 as TARGET_SHA256,
    _COMMAND_DIAGNOSTIC_CONSTRAINT,
)
from .current_schema_verification import current_schema_contract_is_exact, current_namespace_adjuncts_are_exact
from .schema import _ADVISORY_LOCK_SQL


class CommandDiagnosticUpgradeError(RuntimeError):
    """Closed cause; an error is never an installation or repair instruction."""
    def __init__(self, category="transition-failed"):
        if category not in ("invalid-plan", "authorization-denied", "target-mismatch",
                "schema-mismatch", "transition-failed", "transaction-unavailable"):
            raise ValueError("invalid upgrade failure category")
        self.category = category
        super().__init__("command diagnostic upgrade: " + category)


class CommandDiagnosticUpgradeUncertain(CommandDiagnosticUpgradeError):
    """Transaction exit was not acknowledged; inspect with a fresh connection."""


def _text(value):
    if (type(value) is not str or not 1 <= len(value) <= 200
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise CommandDiagnosticUpgradeError("invalid-plan")


@dataclass(frozen=True)
class CommandDiagnosticUpgradePlan:
    installation_id: str
    database_name: str
    schema_name: str
    actor: PrincipalIdentity
    source_contract_sha256: str = SOURCE_SHA256
    target_contract_sha256: str = TARGET_SHA256

    def __post_init__(self):
        for value in (self.installation_id, self.database_name, self.schema_name):
            _text(value)
        if (type(self.actor) is not PrincipalIdentity or self.source_contract_sha256 != SOURCE_SHA256
                or self.target_contract_sha256 != TARGET_SHA256):
            raise CommandDiagnosticUpgradeError("invalid-plan")
        self.actor.__post_init__()

    def descriptor(self):
        return {"installation_id": self.installation_id, "database_name": self.database_name,
            "schema_name": self.schema_name, "actor": self.actor.descriptor(),
            "source_contract_sha256": self.source_contract_sha256,
            "target_contract_sha256": self.target_contract_sha256}

    @property
    def fingerprint(self):
        return sha256(json.dumps({"domain": "cpk.command-diagnostic-upgrade.v1", "plan": self.descriptor()},
            sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class CommandDiagnosticUpgradePolicy:
    """Trusted composition authorizes one transition for one installation target."""
    installation_id: str
    database_name: str
    schema_name: str
    administrators: tuple[PrincipalIdentity, ...]

    def __post_init__(self):
        for value in (self.installation_id, self.database_name, self.schema_name):
            _text(value)
        if (type(self.administrators) is not tuple or not 1 <= len(self.administrators) <= 1024
                or any(type(value) is not PrincipalIdentity for value in self.administrators)
                or len(set(self.administrators)) != len(self.administrators)):
            raise CommandDiagnosticUpgradeError("invalid-plan")
        for identity in self.administrators:
            identity.__post_init__()

    def plan(self, principal):
        self.__post_init__()
        if type(principal) is not AuthenticatedPrincipal or principal.identity not in self.administrators:
            raise CommandDiagnosticUpgradeError("authorization-denied")
        principal.__post_init__()
        return CommandDiagnosticUpgradePlan(self.installation_id, self.database_name, self.schema_name,
            principal.identity)


@dataclass(frozen=True)
class CommandDiagnosticUpgradeResult:
    plan_fingerprint: str
    source_contract_sha256: str
    target_contract_sha256: str
    replayed: bool
    provisional: bool


@dataclass(frozen=True)
class CommandDiagnosticUpgradeInspection:
    plan_fingerprint: str
    state: str
    contract_sha256: str


def _require(plan, principal, policy):
    if type(policy) is not CommandDiagnosticUpgradePolicy or type(plan) is not CommandDiagnosticUpgradePlan:
        raise CommandDiagnosticUpgradeError("invalid-plan")
    plan.__post_init__()
    if plan != policy.plan(principal):
        raise CommandDiagnosticUpgradeError("authorization-denied")


def _target(connection, plan):
    if connection.execute("SELECT current_database(),current_schema()").fetchone() != (
            plan.database_name, plan.schema_name):
        raise CommandDiagnosticUpgradeError("target-mismatch")


def _timeouts(connection):
    previous = dict(connection.execute("SELECT name,setting FROM pg_settings "
        "WHERE name IN ('lock_timeout','statement_timeout')").fetchall())
    for name, maximum in (("lock_timeout", 5000), ("statement_timeout", 30000)):
        original = int(previous[name])
        value = maximum if original == 0 else min(original, maximum)
        connection.execute("SELECT set_config(%s,%s,true)", (name, str(value) + "ms"))
    return previous


def _restore_timeouts(connection, previous):
    for name, value in previous.items():
        connection.execute("SELECT set_config(%s,%s,true)", (name, value + "ms"))


def _recognized(connection):
    if not current_namespace_adjuncts_are_exact(connection):
        raise CommandDiagnosticUpgradeError("schema-mismatch")
    for state, contract in (("source", SOURCE), ("target", TARGET)):
        if current_schema_contract_is_exact(connection, contract=contract):
            return state
    raise CommandDiagnosticUpgradeError("schema-mismatch")


def _validate(connection, state):
    from .current_data_validation import validate_current_rows
    from .workspace_permission_store import validate_workspace_permission_rows
    from .managed_runtime_assignment_store import validate_managed_runtime_assignment_rows
    from .execution import (validate_execution_invocation_rows, validate_command_diagnostic_rows,
        historical_command_receipt_identity)
    from .ingress_invocation_store import validate_ingress_invocation_rows
    from .runtime_effect_origin_store import validate_runtime_effect_origin_rows
    from .provider_policy_command_store import validate_provider_policy_command_rows
    reader = historical_command_receipt_identity if state == "source" else None
    validate_current_rows(connection)
    validate_workspace_permission_rows(connection)
    validate_managed_runtime_assignment_rows(connection)
    validate_execution_invocation_rows(connection, receipt_identity_reader=reader)
    validate_ingress_invocation_rows(connection, receipt_identity_reader=reader)
    validate_runtime_effect_origin_rows(connection, receipt_identity_reader=reader)
    validate_provider_policy_command_rows(connection)
    if state == "target":
        validate_command_diagnostic_rows(connection)


def upgrade_command_diagnostics(connection, plan, *, principal, administration_policy):
    """IDLE owns commit; INTRANS owns a savepoint and returns provisional."""
    _require(plan, principal, administration_policy)
    status = connection.info.transaction_status
    if status not in (TransactionStatus.IDLE, TransactionStatus.INTRANS):
        raise CommandDiagnosticUpgradeError("transaction-unavailable")
    provisional = status == TransactionStatus.INTRANS
    failure = None
    exit_error = None
    try:
        with connection.transaction():
            try:
                previous = _timeouts(connection)
                if connection.execute("SHOW transaction_isolation").fetchone()[0] != "read committed":
                    raise CommandDiagnosticUpgradeError("transaction-unavailable")
                _target(connection, plan)
                connection.execute(_ADVISORY_LOCK_SQL)
                state = _recognized(connection)
                for relation in SOURCE.relations:
                    mode = "ACCESS EXCLUSIVE" if relation.name == "cpk_execution_command_receipts" else "SHARE"
                    connection.execute(f'LOCK TABLE ONLY "{relation.name}" IN {mode} MODE')
                # Recognition is repeated under all locks; the earlier read confers no authority.
                if _recognized(connection) != state:
                    raise CommandDiagnosticUpgradeError("schema-mismatch")
                _validate(connection, state)
                if state == "source":
                    connection.execute("ALTER TABLE cpk_execution_command_receipts ADD COLUMN diagnostic jsonb, "
                        "ADD CONSTRAINT cpk_execution_command_receipts_diagnostic_check CHECK ("
                        + _COMMAND_DIAGNOSTIC_CONSTRAINT.check_expression + ")")
                    if _recognized(connection) != "target":
                        raise CommandDiagnosticUpgradeError("schema-mismatch")
                    _validate(connection, "target")
                _restore_timeouts(connection, previous)
                result = CommandDiagnosticUpgradeResult(plan.fingerprint, SOURCE_SHA256, TARGET_SHA256,
                    state == "target", provisional)
            except Exception as error:
                failure = CommandDiagnosticUpgradeError(error.category if isinstance(error,
                    CommandDiagnosticUpgradeError) else "transition-failed")
                raise failure from None
    except Exception as error:
        exit_error = error
    if exit_error is not None:
        if failure is not None and exit_error is failure:
            raise CommandDiagnosticUpgradeError(failure.category)
        raise CommandDiagnosticUpgradeUncertain("transition-failed" if failure is None else failure.category)
    return result


def inspect_command_diagnostic_upgrade(connection, plan, *, principal, administration_policy):
    """Fresh read-only snapshot; no mutation, repair, retry or acknowledgement inference."""
    _require(plan, principal, administration_policy)
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise CommandDiagnosticUpgradeError("transaction-unavailable")
    failure = None
    try:
        with connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            _timeouts(connection)
            _target(connection, plan)
            connection.execute(_ADVISORY_LOCK_SQL)
            state = _recognized(connection)
            _validate(connection, state)
            result = CommandDiagnosticUpgradeInspection(plan.fingerprint, state,
                SOURCE_SHA256 if state == "source" else TARGET_SHA256)
    except Exception as error:
        failure = error.category if isinstance(error, CommandDiagnosticUpgradeError) else "transition-failed"
    if failure is not None:
        raise CommandDiagnosticUpgradeError(failure)
    return result
