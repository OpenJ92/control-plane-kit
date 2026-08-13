from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
import unittest

import control_plane_kit_operations.read_services as read_services
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    ProbeKind,
    ProbeOutcome,
)
from control_plane_kit_operations import ReadModelError
from control_plane_kit_operations.read_pages import (
    IdentityReadCursor,
    ReadCollection,
    ReadPage,
    ReadPageCandidate,
    ReadPageRequest,
    WorkspaceReadScope,
)
from control_plane_kit_operations.records import (
    BoundedEvidence,
    ObservationFreshness,
    ObservationRecord,
    ObservationStaleReason,
    ObservationStatus,
    WorkspaceRecord,
)

from test_read_services_package import _local_module_imports


def _record(
    observation_id: str,
    *,
    observed_at: str = "2026-08-13T12:00:00Z",
    freshness: ObservationFreshness = ObservationFreshness.FRESH,
    graph_id: str | None = "graph-current",
    evidence: dict[str, object] | None = None,
) -> ObservationRecord:
    correlated = graph_id is not None
    return ObservationRecord(
        observation_id=observation_id,
        workspace_id="workspace-a",
        subject_id=observation_id,
        status=ObservationStatus.HEALTHY,
        observed_at=observed_at,
        evidence=BoundedEvidence.from_mapping(evidence),
        freshness=freshness,
        graph_id=graph_id,
        probe_kind=ProbeKind.APPLICATION_HEALTH if correlated else None,
        probe_outcome=ProbeOutcome.HEALTHY if correlated else None,
        endpoint_context=EndpointContext.RUNTIME_PRIVATE if correlated else None,
    )


class _WorkspaceCapability:
    def __init__(
        self,
        trace: list[object],
        *,
        failure: BaseException | None = None,
    ) -> None:
        self._trace = trace
        self._failure = failure

    def __call__(self, workspace_id: str) -> WorkspaceRecord:
        self._trace.append(("workspace", workspace_id))
        if self._failure is not None:
            raise self._failure
        return WorkspaceRecord(
            workspace_id,
            "Workspace A",
            current_graph_id="graph-current",
        )


class _Clock:
    def __init__(self, trace: list[object], value: object) -> None:
        self._trace = trace
        self._value = value

    def __call__(self):
        self._trace.append("clock")
        return self._value


class _HostileClockValue:
    def __str__(self) -> str:
        return "secret://clock-string-canary"

    def __repr__(self) -> str:
        return "clock-address-canary-10.0.0.9"


