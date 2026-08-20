from __future__ import annotations

from dataclasses import fields, replace
import inspect
import json
import os
from pathlib import Path
import unittest
from unittest import mock

import rfc8785

import control_plane_kit_architecture_testing as architecture_testing
import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations import (
    ActivityEventKind,
    EffectAttemptIdentity,
    RunId,
)
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectIntent,
    RuntimeEffectIntentSource,
    runtime_effect_intent_fingerprint,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectContractError
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    OperationsRecordError,
)

from tests.effect_attempt_intent_fixture import (
    ClassAccessHostileBytes,
    EVENT_ID,
    EffectAttemptIntentFixture,
    EffectAttemptIntentRecord,
    INTENT_ERROR,
    INTENT_MAX_BYTES,
    INTENT_MODULE,
    INTENT_SOURCE_PATH,
    _decode_runtime_effect_intent,
    _encode_runtime_effect_intent,
    _load_optional,
    class_access_hostile_copy,
    deep_coordinate_intent_candidates,
    forge_exact,
    product_material,
    subclass_copy,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = Path(
    os.environ.get(
        "CPK_PACKAGE_MODULE_INVENTORY",
        REPOSITORY_ROOT / "docs" / "architecture" / "package-module-inventory.json",
    )
)
ROOT_EXPORTS = {"EffectAttemptIntentRecord"}

EXACT_IMPORT_SURFACE = (
    architecture_testing.ImportSurfaceEntry("__future__", "annotations", None),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations",
        "ActivityEventKind",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations",
        "EffectAttemptIdentity",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.operations",
        "RunId",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.planning",
        "ActivityId",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.planning",
        "activity_operation_from_descriptor",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_authority",
        "RuntimeAuthorityAccessDeliveryCodec",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_authority",
        "RuntimeAuthorityReferenceCodec",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "RuntimeEffectIntent",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "RuntimeEffectIntentSource",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "runtime_effect_intent_fingerprint",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "runtime_effect_intent_for_request",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effect_observation",
        "runtime_effect_request_for_intent",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effects",
        "RuntimeEffectContractError",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effects",
        "RuntimeEffectKind",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.runtime_effects",
        "RuntimeProductMaterial",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_core.types",
        "RuntimeKind",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.records",
        "ActivityEventRecord",
        None,
    ),
    architecture_testing.ImportSurfaceEntry(
        "control_plane_kit_operations.records",
        "OperationsRecordError",
        None,
    ),
    architecture_testing.ImportSurfaceEntry("dataclasses", "dataclass", None),
    architecture_testing.ImportSurfaceEntry("dataclasses", "field", None),
    architecture_testing.ImportSurfaceEntry("json", None, None),
    architecture_testing.ImportSurfaceEntry("rfc8785", None, None),
)

EXACT_CALL_SURFACE = (
    architecture_testing.ResolvedCallTarget("_canonical_runtime_effect_intent"),
    architecture_testing.ResolvedCallTarget("_canonical_runtime_effect_intent"),
    architecture_testing.ResolvedCallTarget("_decode_runtime_effect_intent"),
    architecture_testing.ResolvedCallTarget("_encode_runtime_effect_intent"),
    architecture_testing.ResolvedCallTarget("_raise_intent_error"),
    architecture_testing.ResolvedCallTarget("_raise_intent_error"),
    architecture_testing.ResolvedCallTarget("_raise_intent_error"),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.operations.EffectAttemptIdentity"
    ),
    architecture_testing.ResolvedCallTarget("control_plane_kit_core.operations.RunId"),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.planning.ActivityId"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.planning.activity_operation_from_descriptor"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_authority.RuntimeAuthorityAccessDeliveryCodec"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_authority."
        "RuntimeAuthorityAccessDeliveryCodec.decode"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_authority.RuntimeAuthorityReferenceCodec"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_authority.RuntimeAuthorityReferenceCodec.decode"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_effect_observation.RuntimeEffectIntent"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_effect_observation."
        "RuntimeEffectIntent.descriptor"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_effect_observation.RuntimeEffectIntentSource"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_effect_observation.runtime_effect_intent_fingerprint"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_effect_observation.runtime_effect_intent_for_request"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_effect_observation.runtime_effect_request_for_intent"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_effects.RuntimeEffectKind"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.runtime_effects.RuntimeProductMaterial.from_descriptor"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_core.types.RuntimeKind"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_operations.records.ActivityEventRecord"
    ),
    architecture_testing.ResolvedCallTarget(
        "control_plane_kit_operations.records.OperationsRecordError"
    ),
    architecture_testing.ResolvedCallTarget("dataclasses.dataclass"),
    architecture_testing.ResolvedCallTarget("dataclasses.field"),
    architecture_testing.ResolvedCallTarget("dataclasses.field"),
    architecture_testing.ResolvedCallTarget("json.loads"),
    architecture_testing.ResolvedCallTarget("len"),
    architecture_testing.ResolvedCallTarget("len"),
    architecture_testing.ResolvedCallTarget("rfc8785.dumps"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
    architecture_testing.ResolvedCallTarget("type"),
)


