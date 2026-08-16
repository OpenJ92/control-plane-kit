from __future__ import annotations

import ast
from dataclasses import fields, replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import unittest

import rfc8785

import control_plane_kit_core as root
from control_plane_kit_core.operations.run_identity import RunId
from control_plane_kit_core.planning import ActivityId, NodeTarget, StartNode
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    LiteralEndpointMaterial,
    RuntimeEndpointObservation,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectFailure,
    RuntimeEffectKind,
    RuntimeEffectRequest,
    RuntimeEffectResult,
    RuntimeEffectSource,
)
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretReference,
    SecretResolutionGrant,
    SecretUseIntent,
)
from control_plane_kit_core.runtime_authority import RuntimeEffectContractError
from control_plane_kit_core.types import Protocol, RuntimeKind


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACKAGE_ROOT.parent
SOURCE_ROOT = PACKAGE_ROOT / "src" / "control_plane_kit_core"
MODULE = "control_plane_kit_core.runtime_effect_observation"
RESULT_DOMAIN = b"control-plane-kit.runtime-effect-result.v1\x00"
OUTCOME_FINGERPRINT_MAX_BYTES = 8_192
PUBLIC_NAMES = {
    "RuntimeEffectIntent",
    "RuntimeEffectIntentSource",
    "RuntimeEffectObservationEvidence",
    "RuntimeEffectObservationFailure",
    "RuntimeEffectObservationRequest",
    "RuntimeEffectObservedSucceeded",
    "RuntimeEffectObservedFailed",
    "RuntimeEffectObservedAbsent",
    "RuntimeEffectObservedConflict",
    "RuntimeEffectObservedIndeterminate",
    "RuntimeEffectObserverUnsupported",
    "RuntimeEffectObservationResult",
    "runtime_effect_intent_for_request",
    "runtime_effect_request_for_intent",
    "runtime_effect_intent_fingerprint",
    "runtime_effect_result_fingerprint",
    "runtime_effect_observation_fingerprint",
}


def _language():
    try:
        module = importlib.import_module(MODULE)
    except ModuleNotFoundError as error:
        if error.name != MODULE:
            raise
        raise AssertionError("missing #1693 runtime effect observation module") from error
    missing = sorted(PUBLIC_NAMES - set(vars(module)))
    if missing:
        raise AssertionError(f"missing #1693 public language: {', '.join(missing)}")
    return module


def _source(*, intent_event_id: str = "event-started-a") -> RuntimeEffectSource:
    return RuntimeEffectSource(
        workspace_id="workspace-a",
        request_id="request-a",
        run_id=RunId("run-a"),
        plan_id="plan-a",
        base_graph_id="graph-base",
        desired_graph_id="graph-desired",
        intent_event_id=intent_event_id,
    )


def _grant() -> SecretResolutionGrant:
    return SecretResolutionGrant(
        authorization_id="suse_" + "a" * 64,
        workspace_id="workspace-a",
        reference_registration_id="sref_" + "b" * 64,
        provider_registration_id="sprov_" + "c" * 64,
        endpoint_reference=SecretProviderEndpointReference("provider-a"),
        credential_reference=SecretReference("secret://bootstrap/provider-a-token"),
        reference=SecretReference("secret://local/workspace-a/docker/client-key"),
        intent=SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_KEY,
        actor_subject="worker-a",
        correlation_id="secret-use-" + "d" * 64,
        intent_fingerprint="e" * 64,
        run_id="run-a",
        activity_id="activity-a",
        effect_id="event-started-a",
    )


def _subclass_copy(value):
    hostile_type = type(f"Hostile{type(value).__name__}", (type(value),), {})
    arguments = {
        item.name: getattr(value, item.name)
        for item in fields(value)
        if item.init
    }
    return hostile_type(**arguments)


def _endpoint(
    *,
    subject_id: str = "api",
    socket_name: str = "http",
    graph_id: str = "graph-desired",
    protocol: Protocol = Protocol.HTTP,
    context: EndpointContext = EndpointContext.RUNTIME_PRIVATE,
    address: str = "http://api:8000",
) -> RuntimeEndpointObservation:
    return RuntimeEndpointObservation(
        subject_id=subject_id,
        socket_name=socket_name,
        graph_id=graph_id,
        protocol=protocol,
        context=context,
        address=LiteralEndpointMaterial(address),
    )


