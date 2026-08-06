from pathlib import Path
import unittest


DOC = Path(__file__).resolve().parents[1] / "docs" / "NODE_CONTROL_TOPOLOGY.md"


class NodeControlTopologyTests(unittest.TestCase):
    def test_prefix_and_compatibility_decision_is_frozen(self) -> None:
        text = DOC.read_text(encoding="utf-8")

        required = (
            "Canonical future workload-control prefix:",
            "/__control",
            "Bounded legacy descriptor compatibility:",
            "/__deploy",
            "No issue may silently publish two unauthenticated or divergent route families.",
        )

        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_sdk_boundary_and_single_variable_model_are_frozen(self) -> None:
        text = DOC.read_text(encoding="utf-8")

        required = (
            "control-plane-kit-server-sdk",
            "OpenJ92/control-plane-kit-server-sdk",
            "control-plane-kit-server-sdk[fastapi]",
            "ControlPlaneVariable[State, Command, Result]",
            "There is one public extension model:",
            "Rejected extension shapes:",
            "arbitrary object reflection",
            "a second durable domain-handler plugin interface",
        )

        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_trust_boundaries_and_grants_stay_distinct(self) -> None:
        text = DOC.read_text(encoding="utf-8")

        required = (
            "operator or agent -> cpk-server",
            "cpk-server -> gateway",
            "gateway -> workload",
            "Gateway transit grant:",
            "Workload end-to-end grant:",
            "substituting a gateway probe grant for a workload command grant",
            "gateway reachability grants workload command authority",
        )

        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_adopters_and_handoff_are_explicit(self) -> None:
        text = DOC.read_text(encoding="utf-8")

        required = (
            "http-active-router",
            "weighted HTTP load balancer",
            "service discovery",
            "#1148 should add pure provider-neutral contracts only:",
            "#1148 should not:",
            "rename live `/__deploy` descriptors",
            "mutate Cloudflare",
            "duplicate gateway key lifecycle",
        )

        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
