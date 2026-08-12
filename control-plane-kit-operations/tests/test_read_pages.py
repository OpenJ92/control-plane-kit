from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
import unittest


import control_plane_kit_operations as operations
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose


READ_COLLECTION_SPECS = getattr(operations, "READ_COLLECTION_SPECS", None)
DelegationKeyReadCursor = getattr(operations, "DelegationKeyReadCursor", None)
IdentityReadCursor = getattr(operations, "IdentityReadCursor", None)
OrdinalReadCursor = getattr(operations, "OrdinalReadCursor", None)
PlanReadScope = getattr(operations, "PlanReadScope", None)
ReadCollection = getattr(operations, "ReadCollection", None)
ReadOrder = getattr(operations, "ReadOrder", None)
ReadPage = getattr(operations, "ReadPage", None)
ReadPageCandidate = getattr(operations, "ReadPageCandidate", None)
ReadPageError = getattr(operations, "ReadPageError", None)
ReadPageRequest = getattr(operations, "ReadPageRequest", None)
RunReadScope = getattr(operations, "RunReadScope", None)
SessionReadScope = getattr(operations, "SessionReadScope", None)
TemporalReadCursor = getattr(operations, "TemporalReadCursor", None)
WorkspaceReadScope = getattr(operations, "WorkspaceReadScope", None)
read_collection_spec = getattr(operations, "read_collection_spec", None)
read_cursor_from_mapping = getattr(operations, "read_cursor_from_mapping", None)


_INSTANT = "2026-08-12T12:34:56.123456Z"


class _HostileMapping(Mapping[str, object]):
    touched = False

    def __getitem__(self, key: str) -> object:
        self.touched = True
        raise AssertionError("hostile mapping was interpreted")

    def __iter__(self):
        self.touched = True
        raise AssertionError("hostile mapping was interpreted")

    def __len__(self) -> int:
        self.touched = True
        raise AssertionError("hostile mapping was interpreted")


class _HostileText(str):
    def __new__(cls, value: str):
        instance = super().__new__(cls, value)
        instance.touched = False
        return instance

    def __len__(self) -> int:
        self.touched = True
        raise AssertionError("hostile member was interpreted")

    def __str__(self) -> str:
        self.touched = True
        raise AssertionError("hostile member was rendered")

    def __repr__(self) -> str:
        self.touched = True
        raise AssertionError("hostile member was rendered")