class RuntimeEffectExistingBoundaryTests(unittest.TestCase):
    def test_request_requires_effect_and_intent_event_congruence(self) -> None:
        class HostileText(str):
            pass

        candidates = (
            ("event-started-a", "different-event"),
            (HostileText("event-started-a"), "event-started-a"),
            ("event-started-a", HostileText("event-started-a")),
            ("event-started-\ud800", "event-started-\ud800"),
        )
        for effect_id, intent_event_id in candidates:
            with self.subTest(case=type(effect_id).__name__):
                with self.assertRaisesRegex(
                    RuntimeEffectContractError,
                    "effect.*event|event.*effect",
                ):
                    RuntimeEffectRequest(
                        effect_id=effect_id,
                        kind=RuntimeEffectKind.REALIZE_ACTIVITY,
                        runtime_kind=RuntimeKind.DOCKER,
                        source=_source(intent_event_id=intent_event_id),
                        activity_id=ActivityId("activity-a"),
                        operation=StartNode(NodeTarget("api")),
                    )

    def test_transient_secret_grants_are_repr_hidden_and_not_described(self) -> None:
        grant = _grant()
        request = RuntimeEffectRequest(
            effect_id="event-started-a",
            kind=RuntimeEffectKind.REALIZE_ACTIVITY,
            runtime_kind=RuntimeKind.DOCKER,
            source=_source(),
            activity_id=ActivityId("activity-a"),
            operation=StartNode(NodeTarget("api")),
            secret_resolution_grants=(grant,),
        )

        self.assertIs(request.secret_resolution_grants[0], grant)
        if "secret_resolution_grants" in request.descriptor():
            self.fail("runtime effect request descriptor exposes transient grants")
        for candidate in (
            grant.authorization_id,
            grant.endpoint_reference.reference_id,
            grant.credential_reference.reference_id,
            grant.actor_subject,
            grant.correlation_id,
            grant.intent_fingerprint,
        ):
            self.assertNotIn(candidate, repr(request))
            self.assertNotIn(candidate, repr(request.descriptor()))
        grant_field = next(
            item
            for item in fields(RuntimeEffectRequest)
            if item.name == "secret_resolution_grants"
        )
        self.assertTrue(grant_field.init)
        self.assertFalse(grant_field.repr)

    def test_live_result_fingerprint_has_exact_golden_and_distinct_domain(self) -> None:
        language = _language()
        result = RuntimeEffectResult.succeeded(
            "event-started-a",
            evidence={"container_state": "running", "exit_code": 0},
        )
        canonical = rfc8785.dumps(result.descriptor())

        self.assertEqual(
            canonical,
            b'{"effect_id":"event-started-a","evidence":{"container_state":"running","exit_code":0},"failure":null,"kind":"succeeded","observations":[]}',
        )
        self.assertEqual(
            language.runtime_effect_result_fingerprint(result),
            "c034bbfb31a97465a275bde6adc433b7060fe7f29ac6055e8ac6a58c520826c2",
        )
        self.assertEqual(
            hashlib.sha256(RESULT_DOMAIN + canonical).hexdigest(),
            "c034bbfb31a97465a275bde6adc433b7060fe7f29ac6055e8ac6a58c520826c2",
        )
        observation = language.RuntimeEffectObservedSucceeded(
            effect_id="event-started-a",
            request_fingerprint="a" * 64,
            evidence=language.RuntimeEffectObservationEvidence(
                {"container_state": "running", "exit_code": 0}
            ),
        )
        self.assertNotEqual(
            language.runtime_effect_result_fingerprint(result),
            language.runtime_effect_observation_fingerprint(observation),
        )

    def test_result_fingerprint_rejects_hostile_nominal_and_json_values(self) -> None:
        language = _language()

        result = RuntimeEffectResult.failed(
            "event-started-a",
            RuntimeEffectFailure("runtime.failed", "runtime failed"),
        )
        with self.assertRaises(RuntimeEffectContractError):
            language.runtime_effect_result_fingerprint(
                _subclass_copy(result)
            )

        forged = object.__new__(RuntimeEffectResult)
        for item in fields(result):
            object.__setattr__(
                forged,
                item.name,
                {"unsafe": float("nan")}
                if item.name == "evidence"
                else getattr(result, item.name),
            )
        with self.assertRaises(RuntimeEffectContractError):
            language.runtime_effect_result_fingerprint(forged)

    def test_live_result_fingerprint_commits_complete_nested_result(self) -> None:
        language = _language()
        succeeded = RuntimeEffectResult.succeeded(
            "event-started-a",
            evidence={"container_state": "running", "exit_code": 0},
            observations=(_endpoint(),),
        )
        baseline = language.runtime_effect_result_fingerprint(succeeded)
        endpoint = succeeded.observations[0]
        mutations = {
            "effect_id": replace(succeeded, effect_id="event-started-b"),
            "kind": RuntimeEffectResult.failed(
                "event-started-a",
                RuntimeEffectFailure("runtime.failed", "runtime failed"),
            ),
            "evidence_key": replace(
                succeeded,
                evidence={"provider_state": "running", "exit_code": 0},
            ),
            "evidence_text": replace(
                succeeded,
                evidence={"container_state": "stopped", "exit_code": 0},
            ),
            "evidence_integer": replace(
                succeeded,
                evidence={"container_state": "running", "exit_code": 1},
            ),
            "endpoint_subject": replace(
                succeeded,
                observations=(replace(endpoint, subject_id="worker"),),
            ),
            "endpoint_socket": replace(
                succeeded,
                observations=(replace(endpoint, socket_name="admin"),),
            ),
            "endpoint_graph": replace(
                succeeded,
                observations=(replace(endpoint, graph_id="graph-next"),),
            ),
            "endpoint_protocol": replace(
                succeeded,
                observations=(
                    _endpoint(
                        protocol=Protocol.POSTGRES,
                        address="postgres://api:5432",
                    ),
                ),
            ),
            "endpoint_context": replace(
                succeeded,
                observations=(replace(endpoint, context=EndpointContext.PUBLIC),),
            ),
            "endpoint_address": replace(
                succeeded,
                observations=(
                    replace(
                        endpoint,
                        address=LiteralEndpointMaterial("http://api:8001"),
                    ),
                ),
            ),
        }
        for name, candidate in mutations.items():
            with self.subTest(name=name):
                self.assertNotEqual(
                    language.runtime_effect_result_fingerprint(candidate), baseline
                )

        failure = RuntimeEffectFailure(
            "runtime.failed",
            "runtime failed",
            {"reason": "provider-error"},
        )
        failed = RuntimeEffectResult.failed("event-started-a", failure)
        unsupported = RuntimeEffectResult.unsupported("event-started-a", failure)
        uncertain = RuntimeEffectResult.uncertain("event-started-a", failure)
        self.assertEqual(
            len(
                {
                    language.runtime_effect_result_fingerprint(value)
                    for value in (failed, unsupported, uncertain)
                }
            ),
            3,
        )
        self.assertEqual(failed.failure, unsupported.failure)
        self.assertEqual(failed.failure, uncertain.failure)
        without_kind = lambda value: {
            key: nested
            for key, nested in value.descriptor().items()
            if key != "kind"
        }
        self.assertEqual(without_kind(failed), without_kind(unsupported))
        self.assertEqual(without_kind(failed), without_kind(uncertain))
        failed_fingerprint = language.runtime_effect_result_fingerprint(failed)
        for name, changed in {
            "failure_code": replace(failure, code="runtime.changed"),
            "failure_message": replace(failure, message="runtime changed"),
            "failure_details_key": replace(failure, details={"cause": "provider-error"}),
            "failure_details_value": replace(failure, details={"reason": "timeout"}),
        }.items():
            with self.subTest(name=name):
                self.assertNotEqual(
                    language.runtime_effect_result_fingerprint(
                        replace(failed, failure=changed)
                    ),
                    failed_fingerprint,
                )

    def test_complete_live_result_fingerprint_input_has_exact_byte_ceiling(self) -> None:
        language = _language()

        def result_for_size(target: int) -> RuntimeEffectResult:
            seed = RuntimeEffectResult.succeeded(
                "event-started-a",
                observations=(_endpoint(subject_id="s"),),
            )
            seed_size = len(rfc8785.dumps(seed.descriptor()))
            subject_size = target - seed_size + 1
            candidate = RuntimeEffectResult.succeeded(
                "event-started-a",
                observations=(_endpoint(subject_id="s" * subject_size),),
            )
            self.assertEqual(len(rfc8785.dumps(candidate.descriptor())), target)
            return candidate

        maximum = result_for_size(OUTCOME_FINGERPRINT_MAX_BYTES)
        plus_one = result_for_size(OUTCOME_FINGERPRINT_MAX_BYTES + 1)
        self.assertEqual(len(language.runtime_effect_result_fingerprint(maximum)), 64)
        with self.assertRaisesRegex(RuntimeEffectContractError, "too large") as caught:
            language.runtime_effect_result_fingerprint(plus_one)
        self.assertNotIn("s" * 64, str(caught.exception) + repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)


