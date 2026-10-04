"""Support for the existing first-start owner; no independent commit or effect."""
from __future__ import annotations

from dataclasses import dataclass, replace
from contextlib import contextmanager
from contextvars import ContextVar
from threading import get_ident
import asyncio

from control_plane_kit_core.configuration_instances import ConfigurationInstanceRef, ConfigurationInstanceSelection
from control_plane_kit_core.planning import StartNode, ReconcileNode
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint, _birth_selection
from control_plane_kit_operations.records import OperationsRecordError


@dataclass
class _ConfigurationAccounting:
    used: ConfigurationEvidenceFootprint = ConfigurationEvidenceFootprint(0, 0, 0, 0)
    owner: object = None
    execution_context: object = None
    active: bool = True


_ACCOUNTING = ContextVar("cpk_configuration_accounting", default=None)


def _execution_context():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return (get_ident(), task)


@contextmanager
def _configuration_accounting(owner=None, *, join=False, active=True):
    """One command's accounting; never a cross-UoW mutable-authority cache."""
    execution_context = _execution_context()
    current = _ACCOUNTING.get()
    if (join and current is not None and current.owner == owner
            and current.execution_context == execution_context):
        yield current
        return
    accounting = _ConfigurationAccounting(owner=owner, execution_context=execution_context, active=active)
    token = _ACCOUNTING.set(accounting)
    try:
        yield accounting
    finally:
        _ACCOUNTING.reset(token)


@dataclass(frozen=True, repr=False)
class _PreparedConfigurationStart:
    stores: object
    guard: object
    identity: object
    intent: object
    births: tuple


def _require_prepared(value, connection, identity, intent):
    if (type(value) is not _PreparedConfigurationStart
            or value.stores.connection is not connection
            or value.identity != identity or value.intent != intent):
        raise OperationsRecordError("configuration start requires owner preparation")
    value.stores.graphs._require_receiver_lifecycle(value.guard, intent.source.workspace_id)
    value.stores.configuration_preparation._require_issued(value)
    value.stores.configuration_preparation._require_current(value)


def _require_prepared_intent(value, connection, identity, intent):
    if intent.kind is RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1:
        _require_prepared(value, connection, identity, intent)


def _propose_configuration(identity, intent, accepted_refs=None):
    if (type(intent.operation) not in (StartNode, ReconcileNode)
            or not any(material.product.runtime_contract.configuration_artifacts for material in intent.products)):
        return intent
    refs = tuple(ConfigurationInstanceRef("proposal", intent.source.workspace_id, material.runtime_id,
        material.node_id, artifact.artifact_id, artifact.target_path, artifact.media_type,
        artifact.file_mode, artifact.content_digest)
        for material in intent.products for artifact in material.product.runtime_contract.configuration_artifacts)
    # Derive each slot before constructing the selection: placeholder allocation
    # IDs are intentionally not a valid multi-slot ordinary selection.
    if accepted_refs is None:
        selected = tuple(_birth_selection(identity, ConfigurationInstanceSelection((ref,))).instances[0]
            for ref in refs)
    else:
        selected = ConfigurationInstanceSelection(accepted_refs).instances
        material = lambda ref: replace(ref, allocation_id="proposal")
        if tuple(map(material, selected)) != tuple(sorted(refs, key=lambda ref: ref.artifact_id)):
            raise OperationsRecordError("accepted configuration material is unavailable")
    return replace(intent, kind=RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1,
        configuration_instances=ConfigurationInstanceSelection(selected))