class ReadPageContractTests(unittest.TestCase):
    def require_contract(self) -> None:
        self.assertIsNotNone(operations, "read-page public contract is missing")
        self.assertIsNotNone(ReadCollection, "ReadCollection is missing")

    def workspace(self):
        self.require_contract()
        return WorkspaceReadScope("workspace-a")

    def session(self):
        self.require_contract()
        return SessionReadScope("workspace-a", "session-a")

    def plan(self):
        self.require_contract()
        return PlanReadScope("workspace-a", "plan-a")

    def run_scope(self):
        self.require_contract()
        return RunReadScope("workspace-a", "run-a")

    def action_cursor(self, item: str = "action-a", ordinal: int = 1):
        self.require_contract()
        return OrdinalReadCursor(
            ReadCollection.SESSION_ACTIONS,
            self.session(),
            ordinal,
            item,
        )

    def test_root_exports_are_exact_public_objects(self) -> None:
        self.require_contract()
        expected = {
            "READ_COLLECTION_SPECS",
            "DelegationKeyReadCursor",
            "IdentityReadCursor",
            "OrdinalReadCursor",
            "PlanReadScope",
            "ReadCollection",
            "ReadOrder",
            "ReadPage",
            "ReadPageCandidate",
            "ReadPageError",
            "ReadPageRequest",
            "RunReadScope",
            "SessionReadScope",
            "TemporalReadCursor",
            "WorkspaceReadScope",
            "read_collection_spec",
            "read_cursor_from_mapping",
        }
        self.assertTrue(expected.issubset(set(operations.__all__)))
        for name in expected:
            self.assertIsNotNone(getattr(operations, name))

    def test_literal_collection_table_is_source_pinned(self) -> None:
        self.require_contract()
        rows = (
            ("ACTIVITY_SESSIONS", "activity-sessions", "read.activity", WorkspaceReadScope, TemporalReadCursor, ReadOrder.ASCENDING, ("created_at", "session_id")),
            ("OPEN_SESSIONS", "open-sessions", "read.sessions", WorkspaceReadScope, TemporalReadCursor, ReadOrder.ASCENDING, ("created_at", "session_id")),
            ("SESSION_ACTIONS", "session-actions", "read.session-actions", SessionReadScope, OrdinalReadCursor, ReadOrder.ASCENDING, ("ordinal", "action_id")),
            ("SESSION_PLANS", "session-plans", "read.session-plans", SessionReadScope, TemporalReadCursor, ReadOrder.ASCENDING, ("created_at", "plan_id")),
            ("SESSION_APPROVALS", "session-approvals", "read.session-approvals", SessionReadScope, TemporalReadCursor, ReadOrder.ASCENDING, ("requested_at", "request_id")),
            ("PENDING_APPROVALS", "pending-approvals", "read.pending-approvals", WorkspaceReadScope, TemporalReadCursor, ReadOrder.ASCENDING, ("requested_at", "request_id")),
            ("PLAN_RUNS", "plan-runs", "read.plan-runs", PlanReadScope, TemporalReadCursor, ReadOrder.ASCENDING, ("created_at", "run_id")),
            ("RUN_EVENTS", "run-events", "read.run-events", RunReadScope, OrdinalReadCursor, ReadOrder.ASCENDING, ("ordinal", "event_id")),
            ("LATEST_OBSERVATIONS", "latest-observations", "read.observed-state", WorkspaceReadScope, IdentityReadCursor, ReadOrder.ASCENDING, ("subject_id",)),
            ("RUNTIME_AUTHORITIES", "runtime-authorities", "read.runtime-authorities", WorkspaceReadScope, IdentityReadCursor, ReadOrder.ASCENDING, ("authority_ref",)),
            ("RUNTIME_AUTHORITY_DELIVERIES", "runtime-authority-deliveries", "read.runtime-authority-deliveries", WorkspaceReadScope, IdentityReadCursor, ReadOrder.ASCENDING, ("authority_ref",)),
            ("INGRESS_AUTHORITIES", "ingress-authorities", "read.ingress-authorities", WorkspaceReadScope, IdentityReadCursor, ReadOrder.ASCENDING, ("authority_ref",)),
            ("SECRET_PROVIDERS", "secret-providers", "read.secret-providers", WorkspaceReadScope, IdentityReadCursor, ReadOrder.ASCENDING, ("provider_id",)),
            ("SECRET_REFERENCES", "secret-references", "read.secret-references", WorkspaceReadScope, IdentityReadCursor, ReadOrder.ASCENDING, ("registration_id",)),
            ("DELEGATION_SIGNING_KEYS", "delegation-signing-keys", "read.delegation-keys", WorkspaceReadScope, DelegationKeyReadCursor, ReadOrder.ASCENDING, ("purpose", "issuer", "key_id")),
            ("GATEWAY_PROBES", "gateway-probes", "read.gateway-probe-timeline", WorkspaceReadScope, TemporalReadCursor, ReadOrder.DESCENDING, ("issued_at", "probe_id")),
        )
        self.assertEqual(len(READ_COLLECTION_SPECS), 16)
        self.assertEqual(len(ReadCollection), 16)
        self.assertEqual(
            len({spec.route_id for spec in READ_COLLECTION_SPECS}),
            16,
        )
        for name, wire, route, scope, cursor, order, position in rows:
            collection = getattr(ReadCollection, name)
            spec = read_collection_spec(collection)
            self.assertEqual(collection.value, wire)
            self.assertEqual(spec.collection, collection)
            self.assertEqual(spec.route_id, route)
            self.assertIs(spec.scope_type, scope)
            self.assertIs(spec.cursor_type, cursor)
            self.assertEqual(spec.order, order)
            self.assertEqual(spec.position_fields, position)
        self.assertEqual(
            {spec.collection for spec in READ_COLLECTION_SPECS},
            set(ReadCollection),
        )

    def test_cursor_variants_have_exact_parsed_mapping_profiles(self) -> None:
        self.require_contract()
        values = (
            self.action_cursor(),
            TemporalReadCursor(
                ReadCollection.ACTIVITY_SESSIONS,
                self.workspace(),
                _INSTANT,
                "session-a",
            ),
            IdentityReadCursor(
                ReadCollection.SECRET_REFERENCES,
                self.workspace(),
                "registration-a",
            ),
            DelegationKeyReadCursor(
                ReadCollection.DELEGATION_SIGNING_KEYS,
                self.workspace(),
                DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
                "issuer-a",
                "key-a",
            ),
        )
        expected = (
            ("session-actions", {"ordinal": 1, "item_id": "action-a"}),
            ("activity-sessions", {"instant": _INSTANT, "item_id": "session-a"}),
            ("secret-references", {"item_id": "registration-a"}),
            ("delegation-signing-keys", {
                "purpose": "gateway-node-control-transit",
                "issuer": "issuer-a",
                "key_id": "key-a",
            }),
        )
        for value, (wire, position) in zip(values, expected, strict=True):
            descriptor = value.descriptor()
            self.assertEqual(
                set(descriptor),
                {"format_version", "collection", "scope", "position"},
            )
            self.assertEqual(descriptor["format_version"], 1)
            self.assertEqual(descriptor["collection"], wire)
            self.assertEqual(descriptor["position"], position)
            self.assertEqual(read_cursor_from_mapping(descriptor), value)
            self.assertNotIn("direction", descriptor)
            self.assertNotIn("secret://", repr(descriptor))

    def test_scopes_have_exact_keys_and_cursor_profiles_do_not_substitute(self) -> None:
        self.require_contract()
        cursors = (
            TemporalReadCursor(
                ReadCollection.ACTIVITY_SESSIONS,
                self.workspace(),
                _INSTANT,
                "session-a",
            ),
            self.action_cursor(),
            TemporalReadCursor(
                ReadCollection.PLAN_RUNS,
                self.plan(),
                _INSTANT,
                "run-a",
            ),
            OrdinalReadCursor(
                ReadCollection.RUN_EVENTS,
                self.run_scope(),
                1,
                "event-a",
            ),
        )
        expected_scopes = (
            {"workspace_id": "workspace-a"},
            {"workspace_id": "workspace-a", "session_id": "session-a"},
            {"workspace_id": "workspace-a", "plan_id": "plan-a"},
            {"workspace_id": "workspace-a", "run_id": "run-a"},
        )
        for cursor, scope in zip(cursors, expected_scopes, strict=True):
            self.assertEqual(cursor.descriptor()["scope"], scope)

        invalid = (
            lambda: TemporalReadCursor(
                ReadCollection.SESSION_ACTIONS,
                self.session(),
                _INSTANT,
                "action-a",
            ),
            lambda: OrdinalReadCursor(
                ReadCollection.RUN_EVENTS,
                self.session(),
                1,
                "event-a",
            ),
            lambda: IdentityReadCursor(
                ReadCollection.RUNTIME_AUTHORITIES,
                self.plan(),
                "runtime-a",
            ),
        )
        for build in invalid:
            with self.subTest(build=build):
                self.assert_bounded_error(build)

    def test_mapping_structure_and_version_fail_before_member_interpretation(self) -> None:
        self.require_contract()
        hostile = _HostileMapping()
        self.assert_bounded_error(lambda: read_cursor_from_mapping(hostile))
        self.assertFalse(hostile.touched)

        valid = self.action_cursor().descriptor()
        malformed = (
            MappingProxyType(valid),
            {**valid, "unknown": "candidate-do-not-retain"},
            {key: value for key, value in valid.items() if key != "position"},
            {**valid, "format_version": True},
            {**valid, "format_version": "1"},
            {**valid, "format_version": 0},
            {**valid, "format_version": 2},
            {**valid, "collection": "not-a-collection"},
            {**valid, "scope": MappingProxyType(valid["scope"])},
            {**valid, "position": MappingProxyType(valid["position"])},
        )
        for value in malformed:
            with self.subTest(value=type(value).__name__):
                self.assert_bounded_error(
                    lambda value=value: read_cursor_from_mapping(value),
                    forbidden="candidate-do-not-retain",
                )

        variants = (
            TemporalReadCursor(
                ReadCollection.ACTIVITY_SESSIONS,
                self.workspace(),
                _INSTANT,
                "session-a",
            ),
            self.action_cursor(),
            IdentityReadCursor(
                ReadCollection.SECRET_REFERENCES,
                self.workspace(),
                "registration-a",
            ),
            DelegationKeyReadCursor(
                ReadCollection.DELEGATION_SIGNING_KEYS,
                self.workspace(),
                DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
                "issuer-a",
                "key-a",
            ),
        )
        for cursor in variants:
            descriptor = cursor.descriptor()
            for member in ("scope", "position"):
                nested = descriptor[member]
                missing_key = next(iter(nested))
                invalid_nested = (
                    {key: value for key, value in nested.items() if key != missing_key},
                    {**nested, "unknown": "candidate-do-not-retain"},
                )
                for candidate in invalid_nested:
                    malformed_descriptor = {**descriptor, member: candidate}
                    with self.subTest(
                        collection=descriptor["collection"],
                        member=member,
                        keys=tuple(candidate),
                    ):
                        self.assert_bounded_error(
                            lambda value=malformed_descriptor: (
                                read_cursor_from_mapping(value)
                            ),
                            forbidden="candidate-do-not-retain",
                        )

                hostile = _HostileText("candidate-do-not-retain")
                text_key = "workspace_id" if member == "scope" else next(
                    key for key in nested if key in {"item_id", "issuer"}
                )
                malformed_descriptor = {
                    **descriptor,
                    member: {
                        **nested,
                        text_key: hostile,
                        "unknown": "extra",
                    },
                }
                self.assert_bounded_error(
                    lambda value=malformed_descriptor: read_cursor_from_mapping(value),
                    forbidden="candidate-do-not-retain",
                )
                self.assertFalse(hostile.touched)

    def test_identifier_and_position_boundaries_are_exact(self) -> None:
        self.require_contract()
        accepted = "a" * 512
        TemporalReadCursor(
            ReadCollection.ACTIVITY_SESSIONS,
            WorkspaceReadScope(accepted),
            _INSTANT,
            accepted,
        )
        IdentityReadCursor(
            ReadCollection.SECRET_REFERENCES,
            self.workspace(),
            accepted,
        )
        self.action_cursor(item=accepted)
        DelegationKeyReadCursor(
            ReadCollection.DELEGATION_SIGNING_KEYS,
            self.workspace(),
            DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
            "i" * 128,
            "k" * 128,
        )
        for scope_type, parent_field in (
            (WorkspaceReadScope, None),
            (SessionReadScope, "session"),
            (PlanReadScope, "plan"),
            (RunReadScope, "run"),
        ):
            arguments = [accepted]
            if parent_field is not None:
                arguments.append(accepted)
            scope_type(*arguments)
        for build in (
            lambda: WorkspaceReadScope("a" * 513),
            lambda: SessionReadScope("workspace-a", "s" * 513),
            lambda: PlanReadScope("workspace-a", "p" * 513),
            lambda: RunReadScope("workspace-a", "r" * 513),
            lambda: WorkspaceReadScope("line\nbreak"),
            lambda: TemporalReadCursor(
                ReadCollection.ACTIVITY_SESSIONS,
                self.workspace(),
                _INSTANT,
                "a" * 513,
            ),
            lambda: self.action_cursor(item="a" * 513),
            lambda: IdentityReadCursor(
                ReadCollection.SECRET_REFERENCES,
                self.workspace(),
                "a" * 513,
            ),
            lambda: DelegationKeyReadCursor(
                ReadCollection.DELEGATION_SIGNING_KEYS,
                self.workspace(),
                DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
                "i" * 129,
                "key-a",
            ),
            lambda: DelegationKeyReadCursor(
                ReadCollection.DELEGATION_SIGNING_KEYS,
                self.workspace(),
                DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
                "issuer-a",
                "k" * 129,
            ),
            lambda: DelegationKeyReadCursor(
                ReadCollection.DELEGATION_SIGNING_KEYS,
                self.workspace(),
                "not-a-purpose",
                "issuer-a",
                "key-a",
            ),
            lambda: DelegationKeyReadCursor(
                ReadCollection.DELEGATION_SIGNING_KEYS,
                self.workspace(),
                True,
                "issuer-a",
                "key-a",
            ),
            lambda: DelegationKeyReadCursor(
                ReadCollection.DELEGATION_SIGNING_KEYS,
                self.workspace(),
                DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
                "issuer-a",
                "K-not-lowercase",
            ),
            lambda: self.action_cursor(ordinal=True),
            lambda: self.action_cursor(ordinal=0),
        ):
            with self.subTest(build=build):
                self.assert_bounded_error(build)

    def test_temporal_cursor_requires_canonical_microsecond_utc_text(self) -> None:
        self.require_contract()
        TemporalReadCursor(
            ReadCollection.ACTIVITY_SESSIONS,
            self.workspace(),
            _INSTANT,
            "session-a",
        )
        for instant in (
            "2026-08-12T12:34:56Z",
            "2026-08-12T12:34:56.12345Z",
            "2026-08-12T12:34:56.123456+00:00",
            "2026-08-12 12:34:56.123456Z",
            "not-a-time",
            "x" * 28,
        ):
            with self.subTest(instant=instant):
                self.assert_bounded_error(
                    lambda instant=instant: TemporalReadCursor(
                        ReadCollection.ACTIVITY_SESSIONS,
                        self.workspace(),
                        instant,
                        "session-a",
                    )
                )

    def test_request_limit_and_cursor_scope_are_exact(self) -> None:
        self.require_contract()
        for limit in (1, 100):
            request = ReadPageRequest(
                ReadCollection.SESSION_ACTIONS,
                self.session(),
                limit,
            )
            self.assertEqual(request.limit, limit)
        for limit in (0, 101, True, 1.0, "1"):
            with self.subTest(limit=limit):
                self.assert_bounded_error(
                    lambda limit=limit: ReadPageRequest(
                        ReadCollection.SESSION_ACTIONS,
                        self.session(),
                        limit,
                    )
                )
        wrong = OrdinalReadCursor(
            ReadCollection.SESSION_ACTIONS,
            SessionReadScope("workspace-b", "session-a"),
            1,
            "action-a",
        )
        self.assert_bounded_error(
            lambda: ReadPageRequest(
                ReadCollection.SESSION_ACTIONS,
                self.session(),
                10,
                wrong,
            )
        )
        wrong_collection = TemporalReadCursor(
            ReadCollection.OPEN_SESSIONS,
            self.workspace(),
            _INSTANT,
            "session-a",
        )
        self.assert_bounded_error(
            lambda: ReadPageRequest(
                ReadCollection.ACTIVITY_SESSIONS,
                self.workspace(),
                10,
                wrong_collection,
            )
        )

    def test_exposed_and_hidden_candidate_coordinates_match_request(self) -> None:
        self.require_contract()
        request = ReadPageRequest(
            ReadCollection.ACTIVITY_SESSIONS,
            self.workspace(),
            1,
        )
        matching = TemporalReadCursor(
            ReadCollection.ACTIVITY_SESSIONS,
            self.workspace(),
            _INSTANT,
            "session-a",
        )
        wrong_collection = TemporalReadCursor(
            ReadCollection.OPEN_SESSIONS,
            self.workspace(),
            _INSTANT,
            "session-b",
        )
        wrong_scope = TemporalReadCursor(
            ReadCollection.ACTIVITY_SESSIONS,
            WorkspaceReadScope("workspace-b"),
            _INSTANT,
            "session-b",
        )
        good = ReadPageCandidate({"session_id": "session-a"}, matching)
        for candidates in (
            (ReadPageCandidate({"session_id": "session-b"}, wrong_collection),),
            (ReadPageCandidate({"session_id": "session-b"}, wrong_scope),),
            (good, ReadPageCandidate({"session_id": "session-b"}, wrong_collection)),
            (good, ReadPageCandidate({"session_id": "session-b"}, wrong_scope)),
        ):
            with self.subTest(cursors=tuple(value.cursor_after_item for value in candidates)):
                self.assert_bounded_error(
                    lambda values=candidates: ReadPage.from_candidates(request, values)
                )

    def test_lookahead_cursor_is_last_exposed_not_hidden(self) -> None:
        self.require_contract()
        request = ReadPageRequest(
            ReadCollection.SESSION_ACTIONS,
            self.session(),
            1,
        )
        first = self.action_cursor("action-a", 1)
        hidden = self.action_cursor("action-b", 2)
        page = ReadPage.from_candidates(
            request,
            (
                ReadPageCandidate({"action_id": "action-a"}, first),
                ReadPageCandidate({"action_id": "action-b"}, hidden),
            ),
        )
        self.assertEqual(page.items, ({"action_id": "action-a"},))
        self.assertEqual(page.next_cursor, first)
        descriptor = page.descriptor()
        self.assertEqual(
            descriptor,
            {
                "workspace_id": "workspace-a",
                "kind": "session-actions",
                "limit": 1,
                "items": [{"action_id": "action-a"}],
                "next_cursor": first.descriptor(),
            },
        )
        self.assertNotIn("action-b", repr(descriptor))
        self.assertNotIn(repr(hidden.descriptor()), repr(descriptor))
        self.assertGreater(hidden.ordinal, page.next_cursor.ordinal)

    def test_page_boundaries_and_candidate_overflow_are_total(self) -> None:
        self.require_contract()
        request = ReadPageRequest(
            ReadCollection.SESSION_ACTIONS,
            self.session(),
            2,
        )
        candidates = tuple(
            ReadPageCandidate(
                {"action_id": f"action-{ordinal}"},
                self.action_cursor(f"action-{ordinal}", ordinal),
            )
            for ordinal in (1, 2, 3)
        )
        for count in (0, 1, 2):
            page = ReadPage.from_candidates(request, candidates[:count])
            self.assertEqual(len(page.items), count)
            self.assertIsNone(page.next_cursor)
        page = ReadPage.from_candidates(request, candidates)
        self.assertEqual(len(page.items), 2)
        self.assertEqual(page.next_cursor, candidates[1].cursor_after_item)
        overflow = (*candidates, ReadPageCandidate(
            {"action_id": "action-4"},
            self.action_cursor("action-4", 4),
        ))
        self.assert_bounded_error(
            lambda: ReadPage.from_candidates(request, overflow)
        )

        activity_request = ReadPageRequest(
            ReadCollection.ACTIVITY_SESSIONS,
            self.workspace(),
            2,
        )
        activity_cursor = TemporalReadCursor(
            ReadCollection.ACTIVITY_SESSIONS,
            self.workspace(),
            _INSTANT,
            "session-a",
        )
        activity_page = ReadPage.from_candidates(
            activity_request,
            (ReadPageCandidate({"session_id": "session-a"}, activity_cursor),),
        )
        self.assertEqual(
            activity_page.descriptor(),
            {
                "workspace_id": "workspace-a",
                "kind": "activity-sessions",
                "limit": 2,
                "items": [{"session_id": "session-a"}],
                "next_cursor": None,
            },
        )

    def test_page_map_preserves_order_and_already_derived_cursor(self) -> None:
        self.require_contract()
        request = ReadPageRequest(
            ReadCollection.SESSION_ACTIONS,
            self.session(),
            1,
        )
        cursor = self.action_cursor()
        page = ReadPage.from_candidates(
            request,
            (
                ReadPageCandidate(2, cursor),
                ReadPageCandidate(3, self.action_cursor("action-b", 2)),
            ),
        )
        identity = page.map(lambda value: value)
        composed = page.map(lambda value: value + 1).map(lambda value: value * 2)
        direct = page.map(lambda value: (value + 1) * 2)
        self.assertEqual(identity, page)
        self.assertEqual(composed, direct)
        self.assertIs(composed.next_cursor, page.next_cursor)

        class MapperFailure(RuntimeError):
            pass

        failure = MapperFailure("caller mapper failed")

        def fail_mapper(_value):
            raise failure

        with self.assertRaises(MapperFailure) as caught:
            page.map(fail_mapper)
        self.assertIs(caught.exception, failure)

    def test_public_descriptor_requires_exact_dict_snapshots(self) -> None:
        self.require_contract()
        request = ReadPageRequest(
            ReadCollection.SESSION_ACTIONS,
            self.session(),
            1,
        )
        for item in (MappingProxyType({"action_id": "action-a"}), object(), "text"):
            page = ReadPage.from_candidates(
                request,
                (ReadPageCandidate(item, self.action_cursor()),),
            )
            with self.subTest(item=type(item).__name__):
                self.assert_bounded_error(page.descriptor)

    def test_module_has_no_effect_or_generic_framework_imports(self) -> None:
        self.require_contract()
        source_path = (
            Path(__file__).parents[1]
            / "src"
            / "control_plane_kit_operations"
            / "read_pages.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertTrue(
            imported.isdisjoint(
                {
                    "fastapi",
                    "httpx",
                    "psycopg",
                    "sqlite3",
                    "control_plane_kit_interpreters",
                    "control_plane_kit_server_sdk",
                }
            )
        )
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertTrue(names.isdisjoint({"open", "eval", "exec", "__import__"}))

    def assert_bounded_error(self, call, *, forbidden: str | None = None) -> None:
        self.require_contract()
        with self.assertRaises(ReadPageError) as caught:
            call()
        message = str(caught.exception)
        self.assertLessEqual(len(message), 160)
        if forbidden is not None:
            self.assertNotIn(forbidden, message)
            self.assertNotIn(forbidden, repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)


if __name__ == "__main__":
    unittest.main()
