"""Current connector installation provenance; never execution authority.

Pins describe the current snapshot captured by the read. Historical runtime
authority and installation receipts neither freeze that snapshot nor establish
the connector's present external existence. Consumers must admit fresh effects.
"""
from dataclasses import dataclass, field

from control_plane_kit_core.runtime_effects import RuntimeEffectRequest
from control_plane_kit_core.secret_installation import (
    SecretInstallationReceipt, secret_installation_correlation_for_request,
    secret_material_selection_fingerprint,
)
from control_plane_kit_operations.records import OperationsRecordError


def _require(condition):
    if not condition:
        raise OperationsRecordError("current connector installation evidence is invalid")


@dataclass(frozen=True)
class CurrentConnectorInstallation:
    """One proved original installation at the captured current coordinates."""

    workspace_id: str
    current_graph_id: str
    current_realized_projection_id: str
    current_revision: int
    runtime_id: str
    node_id: str
    original_request: RuntimeEffectRequest = field(repr=False)
    receipt: SecretInstallationReceipt = field(repr=False)

    def __post_init__(self):
        valid = False
        try:
            for value in (self.workspace_id, self.current_graph_id,
                    self.current_realized_projection_id, self.runtime_id, self.node_id):
                _require(type(value) is str and bool(value.strip())
                    and len(value.encode("utf-8")) <= 2048
                    and all(ord(character) >= 32 for character in value))
            _require(type(self.current_revision) is int
                and 0 <= self.current_revision <= 9007199254740991)
            _require(type(self.original_request) is RuntimeEffectRequest
                and self.original_request.secret_resolution_grants == ())
            context = secret_installation_correlation_for_request(self.original_request)
            _require((context.source.workspace_id, context.runtime_id, context.node_id)
                == (self.workspace_id, self.runtime_id, self.node_id))
            _require(bool(context.secret_selections))
            _require(type(self.receipt) is SecretInstallationReceipt)
            SecretInstallationReceipt.__post_init__(self.receipt)
            _require(self.receipt.request_fingerprint == context.request_fingerprint
                and self.receipt.selection_fingerprint
                    == secret_material_selection_fingerprint(self.original_request))
            valid = True
        except (ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError):
            pass
        _require(valid)


@dataclass(frozen=True)
class CurrentConnectorInstallationRead:
    """Closed read outcome. Failure never retains partial installation proof."""

    state: str
    installation: CurrentConnectorInstallation | None = None

    def __post_init__(self):
        _require(type(self.state) is str and self.state in ("complete", "unavailable", "capacity"))
        if self.state == "complete":
            _require(type(self.installation) is CurrentConnectorInstallation)
            CurrentConnectorInstallation.__post_init__(self.installation)
        else:
            _require(self.installation is None)


__all__ = ["CurrentConnectorInstallation", "CurrentConnectorInstallationRead"]
