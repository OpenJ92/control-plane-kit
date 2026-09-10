"""#1796: connection credentials are independent of process permissions."""

from dataclasses import FrozenInstanceError, replace
import unittest

import control_plane_kit_core as core
from control_plane_kit_core import runtime_authority as authority
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityReference,
    RuntimeEffectContractError,
)
from control_plane_kit_core.secrets import SecretReference, SecretUseIntent
from test_runtime_effect_observation import _grant


AUTHORITY = RuntimeAuthorityReference("remote-docker")
SLOTS = (
    ("ca_certificate", SecretUseIntent.DOCKER_REMOTE_TLS_CA_CERTIFICATE),
    ("client_certificate", SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_CERTIFICATE),
    ("client_key", SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_KEY),
)
COORDINATES = dict(
    authority_ref=AUTHORITY,
    workspace_id="workspace-a",
    effect_id="event-started-a",
    run_id="run-a",
    activity_id="activity-a",
)


def _language():
    names = (
        "RemoteDockerTlsConnectionAdmission",
        "runtime_connection_secret_uses",
        "validate_runtime_connection_grants",
    )
    missing = [name for name in names if not hasattr(authority, name)]
    if missing:
        raise AssertionError("missing #1796 connection language: " + ", ".join(missing))
    return authority


def _admission(**overrides):
    values = dict(
        authority_ref=AUTHORITY,
        **{
            slot: SecretReference(f"secret://private/docker/{slot}")
            for slot, _ in SLOTS
        },
    )
    values.update(overrides)
    return _language().RemoteDockerTlsConnectionAdmission(**values)


def _grants(admission):
    return tuple(
        _grant(
            fresh=chr(ord("a") + index),
            label=slot,
            reference=getattr(admission, slot).reference_id,
            intent=intent,
        )
        for index, (slot, intent) in enumerate(SLOTS)
    )


