"""#1950 strengthened accounting and new prewrite peak-admission laws.

The existing durable start owns the behavior. PostgreSQL wire observations are
independent evidence; no diagnostic capture or replacement permission is run.
"""
import unittest
from unittest import mock

import psycopg

from control_plane_kit_operations import configuration_preparation as values
from control_plane_kit_operations import effect_attempt_start_interpreter as start_owner
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint as Footprint
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartConflict, NewlyStarted
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from tests.configuration_cleanup_phase_read_bounds_fixture import _PhaseConnection, _components
from tests.configuration_preparation_fixture import ConfigurationPreparationFixture


class PostgresConfigurationStartTailTests(ConfigurationPreparationFixture, unittest.TestCase):
    def test_complete_start_suffix_accounts_for_physical_reads_and_every_raw_write(self):
        command = self.configuration_command()
        count = len(command.intent.configuration_instances.instances)
        service = self.start_service("tail-start")
        observed = dict(bytes=0, rows=0, largest_cell=0, statements=0, queries=[],
            accounting=None, role_label=lambda sql, params: "ordinary-start")
        tail, forecasts = {}, []
        actual_capacity = values.configuration_preparation_capacity
        test = self

        class MeasuredUnitOfWork(PostgresUnitOfWork):
            def __exit__(uow, *args):
                try:
                    return super().__exit__(*args)
                finally:
                    test.assertIs(_ACCOUNTING.get(), observed["accounting"])
                    tail["end"] = observed["accounting"].used

        def factory():
            observed["accounting"] = _ACCOUNTING.get()
            self.assertIsNotNone(observed["accounting"])
            return MeasuredUnitOfWork(lambda: _PhaseConnection(psycopg.connect(self.database_url), observed))

        def capacity(**kwargs):
            self.assertEqual(kwargs["current"], observed["accounting"].used)
            if not forecasts:
                tail.update(prior=kwargs["current"], query_start=len(observed["queries"]))
            forecasts.append(kwargs["reserved_future"])
            return actual_capacity(**kwargs)

        with mock.patch.object(service, "_unit_of_work_factory", factory), \
                mock.patch.object(values, "configuration_preparation_capacity", capacity):
            result = service.execute(command)
        self.assertIsInstance(result, NewlyStarted)
        self.assertTrue(forecasts, "start did not reach its real capacity owner")
        queries = observed["queries"][tail["query_start"]:]
        rows = [widths for query in queries for widths in query["widths"]]
        physical = Footprint(len(rows), sum(map(sum, rows)), sum(map(len, rows)), len(queries))
        delta = tuple(after - before for after, before in zip(
            _components(tail["end"]), _components(tail["prior"]), strict=True))
        raw = tuple(sum(query["sql"].lstrip().startswith("INSERT INTO " + table + " ")
            for query in queries) for table in (
                "cpk_effect_attempt_intents", "cpk_effect_configuration_refs", "cpk_configuration_claims"))
        self.assertEqual(raw, (1, count, count))
        self.assertEqual(sum(raw), 1 + 2 * count)
        self.assertEqual(delta[3], physical.statements, "raw start SQL escaped its command ledger")
        for index, actual in enumerate(_components(physical)):
            self.assertGreaterEqual(delta[index], actual)
        forecast = tuple(max(_components(value)[i] for value in forecasts) for i in range(4))
        for index, actual in enumerate(delta):
            self.assertLessEqual(actual, forecast[index], "complete suffix exceeded capacity forecast")
        for query in queries:
            for index, peak in enumerate(query["peak"]):
                self.assertLessEqual(peak - _components(tail["prior"])[index], forecast[index],
                    "temporary query reservation exceeded admitted peak")

    def _prior_pressure(self, component):
        command = self.configuration_command()
        before = self.complete_start_snapshot()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        actual_capacity = values.configuration_preparation_capacity
        calls = []

        def capacity(**kwargs):
            accounting = _ACCOUNTING.get()
            self.assertEqual(kwargs["current"], accounting.used)
            if not calls:
                future = kwargs["reserved_future"]
                if component == "records":
                    extra = 4096 - accounting.used.records - future.records
                    self.assertGreater(extra, 0)
                    accounting.used = accounting.used.plus(Footprint(extra, 0, 0, 0))
                    self.assertEqual(accounting.used.plus(future).records, 4096)
                else:
                    extra = 16 * 1024 * 1024 - accounting.used.plus(future).accounted_bytes
                    self.assertGreater(extra, 0)
                    accounting.used = accounting.used.plus(Footprint(0, extra, 0, 0))
                    self.assertEqual(accounting.used.plus(future).accounted_bytes, 16 * 1024 * 1024)
            calls.append(accounting.used)
            return actual_capacity(**dict(kwargs, current=accounting.used))

        with mock.patch.object(values, "configuration_preparation_capacity", capacity), \
                mock.patch.object(start_owner, "_observation", side_effect=AssertionError(
                    "peak capacity refusal reached the post-preparation lease clock")), \
                self.assertRaises(EffectAttemptStartConflict):
            service.execute(command)
        self.assertTrue(calls, "test missed the real start capacity decision")
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_prior_records_plus_settled_tail_fit_but_peak_refuses_before_clock_and_ids(self):
        self._prior_pressure("records")

    def test_prior_bytes_plus_settled_tail_fit_but_peak_refuses_before_clock_and_ids(self):
        self._prior_pressure("bytes")
