import importlib
from pathlib import Path
import sys
import unittest

from control_plane_kit_core.operations import (
    ControlPlaneServiceRole,
    CpkServerEntrypointHandoffContract,
    EntrypointCompositionPolicy,
    ProcessStatePolicy,
)


ROOT = Path(__file__).resolve().parents[3]
PRODUCT_SRC = ROOT / "products" / "cpk_server" / "src"


class CpkServerProcessCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(PRODUCT_SRC))

    def tearDown(self) -> None:
        sys.path.remove(str(PRODUCT_SRC))
        for name in list(sys.modules):
            if name == "control_plane_kit_servers_cpk_server" or name.startswith(
                "control_plane_kit_servers_cpk_server."
            ):
                sys.modules.pop(name, None)

    def test_product_local_law_cards_assign_813_owned_laws(self) -> None:
        import json

        cards = json.loads(
            (ROOT / "products" / "cpk_server" / "law-cards" / "extract-f-813.json")
            .read_text(encoding="utf-8")
        )

        self.assertEqual(cards["schema"], "cpk-server.extract-f-law-cards")
        self.assertEqual(cards["issue"], "#813")
        self.assertEqual(
            [card["law"] for card in cards["law_cards"]],
            [
                "behavior.execution-mode-requires-auth-configuration",
                "behavior.execution-mutations-require-identity-replay-and-conflict",
                "behavior.observer-mutation-updates-observer-state",
                "behavior.replacing-targets-clears-stale-active-target",
            ],
        )
        self.assertTrue(
            all(card["owner"] == "control-plane-kit-servers/cpk_server" for card in cards["law_cards"])
        )

    def test_composition_root_consumes_core_handoff_and_one_program_boundary(self) -> None:
        from control_plane_kit_servers_cpk_server import (
            CpkServerProcessConfiguration,
            create_cpk_server_composition,
        )

        composition = create_cpk_server_composition(
            CpkServerProcessConfiguration.execution_capable(token_configured=True)
        )

        self.assertIsInstance(composition.handoff, CpkServerEntrypointHandoffContract)
        self.assertEqual(
            composition.handoff.composition_policy,
            EntrypointCompositionPolicy.ONE_DEPLOYMENT_PROGRAM,
        )
        self.assertEqual(
            composition.handoff.state_policy,
            ProcessStatePolicy.PROCESS_GLOBALS_ARE_NOT_TRUTH,
        )
        self.assertIs(composition.program, composition.handoff.program)
        self.assertIs(composition.http_api, composition.handoff.http_api)
        self.assertIs(composition.mcp, composition.handoff.mcp)
        self.assertEqual(
            composition.service_binding(ControlPlaneServiceRole.PLANNING).service_name,
            "planning-service",
        )
        self.assertEqual(composition.command_identity_policy, "single-application-boundary")

    def test_execution_capable_composition_requires_auth_configuration(self) -> None:
        from control_plane_kit_servers_cpk_server import (
            CpkServerCompositionError,
            CpkServerProcessConfiguration,
            create_cpk_server_composition,
        )

        with self.assertRaisesRegex(CpkServerCompositionError, "auth configuration"):
            create_cpk_server_composition(
                CpkServerProcessConfiguration.execution_capable(token_configured=False)
            )

        local = create_cpk_server_composition(CpkServerProcessConfiguration.local_read_only())
        self.assertFalse(local.configuration.execution_enabled)

    def test_observer_and_target_mutation_is_process_state_not_graph_truth(self) -> None:
        from control_plane_kit_servers_cpk_server import CpkServerProcessState

        state = CpkServerProcessState(targets=("blue", "green"), active_target="blue")
        observed = state.record_observer("obs-a", {"status": "ready"})
        switched = observed.switch_active_target("green")
        replaced = switched.replace_targets(("green", "purple"))
        cleared = switched.replace_targets(("purple",))

        self.assertEqual(state.observers, ())
        self.assertEqual(observed.observers[0].observer_id, "obs-a")
        self.assertEqual(switched.active_target, "green")
        self.assertEqual(replaced.active_target, "green")
        self.assertIsNone(cleared.active_target)
        self.assertEqual(cleared.graph_truth_policy, "process-state-never-owns-graph-truth")

    def test_unknown_targets_fail_closed(self) -> None:
        from control_plane_kit_servers_cpk_server import (
            CpkServerProcessState,
            UnknownTargetError,
        )

        state = CpkServerProcessState(targets=("blue",), active_target="blue")

        with self.assertRaisesRegex(UnknownTargetError, "unknown target"):
            state.switch_active_target("green")

    def test_root_catalogue_import_does_not_import_cpk_server_product(self) -> None:
        sys.path.insert(0, str(ROOT / "src"))
        try:
            import control_plane_kit_servers

            catalogue = control_plane_kit_servers.load_catalogue()
            self.assertEqual(
                [item.product_id for item in catalogue],
                [
                    "cpk-server",
                    "cpk-server-docker",
                    "hello-server",
                    "http-active-router",
                    "http-multiplexer",
                    "postgres-server",
                ],
            )
            self.assertNotIn("control_plane_kit_servers_cpk_server", sys.modules)
            self.assertNotIn("control_plane_kit_servers_hello_server", sys.modules)
            self.assertNotIn(
                "control_plane_kit_servers_http_active_router",
                sys.modules,
            )
            self.assertNotIn(
                "control_plane_kit_servers_http_multiplexer",
                sys.modules,
            )
        finally:
            sys.path.remove(str(ROOT / "src"))
            sys.modules.pop("control_plane_kit_servers", None)
            sys.modules.pop("control_plane_kit_servers.catalogue", None)

    def test_core_import_does_not_import_cpk_server_product(self) -> None:
        import control_plane_kit_core

        self.assertIsNotNone(control_plane_kit_core.CpkServerEntrypointHandoffContract)
        self.assertNotIn("control_plane_kit_servers_cpk_server", sys.modules)

    def test_hello_product_cannot_satisfy_cpk_server_laws(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("control_plane_kit_servers_hello")


if __name__ == "__main__":
    unittest.main()
