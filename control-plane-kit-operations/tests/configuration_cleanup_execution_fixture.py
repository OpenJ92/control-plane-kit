"""Public cleanup execution after the existing real receiver/D1 chronology."""
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.admission import ExecutionAdmissionCommandService
from tests.configuration_cleanup_read_ceilings_fixture import ConfigurationCleanupReadCeilingsFixture
from tests.test_execution_admission import Sequence


class ConfigurationCleanupExecutionFixture(ConfigurationCleanupReadCeilingsFixture):
    def prepare_cleanup_execution(self):
        # The existing reader fixture stops immediately after real publication
        # and destructive approval; its recorded request/run suffix is excluded.
        self.prepare_ceiling_premise(recorded_cleanup=False)
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_execution_requests WHERE plan_id=%s",
            (self.plan.plan_id,)).fetchone(), (0,))
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_activity_runs WHERE plan_id=%s",
            (self.plan.plan_id,)).fetchone(), (0,))
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_configuration_cleanup_reservations").fetchone(), (0,))
        self.assertEqual(self.plan.plan.activities[0].operation.instances, (self.selected_ref,))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.configuration_completions.get(self.source_identity), self.completion)
        self.assert_registration_unchanged()

    def admit_cleanup(self, suffix="cleanup-execution"):
        return ExecutionAdmissionCommandService(self.unit_of_work, clock=self.now,
            id_factory=Sequence("execution-" + suffix, "action-execute-" + suffix)).execute(self.command(
                plan_id=self.plan.plan_id, approval_request_id=self.approval.request_id,
                scopes=tuple(PolicyScope), key="execute-" + suffix))