class RuntimeConnectionAdmissionTests(unittest.TestCase):
    def assert_rejected(self, action, *private_values):
        with self.assertRaises(RuntimeEffectContractError) as caught:
            action()
        error = caught.exception
        self.assertLessEqual(len(str(error)), 512)
        for private in private_values:
            self.assertNotIn(private, str(error))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    def test_public_frozen_value_derives_exact_three_typed_uses(self):
        language = _language()
        for name in (
            "RemoteDockerTlsConnectionAdmission",
            "runtime_connection_secret_uses",
            "validate_runtime_connection_grants",
        ):
            self.assertIs(getattr(core, name, None), getattr(language, name))
        admission = _admission()
        self.assertEqual(admission.authority_ref, AUTHORITY)
        self.assertEqual(
            language.runtime_connection_secret_uses(admission, authority_ref=AUTHORITY),
            tuple((getattr(admission, slot), intent) for slot, intent in SLOTS),
        )
        with self.assertRaises(FrozenInstanceError):
            admission.client_key = SecretReference("secret://private/replacement")

    def test_one_reference_can_serve_distinct_roles(self):
        language = _language()
        shared = SecretReference("secret://private/shared")
        admission = _admission(**{slot: shared for slot, _ in SLOTS})
        self.assertEqual(
            language.runtime_connection_secret_uses(admission, authority_ref=AUTHORITY),
            tuple((shared, intent) for _, intent in SLOTS),
        )
        language.validate_runtime_connection_grants(
            admission, _grants(admission), **COORDINATES
        )

    def test_slots_require_complete_typed_values_without_coercion(self):
        _language()
        marker = "secret://private/do-not-echo"
        for slot, _ in SLOTS:
            for malformed in (None, marker, {"reference_id": marker}, 12):
                with self.subTest(slot=slot, malformed_type=type(malformed).__name__):
                    self.assert_rejected(
                        lambda: _admission(**{slot: malformed}), marker
                    )
        for malformed in (None, "remote-docker", {"reference_id": "remote-docker"}):
            with self.subTest(authority_type=type(malformed).__name__):
                self.assert_rejected(lambda: _admission(authority_ref=malformed))

    def test_repr_and_public_descriptor_hide_credential_references(self):
        admission = _admission()
        descriptor = admission.descriptor()
        self.assertIsInstance(descriptor, dict)
        self.assertEqual(descriptor["authority_ref"], AUTHORITY.descriptor())
        for surface in (repr(admission), repr(descriptor)):
            for slot, _ in SLOTS:
                self.assertNotIn(getattr(admission, slot).reference_id, surface)
            self.assertNotIn("secret://", surface)
        self.assertEqual(descriptor, admission.descriptor())

    def test_missing_admission_allows_no_connection_grants(self):
        language = _language()
        self.assertEqual(
            language.runtime_connection_secret_uses(None, authority_ref=AUTHORITY), ()
        )
        self.assertIsNone(
            language.validate_runtime_connection_grants(None, (), **COORDINATES)
        )
        self.assert_rejected(
            lambda: language.validate_runtime_connection_grants(
                None, _grants(_admission()), **COORDINATES
            )
        )

    def test_expected_authority_is_exact_for_uses_and_grants(self):
        language = _language()
        admission = _admission()
        for expected in (None, "remote-docker", RuntimeAuthorityReference("foreign")):
            with self.subTest(expected=expected):
                self.assert_rejected(
                    lambda: language.runtime_connection_secret_uses(
                        admission, authority_ref=expected
                    )
                )
                self.assert_rejected(
                    lambda: language.validate_runtime_connection_grants(
                        admission, (), **{**COORDINATES, "authority_ref": expected}
                    )
                )

    def test_empty_partial_and_fresh_complete_grants_are_structurally_valid(self):
        language = _language()
        admission = _admission()
        grants = _grants(admission)
        fresh = tuple(
            _grant(
                fresh=chr(ord("d") + index), label=slot,
                reference=getattr(admission, slot).reference_id, intent=intent,
            )
            for index, (slot, intent) in enumerate(SLOTS)
        )
        for supplied in ((), grants[:1], grants[1:], grants, fresh, grants[::-1]):
            with self.subTest(count=len(supplied)):
                self.assertIsNone(language.validate_runtime_connection_grants(
                    admission, supplied, **COORDINATES
                ))

    def test_duplicate_exact_use_wrong_reference_and_wrong_intent_are_rejected(self):
        language = _language()
        admission = _admission()
        grants = _grants(admission)
        wrong_reference = SecretReference("secret://private/unrelated")
        for supplied in (
            grants + (grants[0],),
            (grants[0], replace(grants[0], authorization_id="suse_" + "f" * 64)),
            (replace(grants[0], reference=wrong_reference),),
            (replace(grants[0], intent=SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_KEY),),
        ):
            with self.subTest(count=len(supplied)):
                self.assert_rejected(
                    lambda: language.validate_runtime_connection_grants(
                        admission, supplied, **COORDINATES
                    ), wrong_reference.reference_id, grants[0].reference.reference_id,
                    grants[0].authorization_id,
                )

    def test_every_grant_must_match_workspace_effect_run_and_activity(self):
        language = _language()
        admission = _admission()
        grant = _grants(admission)[0]
        for coordinate in ("workspace_id", "effect_id", "run_id", "activity_id"):
            with self.subTest(coordinate=coordinate):
                supplied = (replace(grant, **{coordinate: "foreign-private-coordinate"}),)
                self.assert_rejected(
                    lambda: language.validate_runtime_connection_grants(
                        admission, supplied, **COORDINATES
                    ), "foreign-private-coordinate", grant.reference.reference_id,
                )

    def test_carrier_and_grant_collection_are_closed_typed_values(self):
        language = _language()
        admission = _admission()
        for malformed in ("secret://private/carrier", {}, AUTHORITY):
            with self.subTest(carrier_type=type(malformed).__name__):
                self.assert_rejected(
                    lambda: language.runtime_connection_secret_uses(
                        malformed, authority_ref=AUTHORITY
                    ), "secret://private/carrier",
                )
        for malformed in (None, [], list(_grants(admission)), ({},), ("private-grant",)):
            with self.subTest(grants_type=type(malformed).__name__):
                self.assert_rejected(
                    lambda: language.validate_runtime_connection_grants(
                        admission, malformed, **COORDINATES
                    ), "private-grant",
                )


if __name__ == "__main__":
    unittest.main()
