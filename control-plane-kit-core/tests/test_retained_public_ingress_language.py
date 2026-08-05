from __future__ import annotations

from dataclasses import replace
import unittest

from control_plane_kit_core.planning import (
    DEFAULT_ACTIVITY_PLAN_CODEC,
    ActivityId,
    ActivityImpact,
    ActivityPlan,
    NonCompensatable,
    NonCompensatableReason,
    PlannedActivity,
    PlanViolationCode,
    PublicIngressActivityTarget,
    PublicIngressReservationTarget,
    ReleasePublicIngressReservation,
    RiskLevel,
    compensation_for_operation,
)
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    NamedPublicIngress,
    NamedPublicIngressCodec,
    PublicIngressConvergencePolicy,
    PublicIngressContractError,
    PublicIngressLifecycle,
    PublicIngressTarget,
)


def _ingress(**changes: object) -> NamedPublicIngress:
    value = NamedPublicIngress(
        ingress_id="gateway-public",
        authority_ref=IngressAuthorityReference("public-authority"),
        target=PublicIngressTarget("gateway", "control"),
        connector_node_id="cloudflared-gateway",
        hostname="gateway.example.com",
        readiness_check_id="ready",
        lifecycle=PublicIngressLifecycle.RETAINED,
    )
    return replace(value, **changes)


class RetainedPublicIngressLanguageTests(unittest.TestCase):
    def test_convergence_policy_is_distinct_canonical_ingress_data(self) -> None:
        policy = PublicIngressConvergencePolicy(
            attempt_timeout_seconds=7,
            retry_interval_seconds=11,
            maximum_elapsed_seconds=601,
        )
        ingress = replace(_ingress(), convergence=policy)

        descriptor = NamedPublicIngressCodec().encode(ingress)

        self.assertEqual(
            descriptor["convergence"],
            {
                "attempt_timeout_seconds": 7.0,
                "retry_interval_seconds": 11.0,
                "maximum_elapsed_seconds": 601.0,
            },
        )
        self.assertEqual(NamedPublicIngressCodec().decode(descriptor), ingress)
        for concrete_field in ("provider_kind", "dns_record_id", "tunnel_id"):
            self.assertNotIn(concrete_field, descriptor)

    def test_legacy_descriptor_defaults_convergence_but_unknown_shapes_fail_closed(
        self,
    ) -> None:
        descriptor = _ingress().descriptor()
        descriptor.pop("convergence", None)

        restored = NamedPublicIngressCodec().decode(descriptor)

        self.assertEqual(
            restored.convergence,
            PublicIngressConvergencePolicy(),
        )
        canonical = restored.descriptor()
        self.assertIn("convergence", canonical)

        with self.assertRaises(PublicIngressContractError):
            NamedPublicIngressCodec().decode(
                {
                    **canonical,
                    "convergence": {
                        **canonical["convergence"],
                        "provider_delay_seconds": 30,
                    },
                }
            )
        with self.assertRaises(PublicIngressContractError):
            NamedPublicIngressCodec().decode(
                {
                    **canonical,
                    "convergence": {
                        "attempt_timeout_seconds": 5,
                        "maximum_elapsed_seconds": 300,
                    },
                }
            )

    def test_convergence_policy_is_finite_and_scheduler_neutral(self) -> None:
        for values in (
            {"attempt_timeout_seconds": 0},
            {"retry_interval_seconds": 0},
            {"maximum_elapsed_seconds": 0},
            {"attempt_timeout_seconds": 301},
            {"retry_interval_seconds": 3601},
            {"maximum_elapsed_seconds": 86_401},
            {"attempt_timeout_seconds": float("nan")},
            {"retry_interval_seconds": float("inf")},
            {
                "attempt_timeout_seconds": 30,
                "maximum_elapsed_seconds": 20,
            },
        ):
            with self.subTest(values=values):
                with self.assertRaises(PublicIngressContractError):
                    PublicIngressConvergencePolicy(**values)
        self.assertNotIn("sleep", repr(PublicIngressConvergencePolicy()).lower())

    def test_explicit_reservation_release_is_closed_destructive_intent(self) -> None:
        operation = ReleasePublicIngressReservation(
            PublicIngressReservationTarget(
                "gateway-public",
                "reservation-gateway-public",
                1,
            )
        )
        plan = ActivityPlan(
            (
                PlannedActivity(
                    ActivityId("release-reservation"),
                    operation,
                    risk=RiskLevel.CRITICAL,
                    impact=ActivityImpact.DESTRUCTIVE,
                ),
            )
        )

        descriptor = DEFAULT_ACTIVITY_PLAN_CODEC.encode(plan)

        self.assertEqual(
            descriptor["activities"][0]["operation"],
            {
                "kind": "release-public-ingress-reservation",
                "target": {
                    "kind": "public-ingress-reservation",
                    "ingress_id": "gateway-public",
                    "reservation_id": "reservation-gateway-public",
                    "reservation_version": 1,
                },
            },
        )
        self.assertEqual(DEFAULT_ACTIVITY_PLAN_CODEC.decode(descriptor), plan)
        self.assertEqual(
            compensation_for_operation(operation),
            NonCompensatable(NonCompensatableReason.RESOURCE_REMOVAL),
        )

        with self.assertRaisesRegex(ValueError, "critical risk and destructive") as error:
            ActivityPlan(
                (
                    PlannedActivity(
                        ActivityId("unsafe-release"),
                        operation,
                    ),
                )
            )
        self.assertIn(
            PlanViolationCode.RESOURCE_RELEASE_SAFETY,
            {violation.code for violation in error.exception.violations},
        )


if __name__ == "__main__":
    unittest.main()
