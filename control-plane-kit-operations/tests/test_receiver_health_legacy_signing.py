"""Retained V1 is readable history, never a successor live signing pair."""

import unittest

from control_plane_kit_operations.health_effect_preparations import HealthEffectPreparationCodec, HealthEffectPreparationRecord
from control_plane_kit_operations.health_signing_authority import (
    GatewayNodeHealthReadTransitSigningAuthority, WorkloadNodeHealthReadSigningAuthority,
    HealthSigningAuthorityPair, HealthSigningAuthorityError,
)
from control_plane_kit_operations.secret_providers import secret_resolution_grant_for
from tests.postgres_health_effect_preparation_fixture import PostgresHealthEffectPreparationFixture


class ReceiverHealthLegacySigningTests(PostgresHealthEffectPreparationFixture, unittest.TestCase):
    def test_retained_v1_stays_exact_but_cannot_construct_live_signing_authority(self):
        # This is explicitly recorded original V1 owner history. It never
        # starts a new V1 attempt and is not a fresh-admission positive.
        original = self.persist_health()
        self.assertIs(type(original), HealthEffectPreparationRecord)
        codec = HealthEffectPreparationCodec()
        encoded = codec.encode_canonical_bytes(original)
        transit = GatewayNodeHealthReadTransitSigningAuthority(self.keys["transit"].public_key,
            secret_resolution_grant_for(self.uses["transit"], provider=self.provider))
        workload = WorkloadNodeHealthReadSigningAuthority(self.keys["workload"].public_key,
            secret_resolution_grant_for(self.uses["workload"], provider=self.provider))
        # Both family values have real exact registration/use witnesses. This
        # public live-pair type boundary has no key-status, run-status or clock
        # checks that could hide failure to close V1. Initial red may therefore
        # reach it independently of R2; it must be classified separately.
        with self.assertRaises(HealthSigningAuthorityError):
            HealthSigningAuthorityPair(original, transit, workload)
        with self.unit_of_work() as uow:
            restored = uow.stores.health_effect_preparations.get(original.identity)
        self.assertIs(type(restored), HealthEffectPreparationRecord)
        self.assertEqual(restored, original)
        self.assertEqual(codec.encode_canonical_bytes(restored), encoded)
