from __future__ import annotations

import ast
from copy import deepcopy
import importlib
from itertools import permutations
from pathlib import Path
import re
import subprocess
import sys
import unittest

from control_plane_kit_core.operations.recovery import (
    EffectAttemptIdentity,
    InvalidEffectRecoveryContract,
)
from control_plane_kit_core.planning import (
    ActivityDependency,
    ActivityId,
    ActivityPlan,
    ActivityPlanDescriptorCodec,
    MalformedActivityPlanDescriptor,
    NodeTarget,
    PlannedActivity,
    StartNode,
    WaitForHealthy,
    compile_activity_plan,
)
from control_plane_kit_core.secrets import (
    SecretCustodyGrant,
    SecretProviderContractError,
    SecretProviderEndpointReference,
    SecretReference,
    SecretResolutionGrant,
    SecretUseIntent,
    SecretVersionRevocationGrant,
)
from control_plane_kit_core.topology import DeploymentGraph, diff_graphs, validate_graph

from tests.test_graph_codec import public_ingress_graph


ACTIVITY_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "src" / "control_plane_kit_core"


class _TextSubclass(str):
    pass


def _resolution_grant(activity_id: str) -> SecretResolutionGrant:
    return SecretResolutionGrant(
        authorization_id="authorization-a",
        workspace_id="workspace-a",
        reference_registration_id="reference-a",
        provider_registration_id="provider-a",
        endpoint_reference=SecretProviderEndpointReference("provider-a"),
        credential_reference=SecretReference("secret://bootstrap/provider-token"),
        reference=SecretReference("secret://provider-a/workload/token"),
        intent=SecretUseIntent.APPLICATION_CONTROL_TOKEN,
        actor_subject="worker-a",
        correlation_id="correlation-a",
        intent_fingerprint="a" * 64,
        activity_id=activity_id,
    )


def _custody_grant(activity_id: str) -> SecretCustodyGrant:
    return SecretCustodyGrant(
        custody_id="custody-a",
        workspace_id="workspace-a",
        provider_registration_id="provider-a",
        endpoint_reference=SecretProviderEndpointReference("provider-a"),
        credential_reference=SecretReference("secret://bootstrap/provider-token"),
        reference=SecretReference("secret://provider-a/generated/token"),
        intent=SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN,
        actor_subject="worker-a",
        correlation_id="correlation-a",
        custody_fingerprint="b" * 64,
        activity_id=activity_id,
    )


def _revocation_grant(activity_id: str) -> SecretVersionRevocationGrant:
    return SecretVersionRevocationGrant(
        revocation_id="revocation-a",
        workspace_id="workspace-a",
        provider_registration_id="provider-a",
        endpoint_reference=SecretProviderEndpointReference("provider-a"),
        credential_reference=SecretReference("secret://bootstrap/provider-token"),
        reference=SecretReference("secret://provider-a/generated/token"),
        version_id="version-a",
        version_number=1,
        actor_subject="worker-a",
        correlation_id="correlation-a",
        revocation_fingerprint="c" * 64,
        activity_id=activity_id,
    )


