from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import unittest

from control_plane_kit_core.operations import ActivityEventKind
from control_plane_kit_core.planning import ActivityId
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    PublicIngressLifecycle,
)
from control_plane_kit_core.secrets import SecretReference, SecretUseIntent
from control_plane_kit_operations.admission import ExternalReadinessAttestation
from control_plane_kit_operations.ingress_authorities import (
    CloudflareOwnedIngressResource,
    GeneratedIngressSecretReference,
    GeneratedSecretPurpose,
    IngressAuthorityProviderKind,
    IngressAuthorityRegistrationError,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    OperationsRecordError,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    AuthorizedSecretUse,
    SecretProviderRegistrationError,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "src" / "control_plane_kit_operations"


class _TextSubclass(str):
    pass


def _readiness(activity_id: str) -> ExternalReadinessAttestation:
    return ExternalReadinessAttestation(activity_id, "readiness/check-a")


def _event(activity_id: str) -> ActivityEventRecord:
    return ActivityEventRecord(
        event_id="event-a",
        run_id="run-a",
        ordinal=1,
        kind=ActivityEventKind.STEP_STARTED,
        occurred_at="2026-08-13T12:00:00Z",
        activity_id=activity_id,
    )


def _owned_ingress(activity_id: str) -> CloudflareOwnedIngressResource:
    return CloudflareOwnedIngressResource(
        workspace_id="workspace-a",
        runtime_id="runtime-a",
        ingress_id="gateway-a",
        authority_ref=IngressAuthorityReference("public-ingress-a"),
        provider_kind=IngressAuthorityProviderKind.CLOUDFLARE,
        tunnel_name="tunnel-a",
        tunnel_id="tunnel-a",
        dns_record_id="dns-a",
        hostname="gateway-a.example.test",
        zone_id="zone-a",
        lifecycle=PublicIngressLifecycle.EPHEMERAL,
        created_at="2026-08-13T12:00:00Z",
        observed_at="2026-08-13T12:00:01Z",
        source_run_id="run-a",
        source_activity_id=activity_id,
        source_event_id="event-a",
    )


def _generated_secret(activity_id: str) -> GeneratedIngressSecretReference:
    return GeneratedIngressSecretReference(
        workspace_id="workspace-a",
        purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
        secret_ref=SecretReference("secret://provider-a/generated/tunnel-a"),
        provider_registration_id="provider-a",
        reference_registration_id="reference-a",
        custody_id="custody-a",
        provider_version_id="version-a",
        provider_version_number=1,
        recorded_at="2026-08-13T12:00:00Z",
        source_run_id="run-a",
        source_activity_id=activity_id,
        source_event_id="event-a",
    )


def _authorize(activity_id: str) -> AuthorizeSecretUse:
    return AuthorizeSecretUse(
        workspace_id="workspace-a",
        reference=SecretReference("secret://provider-a/workload/token"),
        intent=SecretUseIntent.APPLICATION_CONTROL_TOKEN,
        actor_subject="operator-a",
        correlation_id="correlation-a",
        requested_at="2026-08-13T12:00:00Z",
        actor_scopes=(PolicyScope.SECRET_PROVIDER_USE,),
        activity_id=activity_id,
    )


def _authorized(activity_id: str) -> AuthorizedSecretUse:
    return AuthorizedSecretUse(
        authorization_id="authorization-a",
        workspace_id="workspace-a",
        reference_registration_id="reference-a",
        provider_registration_id="provider-a",
        reference=SecretReference("secret://provider-a/workload/token"),
        intent=SecretUseIntent.APPLICATION_CONTROL_TOKEN,
        actor_subject="operator-a",
        correlation_id="correlation-a",
        requested_at="2026-08-13T12:00:00Z",
        intent_fingerprint="a" * 64,
        activity_id=activity_id,
    )


class ActivityIdentityOperationsTests(unittest.TestCase):
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

    def test_operations_consumers_accept_exact_canonical_boundaries(self) -> None:
        factories = (
            _readiness,
            _event,
            _owned_ingress,
            _generated_secret,
            _authorize,
            _authorized,
        )
        for factory in factories:
            for candidate in ("a", "a" + "b" * 199):
                with self.subTest(factory=factory.__name__, length=len(candidate)):
                    value = factory(candidate)
                    observed = getattr(
                        value,
                        "activity_id",
                        getattr(value, "source_activity_id", None),
                    )
                    self.assertEqual(observed, candidate)
                    self.assertIs(type(observed), str)

    def test_operations_consumers_reject_noncanonical_activity_identities(self) -> None:
        cases = (
            (_readiness, InvalidOperationCommand),
            (_event, OperationsRecordError),
            (_owned_ingress, IngressAuthorityRegistrationError),
            (_generated_secret, IngressAuthorityRegistrationError),
            (_authorize, SecretProviderRegistrationError),
            (_authorized, SecretProviderRegistrationError),
        )
        invalid = (
            _TextSubclass("operations-subclass-canary"),
            "operations/control\ncanary",
            "-operations-leading-canary",
            "a" * 201,
        )
        for factory, error_type in cases:
            for candidate in invalid:
                with self.subTest(factory=factory.__name__, candidate=repr(candidate[:24])):
                    self.assert_bounded_error(
                        error_type,
                        lambda factory=factory, candidate=candidate: factory(candidate),
                        "canary",
                        "a" * 32,
                    )

    def test_operations_use_public_activity_id_without_private_core_imports(self) -> None:
        consumers = (
            "admission.py",
            "records.py",
            "ingress_authorities.py",
            "secret_providers.py",
        )
        for filename in consumers:
            tree = ast.parse((SOURCE_ROOT / filename).read_text(encoding="utf-8"))
            public_imports = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module == "control_plane_kit_core.planning"
                for alias in node.names
            }
            with self.subTest(filename=filename):
                self.assertIn("ActivityId", public_imports)

        for source_path in SOURCE_ROOT.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            imported_modules = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imported_modules.update(
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            )
            with self.subTest(source_path=source_path.name):
                self.assertNotIn(
                    "control_plane_kit_core._activity_identity",
                    imported_modules,
                )

        self.assertIs(
            ActivityId,
            __import__(
                "control_plane_kit_core.planning.activity_plan",
                fromlist=("ActivityId",),
            ).ActivityId,
        )

    def test_private_core_owner_is_exhaustively_inventoried(self) -> None:
        inventory_path = Path(os.environ["CPK_PACKAGE_MODULE_INVENTORY"])
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        rows = tuple(
            row
            for row in inventory["modules"]
            if row["module"] == "control_plane_kit_core._activity_identity"
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["owner"], "core")
        self.assertEqual(row["destination"], "control_plane_kit_core._activity_identity")
        self.assertEqual(row["source"], "control_plane_kit_core/_activity_identity.py")
        self.assertEqual(row["canonical_public_exports"], [])


if __name__ == "__main__":
    unittest.main()
