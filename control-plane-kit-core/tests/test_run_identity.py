from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
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
    ActivityJournalEvent,
    ActivityJournalEventKind,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectContractError,
    RuntimeEffectSource,
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


RUN_ID_MODULE = "control_plane_kit_core.operations.run_identity"
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "src" / "control_plane_kit_core"


class _TextSubclass(str):
    pass


def _candidate_label(value: object) -> str:
    if isinstance(value, str):
        controls = tuple(
            ord(character)
            for character in value
            if ord(character) < 32 or ord(character) == 127
        )
        if controls:
            return "ascii-control-" + "-".join(str(code) for code in controls)
        return repr(value[:24])
    return type(value).__name__


def _invalid_run_identities() -> tuple[tuple[object, tuple[str, ...]], ...]:
    controls = tuple(
        (f"a{chr(code)}control-canary", ("control-canary",))
        for code in (*range(32), 127)
    )
    return (
        (object(), ()),
        (True, ("True",)),
        (_TextSubclass("subclass-canary"), ("subclass-canary",)),
        ("", ()),
        (" ", ()),
        ("-leading-canary", ("leading-canary",)),
        (".leading-canary", ("leading-canary",)),
        ("_leading-canary", ("leading-canary",)),
        (":leading-canary", ("leading-canary",)),
        ("slash/canary", ("slash/canary",)),
        ("space canary", ("space canary",)),
        *controls,
        ("a" * 201, ("a" * 32,)),
    )


INVALID_RUN_IDENTITIES = _invalid_run_identities()
VALID_RUN_IDENTITIES = (
    "a",
    "run.node_1:attempt-2",
    "a" + "b" * 199,
)


def _contract():
    try:
        module = importlib.import_module(RUN_ID_MODULE)
    except ModuleNotFoundError as error:
        if error.name != RUN_ID_MODULE:
            raise
        raise AssertionError("missing #1636 RunId") from error
    run_id = getattr(module, "RunId", None)
    if run_id is None:
        raise AssertionError("missing #1636 RunId export")
    return module, run_id


def _source(run_id: object) -> RuntimeEffectSource:
    return RuntimeEffectSource(
        workspace_id="workspace-a",
        request_id="request-a",
        run_id=run_id,  # type: ignore[arg-type]
        plan_id="plan-a",
        base_graph_id="graph-base",
        desired_graph_id="graph-desired",
        intent_event_id="event-started",
    )


def _resolution_grant(run_id: object) -> SecretResolutionGrant:
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
        run_id=run_id,  # type: ignore[arg-type]
    )


def _custody_grant(run_id: object) -> SecretCustodyGrant:
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
        run_id=run_id,  # type: ignore[arg-type]
    )


def _revocation_grant(run_id: object) -> SecretVersionRevocationGrant:
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
        run_id=run_id,  # type: ignore[arg-type]
    )


