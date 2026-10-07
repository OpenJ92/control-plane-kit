"""Temporary observer-only diagnosis of the existing coordinator setup failure."""
from contextlib import ExitStack
from functools import wraps
import json
import traceback
import unittest
from unittest import mock

from control_plane_kit_operations import effect_attempt_start_interpreter as start
from control_plane_kit_operations.postgres.configuration_preparation_store import ConfigurationPreparationStore
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead
from tests.test_postgres_configuration_capacity_boundary import PostgresConfigurationCapacityBoundaryTests


class CoordinatorStartDiagnosticTests(unittest.TestCase):
    def test_existing_coordinator_configuration_setup(self):
        def observe(original):
            @wraps(original)
            def call(*args, **kwargs):
                try:
                    return original(*args, **kwargs)
                except Exception as error:
                    print("#1950 exception-locations " + json.dumps(dict(
                        owner=original.__qualname__, error=type(error).__name__,
                        frames=[(frame.name, frame.lineno) for frame in traceback.extract_tb(error.__traceback__)])),
                        flush=True)
                    raise
            return call

        case = PostgresConfigurationCapacityBoundaryTests()
        self.addCleanup(case.doCleanups)
        with ExitStack() as stack:
            for owner, names in (
                    (start, ("_require_fresh_effect_receiver_permission",)),
                    (start.EffectAttemptStartService, ("_execute_once",)),
                    (ConfigurationPreparationStore, ("_prepare", "_insert_original", "_require_original", "_require_current")),
                    (_EvidenceRead, ("query", "bounded_rows"))):
                for name in names:
                    stack.enter_context(mock.patch.object(owner, name, observe(getattr(owner, name))))
            case.setUp()