class RuntimeEffectObservationPackageBoundaryTests(unittest.TestCase):
    def test_root_exports_are_exact_module_identities(self) -> None:
        module = _language()
        missing = sorted(PUBLIC_NAMES - set(root.__all__))
        self.assertEqual(missing, [])
        for name in sorted(PUBLIC_NAMES):
            with self.subTest(name=name):
                self.assertIs(getattr(root, name), getattr(module, name))

    def test_inventory_owns_one_source_true_pure_module(self) -> None:
        inventory_path = os.environ.get("CPK_PACKAGE_MODULE_INVENTORY")
        if inventory_path is None:
            inventory_path = str(
                REPOSITORY_ROOT
                / "docs"
                / "architecture"
                / "package-module-inventory.json"
            )
        inventory = json.loads(
            Path(inventory_path).read_text(encoding="utf-8")
        )
        rows = [row for row in inventory["modules"] if row["module"] == MODULE]

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["owner"], "core")
        self.assertEqual(row["role"], "pure-contract")
        self.assertEqual(
            row["source"],
            "control_plane_kit_core/runtime_effect_observation.py",
        )
        self.assertEqual(set(row["canonical_public_exports"]), PUBLIC_NAMES)
        self.assertEqual(
            set(row["internal_dependencies"]),
            {
                "control_plane_kit_core.runtime_effects",
                "control_plane_kit_core.secrets",
            },
        )
        self.assertEqual(row["optional_external_dependencies"], [])
        self.assertEqual(
            set(row["protecting_tests"]),
            {
                "tests/test_runtime_effect_intent.py",
                "tests/test_runtime_effect_observation.py",
                "tests/test_runtime_effect_observation_boundary.py",
            },
        )
        self.assertIn("pure", row["motivation"].lower())
        self.assertIn("observation", row["motivation"].lower())

    def test_module_import_dag_and_calls_are_effect_free(self) -> None:
        path = SOURCE_ROOT / "runtime_effect_observation.py"
        self.assertTrue(path.is_file(), "missing #1693 observation source module")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: set[str] = set()
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)

        forbidden_import_roots = {
            "control_plane_kit_operations",
            "docker",
            "httpx",
            "pathlib",
            "psycopg",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
        forbidden_calls = {
            "connect",
            "execute",
            "open",
            "read_text",
            "resolve",
            "run",
            "system",
            "urlopen",
            "write_text",
        }
        self.assertEqual(
            {name.split(".", 1)[0] for name in imports} & forbidden_import_roots,
            set(),
        )
        self.assertEqual(calls & forbidden_calls, set())

        runtime_effects_tree = ast.parse(
            (SOURCE_ROOT / "runtime_effects.py").read_text(encoding="utf-8")
        )
        reverse_imports = {
            node.module
            for node in ast.walk(runtime_effects_tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn(MODULE, reverse_imports)


if __name__ == "__main__":
    unittest.main()