class RunIdentityContractTests(unittest.TestCase):
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

    def test_missing_module_guard_does_not_mask_nested_dependency(self) -> None:
        original_import_module = importlib.import_module
        nested_name = "control_plane_kit_core.operations.nested_canary"

        def fail_with_nested_dependency(name: str, package: str | None = None):
            if name == RUN_ID_MODULE:
                raise ModuleNotFoundError(
                    "nested dependency is absent",
                    name=nested_name,
                )
            return original_import_module(name, package)

        importlib.import_module = fail_with_nested_dependency
        try:
            with self.assertRaises(ModuleNotFoundError) as captured:
                _contract()
        finally:
            importlib.import_module = original_import_module

        self.assertEqual(captured.exception.name, nested_name)

    def test_run_id_owns_exact_nominal_canonical_grammar(self) -> None:
        _, RunId = _contract()
        for candidate in VALID_RUN_IDENTITIES:
            with self.subTest(valid=candidate[:24]):
                identity = RunId(candidate)
                self.assertEqual(identity.value, candidate)
                self.assertIs(type(identity.value), str)
                self.assertIsNotNone(RUN_ID_PATTERN.fullmatch(candidate))
                self.assertFalse(hasattr(identity, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    identity.value = "other"  # type: ignore[misc]

        self.assertLess(RunId("run-a"), RunId("run-b"))
        self.assertNotEqual(RunId("run-a"), "run-a")
        for candidate, canaries in INVALID_RUN_IDENTITIES:
            with self.subTest(invalid=_candidate_label(candidate)):
                self.assert_bounded_error(
                    ValueError,
                    lambda candidate=candidate: RunId(candidate),
                    *canaries,
                )

    def test_effect_attempt_identity_is_nominal_and_descriptor_bijective(self) -> None:
        _, RunId = _contract()
        run_id = RunId("run-a")
        identity = EffectAttemptIdentity(run_id, "activity-a", 1)

        self.assertIs(identity.run_id, run_id)
        self.assertEqual(
            identity.descriptor(),
            {"run_id": "run-a", "activity_id": "activity-a", "attempt": 1},
        )
        decoded = EffectAttemptIdentity.from_descriptor(identity.descriptor())
        self.assertEqual(decoded, identity)
        self.assertIs(type(decoded.run_id), RunId)

        class HostileRunId(RunId):
            pass

        for candidate in ("run-a", HostileRunId("run-a"), object()):
            with self.subTest(candidate=type(candidate).__name__):
                self.assert_bounded_error(
                    InvalidEffectRecoveryContract,
                    lambda candidate=candidate: EffectAttemptIdentity(
                        candidate,  # type: ignore[arg-type]
                        "activity-a",
                        1,
                    ),
                )
        self.assert_bounded_error(
            InvalidEffectRecoveryContract,
            lambda: EffectAttemptIdentity.from_descriptor(
                {
                    "run_id": "run/descriptor-canary",
                    "activity_id": "activity-a",
                    "attempt": 1,
                }
            ),
            "descriptor-canary",
        )

    def test_runtime_effect_source_is_nominal_and_descriptor_bijective(self) -> None:
        _, RunId = _contract()
        run_id = RunId("run-a")
        source = _source(run_id)

        self.assertIs(source.run_id, run_id)
        self.assertEqual(source.descriptor()["run_id"], "run-a")
        decoded = RuntimeEffectSource.from_descriptor(source.descriptor())
        self.assertEqual(decoded, source)
        self.assertIs(type(decoded.run_id), RunId)

        class HostileRunId(RunId):
            pass

        for candidate in ("run-a", HostileRunId("run-a"), object()):
            with self.subTest(candidate=type(candidate).__name__):
                self.assert_bounded_error(
                    RuntimeEffectContractError,
                    lambda candidate=candidate: _source(candidate),
                )
        descriptor = source.descriptor()
        descriptor["run_id"] = "run/descriptor-canary"
        self.assert_bounded_error(
            RuntimeEffectContractError,
            lambda: RuntimeEffectSource.from_descriptor(descriptor),
            "descriptor-canary",
        )

    def test_journal_string_boundary_uses_exact_run_grammar(self) -> None:
        for candidate in VALID_RUN_IDENTITIES:
            with self.subTest(valid=candidate[:24]):
                event = ActivityJournalEvent(
                    "event-a",
                    candidate,
                    1,
                    ActivityJournalEventKind.STEP_STARTED,
                    "activity-a",
                )
                self.assertEqual(event.run_id, candidate)
                self.assertIs(type(event.run_id), str)

        failures = []
        for candidate, canaries in INVALID_RUN_IDENTITIES:
            label = _candidate_label(candidate)
            try:
                self.assert_bounded_error(
                    ValueError,
                    lambda candidate=candidate: ActivityJournalEvent(
                        "event-a",
                        candidate,  # type: ignore[arg-type]
                        1,
                        ActivityJournalEventKind.STEP_STARTED,
                        "activity-a",
                    ),
                    *canaries,
                )
            except AssertionError:
                failures.append(label)
        self.assertEqual(failures, [], f"journal admitted or leaked: {failures!r}")

    def test_secret_grants_share_exact_optional_run_grammar(self) -> None:
        factories = (_resolution_grant, _custody_grant, _revocation_grant)
        failures = []
        for factory in factories:
            self.assertIsNone(factory(None).run_id)
            for candidate in VALID_RUN_IDENTITIES:
                with self.subTest(factory=factory.__name__, valid=candidate[:24]):
                    grant = factory(candidate)
                    self.assertEqual(grant.run_id, candidate)
                    self.assertIs(type(grant.run_id), str)
            for candidate, canaries in INVALID_RUN_IDENTITIES:
                label = f"{factory.__name__}:{_candidate_label(candidate)}"
                try:
                    self.assert_bounded_error(
                        SecretProviderContractError,
                        lambda factory=factory, candidate=candidate: factory(candidate),
                        *canaries,
                    )
                except AssertionError:
                    failures.append(label)
        self.assertEqual(failures, [], f"secret grant admitted or leaked: {failures!r}")

    def test_public_import_order_and_nominal_identity_are_stable(self) -> None:
        module_names = (
            "control_plane_kit_core",
            "control_plane_kit_core.planning",
            "control_plane_kit_core.operations",
        )
        failures = []
        for order in permutations(module_names):
            script = "\n".join(
                [
                    "import importlib",
                    *(f"importlib.import_module({name!r})" for name in order),
                    "root = importlib.import_module('control_plane_kit_core')",
                    "planning = importlib.import_module('control_plane_kit_core.planning')",
                    "operations = importlib.import_module('control_plane_kit_core.operations')",
                    f"owner = importlib.import_module({RUN_ID_MODULE!r})",
                    "assert operations.RunId is owner.RunId",
                    "assert not hasattr(root, 'RunId')",
                    "assert 'RunId' not in root.__all__",
                    "assert not hasattr(planning, 'RunId')",
                    "assert 'RunId' not in planning.__all__",
                    "assert '_run_identity' not in root.__all__",
                    "assert '_run_identity' not in planning.__all__",
                    "assert '_run_identity' not in operations.__all__",
                ]
            )
            completed = subprocess.run(
                [sys.executable, "-c", script],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                failures.append(order)
        self.assertEqual(failures, [], f"failed import orders: {failures!r}")

    def test_private_helper_has_exact_pure_ownership(self) -> None:
        helper_path = SOURCE_ROOT / "_run_identity.py"
        self.assertTrue(helper_path.is_file(), "missing private run identity owner")
        tree = ast.parse(helper_path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
                imported.add(node.module or "")
        self.assertLessEqual(imported, {"re"})

        importers = set()
        for source_path in SOURCE_ROOT.rglob("*.py"):
            source_tree = ast.parse(source_path.read_text(encoding="utf-8"))
            if any(
                isinstance(node, ast.ImportFrom)
                and node.module == "control_plane_kit_core._run_identity"
                for node in ast.walk(source_tree)
            ):
                importers.add(source_path.relative_to(SOURCE_ROOT).as_posix())
        self.assertEqual(
            importers,
            {"operations/run_identity.py", "planning/saga.py", "secrets.py"},
        )


if __name__ == "__main__":
    unittest.main()