class _ObservationStore:
    def __init__(
        self,
        trace: list[object],
        page: ReadPage[ObservationRecord] | None = None,
        *,
        failure: BaseException | None = None,
    ) -> None:
        self._trace = trace
        self._page = page
        self._failure = failure

    def latest_page(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[ObservationRecord]:
        self._trace.append(("latest_page", request))
        if self._failure is not None:
            raise self._failure
        if self._page is None:
            raise AssertionError("observation page is absent")
        return self._page


class ObservationReadProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.module = importlib.import_module(
                "control_plane_kit_operations.read_services.observations"
            )
        except ModuleNotFoundError as error:
            self.fail(f"observation read projection is absent: {error.name}")

    @staticmethod
    def _as_of() -> datetime:
        return datetime(2026, 8, 13, 12, 5, tzinfo=timezone.utc)

    def test_projection_precedence_is_closed_and_retains_record_identity(self) -> None:
        policy = self.module.ObservationFreshnessPolicy()
        recorded_stale = _record(
            "recorded-stale",
            observed_at="malformed",
            freshness=ObservationFreshness.STALE,
            graph_id=None,
        )
        vectors = (
            (
                recorded_stale,
                "graph-current",
                ObservationFreshness.STALE,
                ObservationStaleReason.RECORDED_STALE,
            ),
            (
                replace(
                    recorded_stale,
                    observation_id="uncorrelated",
                    freshness=ObservationFreshness.FRESH,
                ),
                "graph-current",
                ObservationFreshness.STALE,
                ObservationStaleReason.UNCORRELATED,
            ),
            (
                _record("graph-changed", observed_at="malformed", graph_id="graph-old"),
                "graph-current",
                ObservationFreshness.STALE,
                ObservationStaleReason.GRAPH_CHANGED,
            ),
            (
                _record("malformed", observed_at="malformed"),
                "graph-current",
                ObservationFreshness.STALE,
                ObservationStaleReason.MALFORMED_TIMESTAMP,
            ),
            (
                _record("future", observed_at="2026-08-13T12:05:00.000001Z"),
                "graph-current",
                ObservationFreshness.STALE,
                ObservationStaleReason.FUTURE_TIMESTAMP,
            ),
            (
                _record("expired", observed_at="2026-08-13T11:59:59.999999Z"),
                "graph-current",
                ObservationFreshness.STALE,
                ObservationStaleReason.EXPIRED,
            ),
            (
                _record("boundary", observed_at="2026-08-13T12:00:00Z"),
                "graph-current",
                ObservationFreshness.FRESH,
                None,
            ),
            (
                _record("ordinary", observed_at="2026-08-13T12:04:00Z"),
                "graph-current",
                ObservationFreshness.FRESH,
                None,
            ),
        )
        for record, graph_id, freshness, reason in vectors:
            with self.subTest(observation_id=record.observation_id):
                before = record
                projected = self.module.project_observation(
                    record,
                    current_graph_id=graph_id,
                    as_of=self._as_of(),
                    policy=policy,
                )
                self.assertIs(projected.record, record)
                self.assertIs(record, before)
                self.assertIs(projected.freshness, freshness)
                self.assertIs(projected.stale_reason, reason)

    def test_clock_contract_and_policy_boundary_remain_exact(self) -> None:
        policy = self.module.ObservationFreshnessPolicy(timedelta(minutes=5))
        self.assertEqual(policy.maximum_age, timedelta(minutes=5))
        for maximum_age in (timedelta(0), timedelta(microseconds=-1)):
            with self.subTest(maximum_age=maximum_age):
                with self.assertRaisesRegex(ValueError, "maximum age must be positive"):
                    self.module.ObservationFreshnessPolicy(maximum_age)
        with self.assertRaisesRegex(ValueError, "clock must be timezone-aware"):
            self.module.project_observation(
                _record("naive"),
                current_graph_id="graph-current",
                as_of=datetime(2026, 8, 13, 12, 5),
                policy=policy,
            )

    def test_early_exit_and_operational_failure_traces_are_exact(self) -> None:
        request = ReadPageRequest(
            ReadCollection.LATEST_OBSERVATIONS,
            WorkspaceReadScope("workspace-a"),
            1,
        )
        for category in ("missing", "foreign"):
            workspace_failure = ReadModelError(f"workspace is {category}")
            trace: list[object] = []
            projection = self.module._ObservationReadProjection(
                _WorkspaceCapability(trace, failure=workspace_failure),
                _ObservationStore(trace),
                clock=_Clock(trace, self._as_of()),
                freshness=self.module.ObservationFreshnessPolicy(),
            )
            with self.subTest(workspace_failure=category):
                with self.assertRaises(ReadModelError) as caught:
                    projection.observed_state(request)
                self.assertIs(caught.exception, workspace_failure)
                self.assertEqual(trace, [("workspace", "workspace-a")])

        for invalid_clock in (
            _HostileClockValue(),
            datetime(2026, 8, 13, 12, 5),
        ):
            trace = []
            projection = self.module._ObservationReadProjection(
                _WorkspaceCapability(trace),
                _ObservationStore(trace),
                clock=_Clock(trace, invalid_clock),
                freshness=self.module.ObservationFreshnessPolicy(),
            )
            with self.subTest(invalid_clock=invalid_clock):
                with self.assertRaisesRegex(
                    ReadModelError,
                    "read-service clock must return a timezone-aware datetime",
                ) as caught:
                    projection.observed_state(request)
                message = "read-service clock must return a timezone-aware datetime"
                self.assertEqual(str(caught.exception), message)
                self.assertEqual(repr(caught.exception), f"ReadModelError({message!r})")
                for canary in (
                    "clock-string-canary",
                    "clock-address-canary",
                    "10.0.0.9",
                ):
                    self.assertNotIn(canary, str(caught.exception))
                    self.assertNotIn(canary, repr(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertEqual(trace, [("workspace", "workspace-a"), "clock"])

        trace = []
        projection = self.module._ObservationReadProjection(
            _WorkspaceCapability(trace),
            None,
            clock=_Clock(trace, self._as_of()),
            freshness=self.module.ObservationFreshnessPolicy(),
        )
        with self.assertRaisesRegex(
            ReadModelError,
            "observed state store is not configured",
        ) as caught:
            projection.observed_state(request)
        message = "observed state store is not configured"
        self.assertEqual(str(caught.exception), message)
        self.assertEqual(repr(caught.exception), f"ReadModelError({message!r})")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(trace, [("workspace", "workspace-a"), "clock"])

        driver_failure = RuntimeError("provider candidate secret://do-not-disclose")
        trace = []
        projection = self.module._ObservationReadProjection(
            _WorkspaceCapability(trace),
            _ObservationStore(trace, failure=driver_failure),
            clock=_Clock(trace, self._as_of()),
            freshness=self.module.ObservationFreshnessPolicy(),
        )
        with self.assertRaises(RuntimeError) as caught:
            projection.observed_state(request)
        self.assertIs(caught.exception, driver_failure)
        self.assertEqual(
            trace,
            [("workspace", "workspace-a"), "clock", ("latest_page", request)],
        )

    def test_success_preserves_page_and_redacts_nested_sensitive_keys(self) -> None:
        request = ReadPageRequest(
            ReadCollection.LATEST_OBSERVATIONS,
            WorkspaceReadScope("workspace-a"),
            1,
        )
        first_cursor = IdentityReadCursor(
            ReadCollection.LATEST_OBSERVATIONS,
            request.scope,
            "hello",
        )
        hidden_cursor = IdentityReadCursor(
            ReadCollection.LATEST_OBSERVATIONS,
            request.scope,
            "worker",
        )
        first = _record(
            "hello",
            evidence={
                "message": "ok",
                "nested": {
                    "secret": "secret-value",
                    "token": "token-value",
                    "private_key": "private-value",
                    "credential": "credential-value",
                    "address": "10.0.0.2",
                    "url": "http://internal",
                    "environment_bindings": [
                        {"name": "API_TOKEN", "value": "binding-value"}
                    ],
                },
            },
        )
        page = ReadPage.from_candidates(
            request,
            (
                ReadPageCandidate(first, first_cursor),
                ReadPageCandidate(_record("worker"), hidden_cursor),
            ),
        )
        trace: list[object] = []
        projection = self.module._ObservationReadProjection(
            _WorkspaceCapability(trace),
            _ObservationStore(trace, page),
            clock=_Clock(trace, self._as_of()),
            freshness=self.module.ObservationFreshnessPolicy(),
        )

        result = projection.observed_state(request)

        self.assertIs(result.request, request)
        self.assertIs(result.next_cursor, first_cursor)
        self.assertEqual([item["observation_id"] for item in result.items], ["hello"])
        self.assertEqual(result.items[0]["payload"]["message"], "ok")
        nested = result.items[0]["payload"]["nested"]
        for key in ("secret", "token", "private_key", "credential", "address", "url"):
            self.assertEqual(nested[key], "<redacted>")
        self.assertEqual(
            nested["environment_bindings"],
            [{"name": "API_TOKEN", "value": "<redacted>"}],
        )
        self.assertIs(first.freshness, ObservationFreshness.FRESH)
        self.assertEqual(
            trace,
            [("workspace", "workspace-a"), "clock", ("latest_page", request)],
        )


class ObservationReadProjectionStructureTests(unittest.TestCase):
    def _trees(self) -> tuple[ast.Module, ast.Module]:
        paths = tuple(getattr(read_services, "__path__", ()))
        self.assertEqual(len(paths), 1)
        package_path = Path(paths[0])
        owner_path = package_path / "observations.py"
        self.assertTrue(owner_path.is_file(), "observation projection owner is absent")
        return (
            ast.parse(owner_path.read_text(encoding="utf-8")),
            ast.parse((package_path / "instance.py").read_text(encoding="utf-8")),
        )

    def test_owner_and_facade_have_exact_definition_shape(self) -> None:
        owner, instance = self._trees()
        owner_classes = {
            node.name for node in owner.body if isinstance(node, ast.ClassDef)
        }
        owner_functions = {
            node.name
            for node in owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertEqual(
            owner_classes,
            {
                "ObservationFreshnessPolicy",
                "ProjectedObservation",
                "_ObservationReadProjection",
            },
        )
        self.assertEqual(
            owner_functions,
            {"project_observation", "_stale", "_observation_descriptor"},
        )
        projection = next(
            node
            for node in owner.body
            if isinstance(node, ast.ClassDef)
            and node.name == "_ObservationReadProjection"
        )
        self.assertEqual(
            {
                node.name
                for node in projection.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            },
            {"__init__", "observed_state", "_observed_state"},
        )
        instance_definitions = {
            node.name
            for node in ast.walk(instance)
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue(
            {
                "ObservationFreshnessPolicy",
                "ProjectedObservation",
                "project_observation",
                "_stale",
                "_observation_descriptor",
                "_ObservationReadProjection",
            }.isdisjoint(instance_definitions)
        )

    def test_facade_is_one_step_delegate_and_retains_only_projection(self) -> None:
        _owner, instance = self._trees()
        service = next(
            node
            for node in instance.body
            if isinstance(node, ast.ClassDef) and node.name == "InstanceReadService"
        )
        methods = {
            node.name: node for node in service.body if isinstance(node, ast.FunctionDef)
        }
        method = methods["observed_state"]
        self.assertEqual(len(method.body), 1)
        returned = method.body[0]
        self.assertIsInstance(returned, ast.Return)
        self.assertIsInstance(returned.value, ast.Call)
        function = returned.value.func
        self.assertIsInstance(function, ast.Attribute)
        self.assertEqual(function.attr, "observed_state")
        self.assertIsInstance(function.value, ast.Attribute)
        self.assertEqual(function.value.attr, "_observations")
        self.assertIsInstance(function.value.value, ast.Name)
        self.assertEqual(function.value.value.id, "self")
        self.assertEqual(len(returned.value.args), 1)
        self.assertIsInstance(returned.value.args[0], ast.Name)
        self.assertEqual(returned.value.args[0].id, "request")
        self.assertEqual(returned.value.keywords, [])

        initializer = methods["__init__"]
        assigned = {
            target.attr
            for node in ast.walk(initializer)
            for target in (
                node.targets
                if isinstance(node, ast.Assign)
                else (node.target,)
                if isinstance(node, ast.AnnAssign)
                else ()
            )
            if isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        }
        self.assertIn("_observations", assigned)
        self.assertTrue(
            {"_observed_state_store", "_clock", "_observation_freshness"}.isdisjoint(
                assigned
            )
        )

    def test_owner_forbidden_edges_cover_all_shared_parser_forms(self) -> None:
        owner, _instance = self._trees()
        forbidden = {"instance", "workspace_graph", "operations_history"}
        self.assertEqual(_local_module_imports(owner, forbidden), set())
        templates = (
            "from .{name} import Value",
            "from . import {name}",
            "from control_plane_kit_operations.read_services.{name} import Value",
            "from control_plane_kit_operations.read_services import {name}",
            "import control_plane_kit_operations.read_services.{name}",
        )
        for name in forbidden:
            for template in templates:
                source = template.format(name=name)
                with self.subTest(source=source):
                    self.assertEqual(
                        _local_module_imports(ast.parse(source), forbidden),
                        {name},
                    )


if __name__ == "__main__":
    unittest.main()
