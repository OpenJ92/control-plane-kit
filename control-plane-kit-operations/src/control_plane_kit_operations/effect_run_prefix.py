"""Internal caller-transaction run preparation shared by effect interpreters."""

from dataclasses import dataclass, field

from control_plane_kit_operations.records import ActivityRunRecord, ExecutionRequestRecord


class EffectRunPrefixConflict(ValueError):
    """The scoped locator or prepared transaction no longer agrees."""


@dataclass(frozen=True)
class PreparedEffectRunPrefix:
    request: ExecutionRequestRecord
    requested_run: ActivityRunRecord
    latest_run: ActivityRunRecord | None
    held_run_ids: tuple[str, ...]
    _owner: object = field(repr=False, compare=False)

    def require(self, unit_of_work, request, requested_run_id, *, latest_required):
        expected = {self.requested_run.run_id}
        if self.latest_run is not None:
            expected.add(self.latest_run.run_id)
        if (unit_of_work.stores.execution is not self._owner or self.request != request
                or self.requested_run.run_id != requested_run_id
                or latest_required and self.latest_run is None
                or set(self.held_run_ids) != expected):
            raise EffectRunPrefixConflict("effect run prefix does not belong to this transaction")
        retained = {self.requested_run.run_id: self.requested_run}
        if self.latest_run is not None:
            retained[self.latest_run.run_id] = self.latest_run
        # Reentry only: never discover or acquire a new latest key after a
        # caller has advanced to attempt/runtime locks. Same-UoW writes can
        # change mutable run truth even though other transactions are excluded.
        for run_id in self.held_run_ids:
            current = unit_of_work.stores.execution.get_run_for_request_for_update(
                request.identity.request_id, run_id)
            if current != retained[run_id]:
                raise EffectRunPrefixConflict("prepared effect run changed in this transaction")


def _lock_effect_run_prefix(unit_of_work, locked_request, requested_run_id, *, latest_required):
    """Caller already holds the exact request; enter before any attempt/runtime.

    Locate bounded, request-scoped keys and lock predecessor before successor.
    This helper has no clock, attempt, provider or commit effect.
    """
    if type(locked_request) is not ExecutionRequestRecord or type(latest_required) is not bool:
        raise EffectRunPrefixConflict("effect request prefix is invalid")
    store = unit_of_work.stores.execution
    request_id = locked_request.identity.request_id
    requested = store.get_run(requested_run_id)
    if (type(requested) is not ActivityRunRecord or requested.run_id != requested_run_id
            or requested.admission.request_id != request_id
            or requested.plan_id != locked_request.identity.plan_id):
        raise EffectRunPrefixConflict("effect run does not belong to the request")
    latest = store.get_latest_run_for_request(request_id) if latest_required else None
    if latest_required and type(latest) is not ActivityRunRecord:
        raise EffectRunPrefixConflict("latest effect run is invalid")
    runs = (requested,) if latest is None or latest.run_id == requested.run_id else (requested, latest)
    for run in runs:
        if (type(run) is not ActivityRunRecord or run.admission.request_id != request_id
                or run.plan_id != locked_request.identity.plan_id):
            raise EffectRunPrefixConflict("effect run does not belong to the request")
    if (requested.run_id != requested_run_id
            or latest is not None and (latest.retry.attempt < requested.retry.attempt
                or latest.run_id != requested.run_id and latest.retry.attempt == requested.retry.attempt
                or latest.run_id == requested.run_id and latest != requested)):
        raise EffectRunPrefixConflict("effect run locators disagree")
    held = []
    for locator in sorted(runs, key=lambda value: value.retry.attempt):
        run = store.get_run_for_request_for_update(request_id, locator.run_id)
        if run != locator:
            raise EffectRunPrefixConflict("effect run changed during preparation")
        held.append(run.run_id)
    return PreparedEffectRunPrefix(locked_request, requested, latest, tuple(held), store)
