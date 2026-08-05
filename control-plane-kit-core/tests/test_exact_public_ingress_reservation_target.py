from __future__ import annotations

import unittest

import control_plane_kit_core.planning as planning


class ExactPublicIngressReservationTargetTests(unittest.TestCase):
    def test_release_plan_pins_provider_neutral_reservation_identity(self) -> None:
        target = planning.PublicIngressReservationTarget(
            ingress_id="gateway-001",
            reservation_id="reservation-001",
            reservation_version=2,
        )
        plan = planning.ActivityPlan(
            (
                planning.PlannedActivity(
                    planning.ActivityId("release-reservation-001"),
                    planning.ReleasePublicIngressReservation(target),
                    risk=planning.RiskLevel.CRITICAL,
                    impact=planning.ActivityImpact.DESTRUCTIVE,
                ),
            )
        )

        descriptor = planning.DEFAULT_ACTIVITY_PLAN_CODEC.encode(plan)
        decoded = planning.DEFAULT_ACTIVITY_PLAN_CODEC.decode(descriptor)

        self.assertEqual(decoded, plan)
        self.assertEqual(
            descriptor["activities"][0]["operation"],
            {
                "kind": "release-public-ingress-reservation",
                "target": {
                    "kind": "public-ingress-reservation",
                    "ingress_id": "gateway-001",
                    "reservation_id": "reservation-001",
                    "reservation_version": 2,
                },
            },
        )

    def test_release_target_rejects_missing_exact_identity(self) -> None:
        with self.assertRaises(ValueError):
            planning.PublicIngressReservationTarget("gateway-001", "", 1)


if __name__ == "__main__":
    unittest.main()
