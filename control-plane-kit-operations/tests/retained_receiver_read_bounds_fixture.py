"""Real ordinary-start footprint observations; never permission or acceptance."""
from contextlib import contextmanager
from unittest import mock

from control_plane_kit_operations import _configuration_preparation as support
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
from control_plane_kit_operations.postgres import configuration_preparation_store as preparation
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead


@contextmanager
def conservative_suffix(self):
    """Observe every prospective query, including reservations refused before SQL."""
    original_budget, original_query = preparation._ordinary_tail_budget, _EvidenceRead.query
    original_bind = preparation._OrdinaryStartReadBoundsOwner.bind
    witnesses = []

    def budget(owner, *args, **kwargs):
        result = original_budget(owner, *args, **kwargs)
        witnesses.append(dict(owner=owner, accounting=owner._accounting,
            before=owner._accounting.used, budget=result[0], peaks=[], settled=None))
        return result

    def query(read, sql, params, *, records, octets, cells, identities=1):
        issued = support._BOUND_ORDINARY_START.get()
        for witness in witnesses:
            if (issued is not None and issued.owner is witness["owner"]
                    and read.accounting is witness["accounting"]):
                reserve = ConfigurationEvidenceFootprint(records * identities, octets, records * cells, 1)
                witness["peaks"].append(read.used.plus(reserve))
        return original_query(read, sql, params, records=records,
            octets=octets, cells=cells, identities=identities)

    @contextmanager
    def bind(owner, *args, **kwargs):
        try:
            with original_bind(owner, *args, **kwargs) as result:
                yield result
        finally:
            for witness in witnesses:
                if witness["owner"] is owner:
                    witness["settled"] = witness["accounting"].used

    with mock.patch.object(preparation, "_ordinary_tail_budget", budget), \
            mock.patch.object(_EvidenceRead, "query", query), \
            mock.patch.object(preparation._OrdinaryStartReadBoundsOwner, "bind", bind):
        try:
            yield witnesses
        finally:
            self.assertTrue(witnesses, "retained update did not enter owner preparation")
            for witness in witnesses:
                self.assertTrue(witness["peaks"])
                self.assertIsNotNone(witness["settled"])
                for name in ("records", "value_octets", "scalar_markers", "statements"):
                    prior = getattr(witness["before"], name)
                    for peak in witness["peaks"]:
                        self.assertLessEqual(getattr(peak, name) - prior,
                            getattr(witness["budget"].peak, name),
                            f"retained suffix prospective {name} exceeds forecast")
                    self.assertLessEqual(getattr(witness["settled"], name) - prior,
                        getattr(witness["budget"].settled, name),
                        f"retained suffix settled {name} exceeds forecast")