class HostileText(str):
    pass


class DispatchText(str):
    dispatches: list[str] = []

    def __hash__(self):
        type(self).dispatches.append("hash")
        raise AssertionError("hostile hash dispatched")

    def __eq__(self, _other):
        type(self).dispatches.append("eq")
        raise AssertionError("hostile equality dispatched")

    def strip(self, *_args, **_kwargs):
        type(self).dispatches.append("strip")
        raise AssertionError("hostile strip dispatched")

    def encode(self, *_args, **_kwargs):
        type(self).dispatches.append("encode")
        raise AssertionError("hostile encode dispatched")


class EffectAttemptIntentUngatedControls(
    EffectAttemptIntentFixture,
    unittest.TestCase,
):
    def test_core_component_inverse_and_request_round_trip_are_total(self) -> None:
        for compensation in (False, True):
            with self.subTest(compensation=compensation):
                intent = self.intent(compensation=compensation)
                self.assertEqual(self.public_round_trip(intent), intent)
                self.assertEqual(
                    runtime_effect_intent_fingerprint(self.public_round_trip(intent)),
                    runtime_effect_intent_fingerprint(intent),
                )
                expected_kind = (
                    ActivityEventKind.STEP_COMPENSATION_STARTED
                    if compensation
                    else ActivityEventKind.STEP_STARTED
                )
                self.assertIs(
                    self.original_event(intent, compensation=compensation).kind,
                    expected_kind,
                )

    def test_core_descriptor_is_exact_protected_preimage_without_generated_data(
        self,
    ) -> None:
        intent = self.intent()
        document = self.canonical_bytes(intent)
        self.assertEqual(document, rfc8785.dumps(intent.descriptor()))
        for admitted in (
            b"workspace-a",
            b"graph-base",
            b"graph-desired",
            b"http://upstream.internal:8080",
            b"secret://local/workspace-a/app/token",
            b"secret://local/workspace-a/postgres/password",
            b"secret://local/workspace-a/oci/pull",
        ):
            self.assertIn(admitted, document)
        for excluded in (
            EVENT_ID.encode(),
            b"authorization-id-canary",
            b"resolved-secret-value-canary",
            b"secret_resolution_grants",
        ):
            self.assertNotIn(excluded, document)

    def test_largest_realizable_lawful_intent_stays_inside_transport_ceiling(
        self,
    ) -> None:
        intent = self.largest_lawful_intent()
        size = len(self.canonical_bytes(intent))
        self.assertGreater(size, INTENT_MAX_BYTES // 2)
        self.assertLessEqual(size, INTENT_MAX_BYTES)
        self.assertEqual(self.public_round_trip(intent), intent)
        self.assertEqual(len(runtime_effect_intent_fingerprint(intent)), 64)

    def test_forged_sequence_intent_can_hash_but_not_survive_public_round_trip(
        self,
    ) -> None:
        lawful = self.intent()
        forged = forge_exact(
            RuntimeEffectIntent,
            kind=lawful.kind,
            runtime_kind=lawful.runtime_kind,
            source=lawful.source,
            activity_id=lawful.activity_id,
            operation=lawful.operation,
            authority_ref=lawful.authority_ref,
            authority_deliveries=list(lawful.authority_deliveries),
            products=lawful.products,
        )
        self.assertEqual(
            runtime_effect_intent_fingerprint(forged),
            runtime_effect_intent_fingerprint(lawful),
        )
        self.assertNotEqual(self.public_round_trip(forged), forged)

    def test_shared_architecture_policy_has_an_independent_valid_world(self) -> None:
        path = "tests/shared_intent_policy_canary.py"
        module = "shared_intent_policy_canary"
        facts = architecture_testing.analyze_source(
            "from sample.intent import decode as decode_intent\n"
            "decode_intent()\n",
            path=path,
            module=module,
        )
        findings = architecture_testing.evaluate_policies(
            (facts,),
            (
                architecture_testing.ExactImportSurfacePolicy(
                    architecture_testing.PolicyId("cpk.canary.intent-imports"),
                    architecture_testing.RuleId("exact"),
                    path,
                    module,
                    (
                        architecture_testing.ImportSurfaceEntry(
                            "sample.intent", "decode", "decode_intent"
                        ),
                    ),
                    "intent canary imports differ",
                ),
                architecture_testing.ExactCallSurfacePolicy(
                    architecture_testing.PolicyId("cpk.canary.intent-calls"),
                    architecture_testing.RuleId("exact"),
                    path,
                    module,
                    (
                        architecture_testing.ResolvedCallTarget(
                            "sample.intent.decode"
                        ),
                    ),
                    "intent canary calls differ",
                ),
            ),
        )
        self.assertEqual(findings, ())


class EffectAttemptIntentContractTests(
    EffectAttemptIntentFixture,
    unittest.TestCase,
):
    def test_missing_module_guard_preserves_nested_import_failures(self) -> None:
        nested = ModuleNotFoundError("nested dependency missing")
        nested.name = "nested_dependency"

        def missing_nested(_name):
            raise nested

        with self.assertRaises(ModuleNotFoundError) as caught:
            _load_optional(INTENT_MODULE, missing_nested)
        self.assertIs(caught.exception, nested)

    def test_record_is_exact_frozen_root_owned_and_repr_protected(self) -> None:
        self.require_intent_language()
        record = self.record()
        self.assertIs(operations_root.EffectAttemptIntentRecord, EffectAttemptIntentRecord)
        self.assertEqual(EffectAttemptIntentRecord.__module__, INTENT_MODULE)
        self.assertTrue(EffectAttemptIntentRecord.__dataclass_params__.frozen)
        self.assertEqual(
            tuple((item.name, item.repr) for item in fields(EffectAttemptIntentRecord)),
            (
                ("identity", True),
                ("original_start_event", False),
                ("intent", False),
            ),
        )
        self.assertEqual(record.workspace_id, "workspace-a")
        self.assertEqual(record.request_id, "request-a")
        self.assertEqual(
            record.request_fingerprint,
            runtime_effect_intent_fingerprint(record.intent),
        )
        rendered = f"{record!s} {record!r}"
        for canary in (
            EVENT_ID,
            "graph-base",
            "http://upstream.internal:8080",
            "secret://local/workspace-a/app/token",
        ):
            self.assertNotIn(canary, rendered)

    def test_record_accepts_exact_forward_and_compensation_worlds(self) -> None:
        self.require_intent_language()
        for compensation in (False, True):
            with self.subTest(compensation=compensation):
                record = self.record(compensation=compensation)
                self.assertEqual(record.identity, self.identity())
                self.assertEqual(self.public_round_trip(record.intent), record.intent)
                self.assertIs(
                    record.original_start_event.kind,
                    ActivityEventKind.STEP_COMPENSATION_STARTED
                    if compensation
                    else ActivityEventKind.STEP_STARTED,
                )

    def test_record_rejects_outer_hostility_and_exact_deep_forgeries(self) -> None:
        self.require_intent_language()
        intent = self.intent()
        event = self.original_event(intent)
        identity = self.identity()

        class HostileRecord(EffectAttemptIntentRecord):
            pass

        hostile_identity = subclass_copy(identity)
        hostile_event = subclass_copy(event)
        hostile_intent = subclass_copy(intent)
        DispatchText.dispatches = []
        forged_run = forge_exact(RunId, value=DispatchText("run-hostile-canary"))
        forged_identity = forge_exact(
            EffectAttemptIdentity,
            run_id=forged_run,
            activity_id=identity.activity_id,
            attempt=identity.attempt,
        )
        forged_source = forge_exact(
            RuntimeEffectIntentSource,
            workspace_id=intent.source.workspace_id,
            request_id=intent.source.request_id,
            run_id=forged_run,
            plan_id=intent.source.plan_id,
            base_graph_id=intent.source.base_graph_id,
            desired_graph_id=intent.source.desired_graph_id,
        )
        forged_intent = forge_exact(
            RuntimeEffectIntent,
            kind=intent.kind,
            runtime_kind=intent.runtime_kind,
            source=forged_source,
            activity_id=intent.activity_id,
            operation=intent.operation,
            authority_ref=intent.authority_ref,
            authority_deliveries=intent.authority_deliveries,
            products=intent.products,
        )
        cases = (
            lambda: HostileRecord(identity, event, intent),
            lambda: EffectAttemptIntentRecord(hostile_identity, event, intent),
            lambda: EffectAttemptIntentRecord(identity, hostile_event, intent),
            lambda: EffectAttemptIntentRecord(identity, event, hostile_intent),
            lambda: EffectAttemptIntentRecord(forged_identity, event, intent),
            lambda: EffectAttemptIntentRecord(identity, event, forged_intent),
        )
        for construct in cases:
            with self.subTest(construct=construct):
                self.assert_intent_error(construct, "run-hostile-canary")
                self.assertEqual(DispatchText.dispatches, [])

    def test_record_and_codec_reject_class_access_and_hash_only_forgeries(
        self,
    ) -> None:
        self.require_intent_language()
        lawful = self.intent()
        event = self.original_event(lawful)
        identity = self.identity()
        dispatches: list[str] = []
        hostile_intent = class_access_hostile_copy(lawful, dispatches)
        lawful_record = self.record(intent=lawful)
        hostile_record = class_access_hostile_copy(lawful_record, dispatches)
        forged_sequence = forge_exact(
            RuntimeEffectIntent,
            kind=lawful.kind,
            runtime_kind=lawful.runtime_kind,
            source=lawful.source,
            activity_id=lawful.activity_id,
            operation=lawful.operation,
            authority_ref=lawful.authority_ref,
            authority_deliveries=lawful.authority_deliveries,
            products=list(lawful.products),
        )
        self.assertEqual(
            runtime_effect_intent_fingerprint(forged_sequence),
            runtime_effect_intent_fingerprint(lawful),
        )
        cases = (
            lambda: EffectAttemptIntentRecord(identity, event, hostile_intent),
            lambda: EffectAttemptIntentRecord(identity, event, forged_sequence),
            lambda: _encode_runtime_effect_intent(hostile_intent),
            lambda: _encode_runtime_effect_intent(forged_sequence),
            lambda: hostile_record.__post_init__(),
        )
        for construct in cases:
            with self.subTest(construct=construct):
                self.assert_intent_error(construct)
                self.assertEqual(dispatches, [])

        ClassAccessHostileBytes.dispatches = []
        document = ClassAccessHostileBytes(self.canonical_bytes(lawful))
        self.assert_intent_error(lambda: _decode_runtime_effect_intent(document))
        self.assertEqual(ClassAccessHostileBytes.dispatches, [])

        self.assertEqual(
            _decode_runtime_effect_intent(_encode_runtime_effect_intent(lawful)),
            lawful,
        )
        self.assertEqual(self.record(intent=lawful).intent, lawful)

        module = __import__(INTENT_MODULE, fromlist=("__file__",))
        original_projection = module.runtime_effect_request_for_intent
        for label, candidate, coordinate_dispatches in (
            deep_coordinate_intent_candidates(lawful)
        ):
            for boundary, construct in (
                (
                    "encode",
                    lambda candidate=candidate: _encode_runtime_effect_intent(
                        candidate
                    ),
                ),
                (
                    "record",
                    lambda candidate=candidate: EffectAttemptIntentRecord(
                        identity,
                        event,
                        candidate,
                    ),
                ),
            ):
                with self.subTest(candidate=label, boundary=boundary):
                    coordinate_dispatches.clear()
                    projections: list[str] = []

                    def forbidden_projection(*_args, **_kwargs):
                        projections.append("projection")
                        raise AssertionError("public request projection dispatched")

                    captured = None
                    module.runtime_effect_request_for_intent = forbidden_projection
                    try:
                        try:
                            construct()
                        except BaseException as error:
                            captured = error
                    finally:
                        module.runtime_effect_request_for_intent = original_projection
                    self.assertEqual(coordinate_dispatches, [])
                    self.assertEqual(projections, [])
                    self.assertIs(type(captured), OperationsRecordError)
                    self.assertEqual(str(captured), INTENT_ERROR)
                    self.assertIsNone(captured.__cause__)
                    self.assertIsNone(captured.__context__)
                    self.assertNotIn(label, f"{captured!s} {captured!r}")

    def test_record_rejects_every_identity_source_event_and_phase_cross_join(self) -> None:
        self.require_intent_language()
        intent = self.intent()
        event = self.original_event(intent)
        cases = (
            {"identity": self.identity(run_id="run-b")},
            {"identity": self.identity(activity_id="activity-b")},
            {"intent": replace(intent, source=replace(intent.source, run_id=RunId("run-b")))},
            {"intent": replace(intent, activity_id=type(intent.activity_id)("activity-b"))},
            {"original_start_event": replace(event, run_id="run-b")},
            {"original_start_event": replace(event, activity_id="activity-b")},
            {"original_start_event": replace(event, kind=ActivityEventKind.STEP_SUCCEEDED)},
            {
                "original_start_event": replace(
                    event, kind=ActivityEventKind.STEP_COMPENSATION_STARTED
                )
            },
        )
        for changes in cases:
            with self.subTest(changes=tuple(changes)):
                self.assert_intent_error(
                    lambda changes=changes: self.record(**changes),
                    "request-b",
                    "run-b",
                    "activity-b",
                )

    def test_original_event_identity_is_generated_coordinate_not_intent_identity(self) -> None:
        self.require_intent_language()
        intent = self.intent()
        first = self.record(
            intent=intent,
            original_start_event=self.original_event(intent, event_id="event-a"),
        )
        second = self.record(
            intent=intent,
            original_start_event=self.original_event(intent, event_id="event-b"),
        )
        self.assertNotEqual(first, second)
        self.assertEqual(first.intent, second.intent)
        self.assertEqual(first.request_fingerprint, second.request_fingerprint)

    def test_private_codec_preserves_exact_core_bytes_and_public_round_trip(self) -> None:
        self.require_intent_language()
        for compensation in (False, True):
            with self.subTest(compensation=compensation):
                intent = self.intent(compensation=compensation)
                document = _encode_runtime_effect_intent(intent)
                self.assertIs(type(document), bytes)
                self.assertEqual(document, self.canonical_bytes(intent))
                decoded = _decode_runtime_effect_intent(document)
                self.assertIs(type(decoded), RuntimeEffectIntent)
                self.assertEqual(decoded, intent)
                self.assertEqual(self.public_round_trip(decoded), intent)
                self.assertEqual(
                    runtime_effect_intent_fingerprint(decoded),
                    runtime_effect_intent_fingerprint(intent),
                )

    def test_private_codec_rejects_noncanonical_json_and_transport_bounds(self) -> None:
        self.require_intent_language()
        valid = self.canonical_bytes()
        duplicate = valid.replace(b"{", b'{"kind":"realize-activity",', 1)
        nonfinite = valid.replace(b'"contract_revision":1', b'"contract_revision":NaN', 1)
        malformed = (
            b"",
            b" " + valid,
            valid + b"\n",
            b"\xef\xbb\xbf" + valid,
            valid + b"{}",
            duplicate,
            nonfinite,
            b"[" * 1000 + b"0" + b"]" * 1000,
            b"x" * (INTENT_MAX_BYTES + 1),
            bytearray(valid),
            memoryview(valid),
        )
        for candidate in malformed:
            with self.subTest(kind=type(candidate).__name__, size=len(candidate)):
                self.assert_intent_error(
                    lambda candidate=candidate: _decode_runtime_effect_intent(candidate),
                    "realize-activity",
                    "contract_revision",
                )

    def test_private_codec_rejects_hostile_and_forged_components(self) -> None:
        self.require_intent_language()
        intent = self.intent()
        hostile = subclass_copy(intent)
        forged = forge_exact(
            RuntimeEffectIntent,
            kind=intent.kind,
            runtime_kind=intent.runtime_kind,
            source=forge_exact(
                RuntimeEffectIntentSource,
                workspace_id=HostileText("workspace-hostile-canary"),
                request_id=intent.source.request_id,
                run_id=intent.source.run_id,
                plan_id=intent.source.plan_id,
                base_graph_id=intent.source.base_graph_id,
                desired_graph_id=intent.source.desired_graph_id,
            ),
            activity_id=intent.activity_id,
            operation=intent.operation,
            authority_ref=intent.authority_ref,
            authority_deliveries=intent.authority_deliveries,
            products=intent.products,
        )
        for candidate in (hostile, forged):
            with self.subTest(candidate=type(candidate).__name__):
                self.assert_intent_error(
                    lambda candidate=candidate: _encode_runtime_effect_intent(candidate),
                    "workspace-hostile-canary",
                )

    def test_private_codec_translates_only_expected_input_faults(self) -> None:
        self.require_intent_language()
        valid = self.canonical_bytes()
        for name, injected in (
            ("loads", RuntimeError("json-internal-canary")),
            ("dumps", TypeError("canonical-internal-canary")),
        ):
            with self.subTest(name=name):
                target = (
                    "control_plane_kit_operations.effect_attempt_intent_evidence."
                    + ("json.loads" if name == "loads" else "rfc8785.dumps")
                )
                with mock.patch(target, side_effect=injected):
                    with self.assertRaises(type(injected)) as caught:
                        (
                            _decode_runtime_effect_intent(valid)
                            if name == "loads"
                            else _encode_runtime_effect_intent(self.intent())
                        )
                self.assertIs(caught.exception, injected)

    def test_largest_lawful_preimage_is_admitted_without_exact_max_overclaim(self) -> None:
        self.require_intent_language()
        intent = self.largest_lawful_intent()
        document = _encode_runtime_effect_intent(intent)
        self.assertGreater(len(document), INTENT_MAX_BYTES // 2)
        self.assertLessEqual(len(document), INTENT_MAX_BYTES)
        self.assertEqual(_decode_runtime_effect_intent(document), intent)

    def test_package_root_owns_only_the_public_record(self) -> None:
        self.assertEqual(ROOT_EXPORTS.difference(operations_root.__all__), set())
        self.require_intent_language()
        self.assertIs(operations_root.EffectAttemptIntentRecord, EffectAttemptIntentRecord)

    def test_package_root_does_not_publish_private_intent_codec(self) -> None:
        for name in (
            "_encode_runtime_effect_intent",
            "_decode_runtime_effect_intent",
        ):
            with self.subTest(name=name):
                self.assertNotIn(name, operations_root.__all__)
                self.assertFalse(hasattr(operations_root, name))

    def test_package_inventory_owns_the_stage_one_module_and_dependencies(self) -> None:
        inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        rows = [row for row in inventory["modules"] if row["module"] == INTENT_MODULE]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["owner"], "operation")
        self.assertEqual(row["destination"], INTENT_MODULE)
        self.assertEqual(set(row["canonical_public_exports"]), ROOT_EXPORTS)
        self.assertEqual(
            set(row["internal_dependencies"]),
            {
                "control_plane_kit_core.operations",
                "control_plane_kit_core.planning",
                "control_plane_kit_core.runtime_authority",
                "control_plane_kit_core.runtime_effect_observation",
                "control_plane_kit_core.runtime_effects",
                "control_plane_kit_core.types",
                "control_plane_kit_operations.records",
            },
        )
        self.assertEqual(row["optional_external_dependencies"], ["rfc8785"])
        self.assertIn("tests/test_effect_attempt_intent_contract.py", row["protecting_tests"])

    def test_module_has_closed_import_and_lexical_call_surface(self) -> None:
        self.require_intent_language()
        module = __import__(INTENT_MODULE, fromlist=("__file__",))
        source_path = Path(inspect.getsourcefile(module))
        facts = architecture_testing.analyze_source(
            source_path.read_text(encoding="utf-8"),
            path=INTENT_SOURCE_PATH,
            module=INTENT_MODULE,
        )
        findings = architecture_testing.evaluate_policies(
            (facts,),
            (
                architecture_testing.ExactImportSurfacePolicy(
                    architecture_testing.PolicyId("cpk.operations.intent.imports"),
                    architecture_testing.RuleId("exact"),
                    INTENT_SOURCE_PATH,
                    INTENT_MODULE,
                    EXACT_IMPORT_SURFACE,
                    "effect attempt intent import surface differs",
                ),
                architecture_testing.ExactCallSurfacePolicy(
                    architecture_testing.PolicyId("cpk.operations.intent.calls"),
                    architecture_testing.RuleId("exact"),
                    INTENT_SOURCE_PATH,
                    INTENT_MODULE,
                    EXACT_CALL_SURFACE,
                    "effect attempt intent lexical call surface differs",
                ),
            ),
        )
        self.assertEqual(findings, ())


if __name__ == "__main__":
    unittest.main()
