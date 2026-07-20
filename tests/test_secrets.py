from __future__ import annotations

import unittest

from control_plane_kit.core.secrets import (
    LocalDevelopmentSecretResolver,
    SecretDenied,
    SecretEnvironmentDelivery,
    SecretFileDelivery,
    SecretFileMode,
    SecretFilePathBinding,
    SecretMissing,
    SecretProviderAuthority,
    SecretProviderId,
    SecretReference,
    SecretReferenceEnvironmentDelivery,
    SecretResolutionCode,
    SecretResolutionError,
    SecretResolved,
    require_resolved_secret,
    secret_delivery_from_descriptor,
)


class SecretContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.authority = SecretProviderAuthority(
            SecretProviderId("local"),
            (("workspace-a",),),
        )
        self.resolver = LocalDevelopmentSecretResolver(
            self.authority,
            {"secret://local/workspace-a/database": "never-persist-this"},
        )

    def test_reference_has_provider_and_path_identity(self) -> None:
        reference = SecretReference("secret://local/workspace-a/database")

        self.assertEqual(reference.provider_id, SecretProviderId("local"))
        self.assertEqual(reference.path, ("workspace-a", "database"))

    def test_malformed_references_fail_at_construction(self) -> None:
        for value in ("", "token", "secret://", "secret://LOCAL/key", "secret://local/a/../b"):
            with self.subTest(value=value), self.assertRaises(SecretResolutionError):
                SecretReference(value)

    def test_local_resolver_distinguishes_resolved_missing_and_denied(self) -> None:
        resolved = self.resolver.resolve(
            SecretReference("secret://local/workspace-a/database")
        )
        missing = self.resolver.resolve(
            SecretReference("secret://local/workspace-a/missing")
        )
        denied = self.resolver.resolve(
            SecretReference("secret://local/workspace-b/database")
        )

        self.assertIsInstance(resolved, SecretResolved)
        self.assertIsInstance(missing, SecretMissing)
        self.assertIsInstance(denied, SecretDenied)

    def test_values_are_released_only_by_explicit_runtime_interpretation(self) -> None:
        value = require_resolved_secret(
            self.resolver,
            SecretReference("secret://local/workspace-a/database"),
        )

        self.assertEqual(value.reveal(), "never-persist-this")
        self.assertNotIn("never-persist-this", repr(value))
        self.assertNotIn("never-persist-this", repr(self.resolver))

    def test_denied_and_missing_errors_are_closed_and_redacted(self) -> None:
        cases = (
            ("secret://local/workspace-a/missing", SecretResolutionCode.MISSING),
            ("secret://local/workspace-b/database", SecretResolutionCode.DENIED),
        )
        for reference_id, expected in cases:
            with self.subTest(reference_id=reference_id):
                with self.assertRaises(SecretResolutionError) as raised:
                    require_resolved_secret(self.resolver, SecretReference(reference_id))
                self.assertIs(raised.exception.code, expected)
                self.assertNotIn(reference_id, str(raised.exception))
                self.assertNotIn("never-persist-this", str(raised.exception))

    def test_bootstrap_configuration_cannot_exceed_authority(self) -> None:
        with self.assertRaises(SecretResolutionError) as raised:
            LocalDevelopmentSecretResolver(
                self.authority,
                {"secret://local/workspace-b/database": "forbidden"},
            )

        self.assertIs(raised.exception.code, SecretResolutionCode.DENIED)
        self.assertNotIn("forbidden", str(raised.exception))

    def test_closed_delivery_variants_round_trip(self) -> None:
        values = (
            SecretEnvironmentDelivery(
                "DATABASE_URL",
                SecretReference("secret://local/workspace-a/database"),
            ),
            SecretReferenceEnvironmentDelivery(
                "DATABASE_REFERENCE",
                SecretReference("secret://local/workspace-a/database"),
            ),
            SecretFileDelivery(
                "/run/secrets/database-password",
                SecretReference("secret://local/workspace-a/database-password"),
                SecretFileMode.OWNER_READ_ONLY,
                SecretFilePathBinding("POSTGRES_PASSWORD_FILE"),
            ),
        )

        self.assertEqual(
            tuple(secret_delivery_from_descriptor(value.descriptor()) for value in values),
            values,
        )

    def test_secret_file_targets_are_closed_to_protected_namespace(self) -> None:
        for target in (
            "/etc/service/password",
            "/run/secrets/../password",
            "/run/secrets/",
            "run/secrets/password",
        ):
            with self.subTest(target=target), self.assertRaises(SecretResolutionError):
                SecretFileDelivery(
                    target,
                    SecretReference("secret://local/workspace-a/password"),
                )

    def test_unknown_or_extra_delivery_fields_fail_closed(self) -> None:
        malformed = (
            {"kind": "pipe", "reference_id": "secret://local/workspace-a/key"},
            {
                "kind": "environment",
                "environment_name": "TOKEN",
                "reference_id": "secret://local/workspace-a/key",
                "value": "must-not-enter",
            },
            {
                "kind": "file",
                "target_path": "/run/secrets/password",
                "reference_id": "secret://local/workspace-a/key",
                "file_mode": "0400",
                "path_binding": {"environment_name": "PASSWORD_FILE", "extra": True},
            },
        )
        for descriptor in malformed:
            with self.subTest(descriptor=descriptor), self.assertRaises(SecretResolutionError):
                secret_delivery_from_descriptor(descriptor)


if __name__ == "__main__":
    unittest.main()
