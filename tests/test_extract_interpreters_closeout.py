from __future__ import annotations

from pathlib import Path
import unittest

from extraction_parity.validation import read_bounded_json


ARTIFACT_ROOT = Path(__file__).parents[1] / "artifacts" / "extraction"


class ExtractInterpretersCloseoutTests(unittest.TestCase):
    def report(self) -> dict[str, object]:
        return read_bounded_json(
            ARTIFACT_ROOT / "extract-interpreters-closeout-report.json"
        )

    def test_closeout_records_runtime_interpreter_algebra(self) -> None:
        report = self.report()

        self.assertEqual(report["schema"], "cpk.extract-interpreters-closeout")
        self.assertEqual(report["parent"], "#907")
        self.assertEqual(report["issue"], "#910")
        self.assertEqual(report["status"], "closeout-ready")
        self.assertEqual(
            report["topology"]["canonical_shape"],
            [
                "core: RuntimeEffectRequest",
                "interpreter: RuntimeEffectRequest -> IO RuntimeEffectResult",
                "operations: ActivityJournal x RuntimeEffectResult -> ActivityJournal'",
            ],
        )
        self.assertEqual(
            report["topology"]["runtime_composition"],
            [
                "cpk-server",
                "configured operations application",
                "ExecutionCoordinator",
                "RuntimeInterpreterDispatcher",
                "DockerRuntimeInterpreter",
                "Python Docker SDK",
            ],
        )

    def test_package_ownership_keeps_docker_sdk_out_of_operations_and_core(self) -> None:
        ownership = self.report()["package_ownership"]

        self.assertIn("RuntimeEffectRequest", ownership["control-plane-kit-core"])
        self.assertIn(
            "RuntimeInterpreterDispatcher protocol",
            ownership["control-plane-kit-operations"],
        )
        self.assertIn(
            "DockerSdkClient",
            ownership["control-plane-kit-interpreters"],
        )
        self.assertIn(
            "runtime interpreter selection at bootstrap",
            ownership["control-plane-kit-servers/cpk-server"],
        )
        self.assertNotIn(
            "Python Docker SDK dependency",
            ownership["control-plane-kit-operations"],
        )
        self.assertNotIn(
            "DockerRuntimeInterpreter",
            ownership["control-plane-kit-core"],
        )

    def test_published_acceptance_evidence_is_digest_pinned(self) -> None:
        published = self.report()["published_cpk_server"]

        self.assertEqual(published["server_pr"], "OpenJ92/control-plane-kit-servers#22")
        self.assertEqual(
            published["image"],
            "ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:def866baeeda659d61a821a29a07a8ceb780bcb440ab7fe0c63a8fa8989e7c7a",
        )
        self.assertEqual(
            published["descriptor_sha256"],
            "10dafb59f3d98a527e9dc39fe87ab93668774afc8ee5b688bf663bdb1553c159",
        )
        self.assertEqual(
            published["catalogue_checksum"],
            "1c3d0dd880caf0b2a065a80403d326db8dd47358e2418afa712f7af0818c4bfc",
        )

    def test_closeout_keeps_future_runtime_work_deferred(self) -> None:
        report = self.report()
        deferred = set(report["deferred"])

        self.assertIn("recursive cpk-server acceptance", deferred)
        self.assertIn("future control portals / ingress", deferred)
        self.assertIn("cloud runtime interpreters", deferred)
        self.assertIn("larger topology stress tests", deferred)
        self.assertIn("frontend work", deferred)
        self.assertIn(
            "hosted acceptance controller talks to cpk-server over public HTTP/MCP routes and does not import operations internals",
            report["boundary_proofs"],
        )


if __name__ == "__main__":
    unittest.main()
