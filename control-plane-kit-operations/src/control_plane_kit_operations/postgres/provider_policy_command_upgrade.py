"""One explicit additive transition for provider-policy command truth."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files

from psycopg.pq import TransactionStatus

from control_plane_kit_operations.provider_policy_commands import (
    ProviderPolicyAdministrationPolicy,
    ProviderPolicyInvocationContext,
)
from .current_data_validation import validate_current_rows
from .current_schema_contract import (
    CURRENT_POSTGRES_SCHEMA_CONTRACT,
    PRE_PROVIDER_POLICY_COMMAND_SCHEMA_CONTRACT,
)
from .current_schema_verification import (
    current_namespace_adjuncts_are_exact,
    current_schema_contract_is_exact,
)
from .schema import _ADVISORY_LOCK_SQL


_ADDITIVE_SQL = files(__package__).joinpath(
    "provider_policy_command_schema.sql"
).read_text(encoding="utf-8")


class ProviderPolicyCommandUpgradeError(RuntimeError):
    """The retained namespace was refused or failed before commit acknowledgement."""


class ProviderPolicyCommandUpgradeUncertain(ProviderPolicyCommandUpgradeError):
    """Transaction exit was not acknowledged; inspect before proposing another attempt."""


@dataclass(frozen=True)
class ProviderPolicyCommandUpgradeResult:
    replayed: bool
    provisional: bool


def _require(context, policy):
    if (
        type(context) is not ProviderPolicyInvocationContext
        or type(policy) is not ProviderPolicyAdministrationPolicy
    ):
        raise ProviderPolicyCommandUpgradeError("provider-policy upgrade authority is malformed")
    try:
        policy.require(context)
    except Exception:
        raise ProviderPolicyCommandUpgradeError("provider-policy upgrade is denied") from None


def _recognized_contract(connection):
    rows = connection.execute(
        "SELECT relname,relkind FROM pg_class "
        "WHERE relnamespace=current_schema()::regnamespace "
        "AND relkind NOT IN ('i','I') ORDER BY relname LIMIT %s",
        (len(CURRENT_POSTGRES_SCHEMA_CONTRACT.relations) + 1,),
    ).fetchall()
    for contract in (
        PRE_PROVIDER_POLICY_COMMAND_SCHEMA_CONTRACT,
        CURRENT_POSTGRES_SCHEMA_CONTRACT,
    ):
        if rows == [(relation.name, relation.kind) for relation in contract.relations]:
            return contract
    raise ProviderPolicyCommandUpgradeError(
        "retained schema is not a supported provider-policy transition state"
    )


def _lock_and_validate(connection, contract):
    connection.execute("\n".join(
        f'LOCK TABLE ONLY "{relation.name}" IN SHARE MODE;'
        for relation in contract.relations
    ))
    if (
        not current_schema_contract_is_exact(connection, contract=contract)
        or not current_namespace_adjuncts_are_exact(connection)
    ):
        raise ProviderPolicyCommandUpgradeError(
            "retained schema does not match its accepted contract"
        )
    validate_current_rows(connection)
    from .workspace_permission_store import validate_workspace_permission_rows
    validate_workspace_permission_rows(connection)
    from .managed_runtime_assignment_store import validate_managed_runtime_assignment_rows
    validate_managed_runtime_assignment_rows(connection)
    from .execution import validate_execution_invocation_rows
    validate_execution_invocation_rows(connection)
    from .runtime_effect_origin_store import validate_runtime_effect_origin_rows
    validate_runtime_effect_origin_rows(connection)
    from .ingress_invocation_store import validate_ingress_invocation_rows
    validate_ingress_invocation_rows(connection)
    if contract is CURRENT_POSTGRES_SCHEMA_CONTRACT:
        from .provider_policy_command_store import validate_provider_policy_command_rows
        validate_provider_policy_command_rows(connection)


def upgrade_provider_policy_commands(
    connection,
    context,
    *,
    administration_policy,
) -> ProviderPolicyCommandUpgradeResult:
    """Apply only the reviewed prior-to-current transition; caller owns outer commit."""
    _require(context, administration_policy)
    verified = False
    try:
        status = connection.info.transaction_status
        if status not in (TransactionStatus.IDLE, TransactionStatus.INTRANS):
            raise ProviderPolicyCommandUpgradeError("provider-policy upgrade requires a usable transaction")
        provisional = status == TransactionStatus.INTRANS
        with connection.transaction():
            if connection.execute("SHOW transaction_isolation").fetchone()[0] != "read committed":
                raise ProviderPolicyCommandUpgradeError(
                    "provider-policy upgrade requires read committed isolation"
                )
            connection.execute(_ADVISORY_LOCK_SQL)
            contract = _recognized_contract(connection)
            _lock_and_validate(connection, contract)
            replayed = contract is CURRENT_POSTGRES_SCHEMA_CONTRACT
            if not replayed:
                connection.execute(_ADDITIVE_SQL)
                _lock_and_validate(connection, CURRENT_POSTGRES_SCHEMA_CONTRACT)
            result = ProviderPolicyCommandUpgradeResult(replayed, provisional)
            verified = True
        return result
    except ProviderPolicyCommandUpgradeError:
        raise
    except Exception:
        if verified:
            raise ProviderPolicyCommandUpgradeUncertain(
                "provider-policy upgrade outcome is uncertain; inspect before retry"
            ) from None
        raise ProviderPolicyCommandUpgradeError(
            "provider-policy upgrade was refused or failed before commit"
        ) from None


__all__ = [
    "ProviderPolicyCommandUpgradeError",
    "ProviderPolicyCommandUpgradeResult",
    "ProviderPolicyCommandUpgradeUncertain",
    "upgrade_provider_policy_commands",
]