class ActivityIdentityContractTests(unittest.TestCase):
    def assert_bounded_error(
        self,
        error_type: type[BaseException],
        callback,
        *canaries: str,
    ) -> None:
        with self.assertRaises(error_type) as captured:
            callback()
        error = captured.exception
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 512)
        for canary in canaries:
            self.assertNotIn(canary, rendered)

    def test_activity_id_owns_the_exact_canonical_grammar(self) -> None:
        valid = ("a", "a" + "b" * 199, "deploy.node-1:0123456789abcdef")
        for candidate in valid:
            with self.subTest(valid=candidate[:24]):
                identity = ActivityId(candidate)
                self.assertEqual(identity.value, candidate)
                self.assertIs(type(identity.value), str)
                self.assertIsNotNone(ACTIVITY_ID_PATTERN.fullmatch(candidate))

        invalid = (
            (object(), ()),
            (True, ("True",)),
            (_TextSubclass("subclass-canary"), ("subclass-canary",)),
            ("", ()),
            (" ", ()),
            ("-leading-canary", ("leading-canary",)),
            ("slash/canary", ("slash/canary",)),
            ("space canary", ("space canary",)),
            ("line\ncanary", ("canary",)),
            ("a" * 201, ("a" * 32,)),
        )
        for candidate, canaries in invalid:
            with self.subTest(invalid=type(candidate).__name__, length=getattr(candidate, "__len__", lambda: -1)()):
                self.assert_bounded_error(
                    ValueError,
                    lambda candidate=candidate: ActivityId(candidate),  # type: ignore[arg-type]
                    *canaries,
                )

    def test_compiler_generated_activity_ids_remain_canonical(self) -> None:
        desired = validate_graph(public_ingress_graph())
        current = validate_graph(DeploymentGraph(desired.graph.name))

        plan = compile_activity_plan(diff_graphs(current, desired))

        self.assertTrue(plan.activities)
        for activity in plan.activities:
            with self.subTest(activity_id=activity.activity_id.value):
                self.assertIsNotNone(
                    ACTIVITY_ID_PATTERN.fullmatch(activity.activity_id.value)
                )

    def test_codec_translates_activity_and_dependency_identity_failures(self) -> None:
        start = PlannedActivity(ActivityId("start"), StartNode(NodeTarget("api")))
        wait = PlannedActivity(
            ActivityId("wait"),
            WaitForHealthy(NodeTarget("api")),
            dependencies=(ActivityDependency(start.activity_id),),
        )
        descriptor = ActivityPlanDescriptorCodec().encode(ActivityPlan((start, wait)))
        cases = []
        malformed_activity = ActivityPlanDescriptorCodec().encode(
            ActivityPlan((start,))
        )
        malformed_activity["activities"][0]["activity_id"] = "activity/codec-canary"
        cases.append(malformed_activity)
        malformed_dependency = deepcopy(descriptor)
        malformed_dependency["activities"][0]["activity_id"] = (
            "dependency/codec-canary"
        )
        dependent = next(
            item for item in malformed_dependency["activities"] if item["dependencies"]
        )
        dependent["dependencies"][0] = "dependency/codec-canary"
        cases.append(malformed_dependency)

        for candidate in cases:
            with self.subTest(candidate=candidate):
                self.assert_bounded_error(
                    MalformedActivityPlanDescriptor,
                    lambda candidate=candidate: ActivityPlanDescriptorCodec().decode(candidate),
                    "codec-canary",
                )

    def test_effect_attempt_identity_uses_the_activity_id_grammar(self) -> None:
        for candidate in ("a", "a" + "b" * 199):
            with self.subTest(valid=len(candidate)):
                self.assertEqual(
                    EffectAttemptIdentity("run-a", candidate, 1).activity_id,
                    candidate,
                )

        for candidate in (
            _TextSubclass("attempt-subclass-canary"),
            "attempt/control\ncanary",
            "a" * 201,
        ):
            with self.subTest(invalid=repr(candidate[:24])):
                self.assert_bounded_error(
                    InvalidEffectRecoveryContract,
                    lambda candidate=candidate: EffectAttemptIdentity(
                        "run-a", candidate, 1
                    ),
                    "canary",
                    "a" * 32,
                )

    def test_secret_grants_share_exact_activity_identity_admission(self) -> None:
        factories = (_resolution_grant, _custody_grant, _revocation_grant)
        for factory in factories:
            for candidate in ("a", "a" + "b" * 199):
                with self.subTest(factory=factory.__name__, valid=len(candidate)):
                    self.assertEqual(factory(candidate).activity_id, candidate)
            for candidate in (
                _TextSubclass("grant-subclass-canary"),
                "grant/control\ncanary",
                "a" * 201,
            ):
                with self.subTest(factory=factory.__name__, invalid=repr(candidate[:24])):
                    self.assert_bounded_error(
                        SecretProviderContractError,
                        lambda factory=factory, candidate=candidate: factory(candidate),
                        "canary",
                        "a" * 32,
                    )

    def test_public_import_order_and_identity_are_stable(self) -> None:
        module_names = (
            "control_plane_kit_core",
            "control_plane_kit_core.planning",
            "control_plane_kit_core.operations",
        )
        for order in permutations(module_names):
            script = "\n".join(
                [
                    "import importlib",
                    *(f"importlib.import_module({name!r})" for name in order),
                    "root = importlib.import_module('control_plane_kit_core')",
                    "planning = importlib.import_module('control_plane_kit_core.planning')",
                    "owner = importlib.import_module('control_plane_kit_core.planning.activity_plan')",
                    "assert planning.ActivityId is owner.ActivityId",
                    "assert not hasattr(root, 'ActivityId')",
                    "assert 'ActivityId' not in root.__all__",
                    "assert '_activity_identity' not in root.__all__",
                    "assert '_activity_identity' not in planning.__all__",
                ]
            )
            with self.subTest(order=order):
                completed = subprocess.run(
                    [sys.executable, "-c", script],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_private_helper_is_pure_internal(self) -> None:
        helper_path = SOURCE_ROOT / "_activity_identity.py"
        self.assertTrue(helper_path.is_file(), "missing private activity identity owner")

        tree = ast.parse(helper_path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
                imported.add(node.module or "")
        self.assertLessEqual(imported, {"re"})

        helper = importlib.import_module("control_plane_kit_core._activity_identity")
        self.assertEqual(helper.__name__, "control_plane_kit_core._activity_identity")


if __name__ == "__main__":
    unittest.main()
