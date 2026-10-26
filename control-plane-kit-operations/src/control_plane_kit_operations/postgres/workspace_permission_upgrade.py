"""One explicit, additive transition from the accepted pre-policy schema."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files

from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.workspace_permissions import (
    GrantWorkspacePermissions, WorkspaceAdministrationContext, WorkspaceAdministrationPolicy,
    WorkspacePermissionUpgradeReceipt, _key, permission_fingerprint, permission_intent,
)
from .current_data_validation import validate_current_rows
from .current_schema_contract import (
    CURRENT_POSTGRES_SCHEMA_CONTRACT, CURRENT_POSTGRES_SCHEMA_CONTRACT_SHA256,
    PRE_WORKSPACE_PERMISSION_SCHEMA_CONTRACT,
)
from .current_schema_verification import (
    current_namespace_adjuncts_are_exact, current_schema_contract_is_exact,
)
from .schema import _ADVISORY_LOCK_SQL
from .workspace_permission_store import WorkspacePermissionStore, validate_workspace_permission_rows


_SOURCE_SHA256 = "c8cc42059c06c898275ba19aa2a6bf8df3d0e93474bb23340bab7b49bcfc9141"
_ADDITIVE_SQL = files(__package__).joinpath("workspace_permission_schema.sql").read_text(encoding="utf-8")
_TRY_LOCK_SQL = _ADVISORY_LOCK_SQL.replace("pg_advisory_xact_lock", "pg_try_advisory_xact_lock")


class WorkspacePermissionUpgradeError(RuntimeError):
    """The explicit transition was refused or failed before commit acknowledgement."""


class WorkspacePermissionUpgradeUncertain(WorkspacePermissionUpgradeError):
    """Transaction exit was not acknowledged; inspect before proposing any retry."""

    def __init__(self, plan_fingerprint):
        super().__init__("workspace permission upgrade outcome is uncertain; inspection is required")
        self.plan_fingerprint = plan_fingerprint


@dataclass(frozen=True)
class WorkspacePermissionUpgradePlan:
    context: WorkspaceAdministrationContext
    grants: tuple[GrantWorkspacePermissions, ...]
    idempotency_key: IdempotencyKey
    source_contract_sha256: str = _SOURCE_SHA256
    target_contract_sha256: str = CURRENT_POSTGRES_SCHEMA_CONTRACT_SHA256

    def __post_init__(self):
        if type(self.context) is not WorkspaceAdministrationContext:
            raise WorkspacePermissionUpgradeError("upgrade context is malformed")
        _key(self.idempotency_key)
        if type(self.grants) is not tuple or len(self.grants) > 1024:
            raise WorkspacePermissionUpgradeError("upgrade seeds are malformed")
        seen = set()
        for grant in self.grants:
            if (type(grant) is not GrantWorkspacePermissions or grant.context != self.context
                    or grant.expected_revision != 0):
                raise WorkspacePermissionUpgradeError("upgrade seed command is malformed")
            key = (grant.workspace_id, grant.target)
            if key in seen:
                raise WorkspacePermissionUpgradeError("upgrade seed target is duplicated")
            seen.add(key)
        for value in (self.source_contract_sha256, self.target_contract_sha256):
            if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise WorkspacePermissionUpgradeError("upgrade contract fingerprint is malformed")

    def descriptor(self):
        return dict(installation_id=self.context.installation_id, actor=self.context.identity.descriptor(),
            idempotency_key=self.idempotency_key.value, source_contract_sha256=self.source_contract_sha256,
            target_contract_sha256=self.target_contract_sha256,
            grants=[permission_intent(grant, "grant") for grant in self.grants])

    @property
    def fingerprint(self):
        return permission_fingerprint(self.descriptor())

    @property
    def seed_fingerprint(self):
        return permission_fingerprint(dict(grants=self.descriptor()["grants"]))


@dataclass(frozen=True)
class WorkspacePermissionUpgradeResult:
    receipt: WorkspacePermissionUpgradeReceipt
    replayed: bool
    provisional: bool

    def descriptor(self):
        return dict(receipt=self.receipt.descriptor(), replayed=self.replayed, provisional=self.provisional)


@dataclass(frozen=True)
class WorkspacePermissionUpgradeInspection:
    status: str
    plan_fingerprint: str
    receipt: WorkspacePermissionUpgradeReceipt | None = None

    def descriptor(self):
        return dict(status=self.status, plan_fingerprint=self.plan_fingerprint,
                    receipt=None if self.receipt is None else self.receipt.descriptor())


def _require_plan(plan, policy):
    if type(plan) is not WorkspacePermissionUpgradePlan or type(policy) is not WorkspaceAdministrationPolicy:
        raise WorkspacePermissionUpgradeError("upgrade authority or plan is malformed")
    policy.require(plan.context)
    if (plan.source_contract_sha256 != _SOURCE_SHA256
            or plan.target_contract_sha256 != CURRENT_POSTGRES_SCHEMA_CONTRACT_SHA256):
        raise WorkspacePermissionUpgradeError("upgrade plan does not match the supported transition")


def _recognized_contract(connection):
    # Preliminary recognition chooses a complete lock set, not schema acceptance.
    # Reject other relation kinds/names; indexes receive full semantic checking later.
    rows = connection.execute(
        "SELECT relname,relkind FROM pg_class WHERE relnamespace=current_schema()::regnamespace "
        "AND relkind NOT IN ('i','I') ORDER BY relname LIMIT %s",
        (len(CURRENT_POSTGRES_SCHEMA_CONTRACT.relations) + 1,),
    ).fetchall()
    for contract in (PRE_WORKSPACE_PERMISSION_SCHEMA_CONTRACT, CURRENT_POSTGRES_SCHEMA_CONTRACT):
        if rows == [(relation.name, relation.kind) for relation in contract.relations]:
            return contract
    raise WorkspacePermissionUpgradeError("retained schema is not a supported transition state")


def _lock_and_validate(connection, contract):
    connection.execute("\n".join(f'LOCK TABLE ONLY "{relation.name}" IN SHARE MODE;'
                                 for relation in contract.relations))
    if (not current_schema_contract_is_exact(connection, contract=contract)
            or not current_namespace_adjuncts_are_exact(connection)):
        raise WorkspacePermissionUpgradeError("retained schema does not match its accepted contract")
    validate_current_rows(connection)
    if contract is CURRENT_POSTGRES_SCHEMA_CONTRACT:
        validate_workspace_permission_rows(connection)


def _matches(receipt, plan):
    return (type(receipt) is WorkspacePermissionUpgradeReceipt
        and receipt.installation_id == plan.context.installation_id
        and receipt.actor == plan.context.identity
        and receipt.idempotency_key == plan.idempotency_key.value
        and receipt.source_contract_sha256 == plan.source_contract_sha256
        and receipt.target_contract_sha256 == plan.target_contract_sha256
        and receipt.seed_fingerprint == plan.seed_fingerprint
        and receipt.seed_count == len(plan.grants)
        and receipt.plan_fingerprint == plan.fingerprint)


def upgrade_workspace_permissions(connection, plan, *, administration_policy, clock, id_factory):
    """Apply one reviewed plan; active caller transactions retain commit authority."""
    _require_plan(plan, administration_policy)
    verified = False
    try:
        status = connection.info.transaction_status
        if status not in (TransactionStatus.IDLE, TransactionStatus.INTRANS):
            raise WorkspacePermissionUpgradeError("upgrade requires a usable transaction")
        provisional = status == TransactionStatus.INTRANS
        with connection.transaction():
            # A pre-existing repeatable snapshot cannot observe a writer that
            # commits while we wait for table locks. Refuse it rather than certify it.
            if connection.execute("SHOW transaction_isolation").fetchone()[0] != "read committed":
                raise WorkspacePermissionUpgradeError("upgrade requires read committed isolation")
            connection.execute(_ADVISORY_LOCK_SQL)
            contract = _recognized_contract(connection)
            _lock_and_validate(connection, contract)
            store = WorkspacePermissionStore(connection)
            if contract is CURRENT_POSTGRES_SCHEMA_CONTRACT:
                receipt = store.installation_receipt()
                if not _matches(receipt, plan):
                    raise WorkspacePermissionUpgradeError("upgrade receipt conflicts with the plan")
                result = WorkspacePermissionUpgradeResult(receipt, True, provisional)
            else:
                # Admission applies only to new seeds. Exact replay preserves
                # receipts even if a credential identity was subsequently removed.
                for grant in plan.grants:
                    administration_policy.require_known(grant.target)
                receipt = WorkspacePermissionUpgradeReceipt(plan.context.installation_id,
                    plan.context.identity, plan.idempotency_key.value, clock(),
                    plan.source_contract_sha256, plan.target_contract_sha256,
                    plan.seed_fingerprint, len(plan.grants), plan.fingerprint)
                connection.execute(_ADDITIVE_SQL)
                connection.execute("INSERT INTO cpk_workspace_permission_installation "
                    "(singleton,installation_id,receipt) VALUES (true,%s,%s)",
                    (receipt.installation_id, Jsonb(receipt.descriptor())))
                for grant in plan.grants:
                    store.change(grant, "grant", clock=clock, id_factory=id_factory)
                _lock_and_validate(connection, CURRENT_POSTGRES_SCHEMA_CONTRACT)
                result = WorkspacePermissionUpgradeResult(receipt, False, provisional)
            verified = True
        return result
    except Exception:
        if verified:
            raise WorkspacePermissionUpgradeUncertain(plan.fingerprint) from None
        raise WorkspacePermissionUpgradeError("workspace permission upgrade was refused or failed before commit") from None


def inspect_workspace_permission_upgrade(connection, plan, *, administration_policy):
    """Observe independently committed truth; never redispatch, seed or repair."""
    _require_plan(plan, administration_policy)
    unknown = WorkspacePermissionUpgradeInspection("unknown", plan.fingerprint)
    try:
        # Do not end, alter or certify a caller's active transaction.
        if connection.info.transaction_status != TransactionStatus.IDLE:
            return unknown
        with connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            connection.execute("SET LOCAL lock_timeout = '1000ms'")
            connection.execute("SET LOCAL statement_timeout = '5000ms'")
            if connection.execute(_TRY_LOCK_SQL).fetchone()[0] is not True:
                return unknown
            contract = _recognized_contract(connection)
            _lock_and_validate(connection, contract)
            if contract is PRE_WORKSPACE_PERMISSION_SCHEMA_CONTRACT:
                result = WorkspacePermissionUpgradeInspection("supported-prior", plan.fingerprint)
            else:
                receipt = WorkspacePermissionStore(connection).installation_receipt()
                result = (WorkspacePermissionUpgradeInspection("matching-committed", plan.fingerprint, receipt)
                          if _matches(receipt, plan) else
                          WorkspacePermissionUpgradeInspection("conflicting-receipt", plan.fingerprint))
        return result
    except Exception:
        return unknown


__all__ = ["WorkspacePermissionUpgradePlan", "WorkspacePermissionUpgradeResult",
           "WorkspacePermissionUpgradeInspection", "WorkspacePermissionUpgradeError",
           "WorkspacePermissionUpgradeUncertain", "upgrade_workspace_permissions",
           "inspect_workspace_permission_upgrade"]
