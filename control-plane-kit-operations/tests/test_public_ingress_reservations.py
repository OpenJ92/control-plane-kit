from __future__ import annotations

import concurrent.futures
import os
import threading
import unittest

import psycopg

import control_plane_kit_operations.ingress_authorities as ingress_authorities
import control_plane_kit_operations.planning as operations_planning
from control_plane_kit_core.operations import OperatorCommandKind
from control_plane_kit_core.planning import (
    ActivityImpact,
    ReleasePublicIngressReservation,
    RiskLevel,
)
from control_plane_kit_core.policies import ApprovalPolicy, PolicyScope
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    PublicIngressLifecycle,
)
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_core.types import WorkspaceLifecycle
from control_plane_kit_operations.postgres import (
    PostgresStoreBundle,
    PostgresUnitOfWork,
    install_schema,
)
from control_plane_kit_operations.read_services import InstanceReadService
from control_plane_kit_operations.records import GraphVersionRecord, WorkspaceRecord
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    OperationCommandService,
    StartOperationSession,
)


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class PublicIngressReservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not self.database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run the Docker gate."
            )
        self.connection = psycopg.connect(self.database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(
                    workspace_id="workspace-a",
                    name="Workspace A",
                    lifecycle=WorkspaceLifecycle.RUNNING,
                )
            )
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord(
                    workspace_id="workspace-b",
                    name="Workspace B",
                    lifecycle=WorkspaceLifecycle.RUNNING,
                )
            )
            unit_of_work.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def reservation(self, **changes):
        values = {
            "reservation_id": "reservation-001",
            "workspace_id": "workspace-a",
            "ingress_id": "gateway-001",
            "authority_ref": IngressAuthorityReference("openj92-public-ingress"),
            "provider_kind": ingress_authorities.IngressAuthorityProviderKind.CLOUDFLARE,
            "dns_record_id": "dns-001",
            "hostname": "cpk-gateway-001.openj92.dev",
            "zone_id": "zone-openj92",
            "lifecycle": PublicIngressLifecycle.RETAINED,
            "status": ingress_authorities.OwnedHostnameReservationStatus.BOUND,
            "created_at": "2026-08-05T18:10:00Z",
            "observed_at": "2026-08-05T18:10:01Z",
            "source_run_id": "run-001",
            "source_activity_id": "allocate-public-ingress:001",
            "source_event_id": "event-001",
        }
        values.update(changes)
        return ingress_authorities.CloudflareOwnedHostnameReservation(**values)

    def resource(self, **changes):
        values = {
            "workspace_id": "workspace-a",
            "runtime_id": "docker-a",
            "ingress_id": "gateway-001",
            "reservation_id": "reservation-001",
            "authority_ref": IngressAuthorityReference("openj92-public-ingress"),
            "provider_kind": ingress_authorities.IngressAuthorityProviderKind.CLOUDFLARE,
            "tunnel_name": "cpk-gateway-001",
            "tunnel_id": "tunnel-001",
            "dns_record_id": "dns-001",
            "hostname": "cpk-gateway-001.openj92.dev",
            "zone_id": "zone-openj92",
            "lifecycle": PublicIngressLifecycle.RETAINED,
            "created_at": "2026-08-05T18:10:00Z",
            "observed_at": "2026-08-05T18:10:01Z",
            "source_run_id": "run-001",
            "source_activity_id": "allocate-public-ingress:001",
            "source_event_id": "event-001",
        }
        values.update(changes)
        return ingress_authorities.CloudflareOwnedIngressResource(**values)

    def test_reservation_is_exact_bounded_secret_free_truth(self) -> None:
        descriptor = self.reservation().descriptor()

        self.assertEqual(descriptor["reservation_id"], "reservation-001")
        self.assertEqual(descriptor["status"], "bound")
        self.assertEqual(descriptor["dns_record_id"], "dns-001")
        self.assertNotIn("token", repr(descriptor).lower())
        self.assertNotIn("credential", repr(descriptor).lower())

    def test_store_is_idempotent_and_enforces_both_live_ownership_keys(self) -> None:
        reservation = self.reservation()
        with self.unit_of_work() as unit_of_work:
            store = unit_of_work.stores.ingress_reservations
            self.assertEqual(store.record_cloudflare(reservation), reservation)
            self.assertEqual(store.record_cloudflare(reservation), reservation)
            with self.assertRaises(ingress_authorities.OwnedHostnameReservationConflict):
                store.record_cloudflare(
                    self.reservation(
                        reservation_id="reservation-other",
                        dns_record_id="dns-other",
                    )
                )
            with self.assertRaises(ingress_authorities.OwnedHostnameReservationConflict):
                store.record_cloudflare(
                    self.reservation(
                        reservation_id="reservation-other-host-key",
                        ingress_id="gateway-other",
                    )
                )
            unit_of_work.commit()

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.ingress_reservations.require_cloudflare(
                    "workspace-a", "reservation-001"
                ),
                reservation,
            )
            self.assertEqual(
                unit_of_work.stores.ingress_reservations.list_cloudflare(
                    "workspace-a"
                ),
                (reservation,),
            )

    def test_reserved_transition_is_version_guarded_and_uncertainty_blocks(self) -> None:
        with self.unit_of_work() as unit_of_work:
            store = unit_of_work.stores.ingress_reservations
            store.record_cloudflare(self.reservation())
            reserved = store.mark_reserved(
                "workspace-a",
                "reservation-001",
                expected_version=1,
                transitioned_at="2026-08-05T18:12:00Z",
                source_run_id="run-remove",
                source_activity_id="remove-public-ingress:001",
                source_event_id="event-remove",
            )
            self.assertEqual(reserved.status.value, "reserved")
            self.assertEqual(reserved.version, 2)
            with self.assertRaises(ingress_authorities.OwnedHostnameReservationConflict):
                store.mark_reserved(
                    "workspace-a",
                    "reservation-001",
                    expected_version=1,
                    transitioned_at="later",
                    source_run_id="run-later",
                    source_activity_id="activity-later",
                    source_event_id="event-later",
                )
            uncertain = store.mark_uncertain(
                "workspace-a",
                "reservation-001",
                expected_version=2,
                transitioned_at="2026-08-05T18:13:00Z",
                source_run_id="run-uncertain",
                source_activity_id="activity-uncertain",
                source_event_id="event-uncertain",
            )
            self.assertEqual(uncertain.status.value, "uncertain")
            with self.assertRaises(ingress_authorities.OwnedHostnameReservationConflict):
                store.mark_releasing(
                    "workspace-a",
                    "reservation-001",
                    expected_version=3,
                    transitioned_at="2026-08-05T18:14:00Z",
                    source_run_id="run-release",
                    source_activity_id="activity-release",
                    source_event_id="event-release",
                )

    def test_tunnel_epochs_join_one_reservation_without_losing_history(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.ingress_reservations.record_cloudflare(
                self.reservation()
            )
            first = unit_of_work.stores.ingress_resources.record_cloudflare(
                self.resource()
            )
            removed = unit_of_work.stores.ingress_resources.mark_removed(
                "workspace-a",
                "gateway-001",
                removed_at="removed-at",
                removed_by_run_id="run-remove",
            )
            second = unit_of_work.stores.ingress_resources.record_cloudflare(
                self.resource(
                    tunnel_id="tunnel-002",
                    source_run_id="run-002",
                    source_activity_id="allocate-public-ingress:002",
                    source_event_id="event-002",
                )
            )
            unit_of_work.commit()

        self.assertEqual(first.epoch, 1)
        self.assertEqual(removed.reservation_id, "reservation-001")
        self.assertEqual(second.epoch, 2)
        self.assertEqual(second.reservation_id, "reservation-001")
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                len(unit_of_work.stores.ingress_reservations.list_cloudflare("workspace-a")),
                1,
            )
            self.assertEqual(
                len(unit_of_work.stores.ingress_resources.list_cloudflare("workspace-a")),
                2,
            )

    def test_reservation_cannot_become_reserved_until_realization_is_removed(
        self,
    ) -> None:
        with self.unit_of_work() as unit_of_work:
            reservations = unit_of_work.stores.ingress_reservations
            resources = unit_of_work.stores.ingress_resources
            reservations.record_cloudflare(self.reservation())
            resources.record_cloudflare(self.resource())
            with self.assertRaises(
                ingress_authorities.OwnedHostnameReservationConflict
            ):
                reservations.mark_reserved(
                    "workspace-a",
                    "reservation-001",
                    expected_version=1,
                    transitioned_at="2026-08-05T18:12:00Z",
                    source_run_id="run-remove",
                    source_activity_id="remove-public-ingress:001",
                    source_event_id="event-remove",
                )
            resources.mark_removed(
                "workspace-a",
                "gateway-001",
                removed_at="2026-08-05T18:12:01Z",
                removed_by_run_id="run-remove",
            )
            reserved = reservations.mark_reserved(
                "workspace-a",
                "reservation-001",
                expected_version=1,
                transitioned_at="2026-08-05T18:12:02Z",
                source_run_id="run-remove",
                source_activity_id="remove-public-ingress:001",
                source_event_id="event-removed",
            )
            unit_of_work.commit()

        self.assertIs(
            reserved.status,
            ingress_authorities.OwnedHostnameReservationStatus.RESERVED,
        )

    def test_reservation_write_rolls_back_with_caller_transaction(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "late failure"):
            with self.unit_of_work() as unit_of_work:
                unit_of_work.stores.ingress_reservations.record_cloudflare(
                    self.reservation()
                )
                raise RuntimeError("late failure")

        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.ingress_reservations.list_cloudflare("workspace-a"),
                (),
            )

    def test_concurrent_conflicting_reservations_produce_one_live_owner(self) -> None:
        ready = threading.Barrier(2)

        def record(reservation_id: str, dns_record_id: str) -> str:
            ready.wait(timeout=10)
            try:
                with self.unit_of_work() as unit_of_work:
                    unit_of_work.stores.ingress_reservations.record_cloudflare(
                        self.reservation(
                            reservation_id=reservation_id,
                            dns_record_id=dns_record_id,
                        )
                    )
                    unit_of_work.commit()
                return "recorded"
            except ingress_authorities.OwnedHostnameReservationConflict:
                return "conflict"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(
                future.result(timeout=15)
                for future in (
                    executor.submit(record, "reservation-a", "dns-a"),
                    executor.submit(record, "reservation-b", "dns-b"),
                )
            )

        self.assertEqual(sorted(outcomes), ["conflict", "recorded"])
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                len(unit_of_work.stores.ingress_reservations.list_cloudflare("workspace-a")),
                1,
            )

    def test_provider_neutral_read_survives_graph_absence(self) -> None:
        reserved = self._record_reserved_reservation()
        stores = PostgresStoreBundle(self.connection)
        service = InstanceReadService(
            workspace_store=stores.workspaces,
            graph_topology_store=stores.graphs,
            ingress_resource_store=stores.ingress_resources,
            ingress_reservation_store=stores.ingress_reservations,
        )

        descriptor = service.public_ingress_resources("workspace-a").descriptor()

        self.assertEqual(descriptor["items"][0]["reservation_id"], reserved.reservation_id)
        self.assertEqual(descriptor["items"][0]["status"], "reserved")
        self.assertEqual(descriptor["items"][0]["realizations"], [])
        self.assertNotIn("cloudflare", repr(descriptor).lower())
        self.assertNotIn("dns_record_id", repr(descriptor).lower())
        self.assertNotIn("tunnel_id", repr(descriptor).lower())
        self.assertNotIn("token", repr(descriptor).lower())

    def test_release_command_creates_exact_destructive_plan_without_effects(self) -> None:
        reserved = self._record_reserved_reservation()
        workspace = self._seed_quiescent_graph_and_session()
        service = operations_planning.PublicIngressReservationReleasePlanningService(
            self.unit_of_work,
            clock=lambda: "2026-08-05T18:20:00Z",
            id_factory=Sequence("plan-release", "action-release"),
        )
        command = operations_planning.RequestPublicIngressReservationRelease(
            session_id="session-release",
            workspace_id="workspace-a",
            actor_id="operator-a",
            ingress_id="gateway-001",
            reservation_id=reserved.reservation_id,
            expected_reservation_version=reserved.version,
            expected_current_graph_id=workspace.current_graph_id,
            expected_current_realized_projection_id=(
                workspace.current_realized_projection_id
            ),
            expected_desired_graph_revision=workspace.desired_graph_revision,
            idempotency_key=IdempotencyKey("release-reservation"),
        )

        result = service.execute(command)
        replay = service.execute(command)

        self.assertFalse(result.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.plan_record, result.plan_record)
        activity = result.plan_record.plan.activities[0]
        self.assertIsInstance(activity.operation, ReleasePublicIngressReservation)
        self.assertEqual(activity.operation.target.ingress_id, "gateway-001")
        self.assertEqual(activity.operation.target.reservation_id, "reservation-001")
        self.assertEqual(activity.operation.target.reservation_version, 2)
        self.assertIs(activity.risk, RiskLevel.CRITICAL)
        self.assertIs(activity.impact, ActivityImpact.DESTRUCTIVE)
        requirement = ApprovalPolicy().requirement_for(result.plan_record.plan)
        self.assertIs(requirement.required_scope, PolicyScope.PLAN_APPROVE_DESTRUCTIVE)
        self.assertTrue(requirement.destructive)
        self.assertIs(
            result.action.action_type,
            OperatorCommandKind.REQUEST_PUBLIC_INGRESS_RESERVATION_RELEASE,
        )
        self.assertEqual(result.action.payload["reservation_id"], "reservation-001")

    def test_release_command_rejects_uncertain_or_replaced_truth(self) -> None:
        self._record_reserved_reservation()
        workspace = self._seed_quiescent_graph_and_session()
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.ingress_reservations.mark_uncertain(
                "workspace-a",
                "reservation-001",
                expected_version=2,
                transitioned_at="2026-08-05T18:19:00Z",
                source_run_id="run-uncertain",
                source_activity_id="activity-uncertain",
                source_event_id="event-uncertain",
            )
            unit_of_work.commit()
        service = operations_planning.PublicIngressReservationReleasePlanningService(
            self.unit_of_work,
            clock=lambda: "2026-08-05T18:20:00Z",
            id_factory=Sequence("unused-plan", "unused-action"),
        )

        with self.assertRaises(
            operations_planning.PublicIngressReservationReleasePlanningConflict
        ):
            service.execute(
                operations_planning.RequestPublicIngressReservationRelease(
                    session_id="session-release",
                    workspace_id="workspace-a",
                    actor_id="operator-a",
                    ingress_id="gateway-001",
                    reservation_id="reservation-001",
                    expected_reservation_version=2,
                    expected_current_graph_id=workspace.current_graph_id,
                    expected_current_realized_projection_id=(
                        workspace.current_realized_projection_id
                    ),
                    expected_desired_graph_revision=workspace.desired_graph_revision,
                    idempotency_key=IdempotencyKey("release-uncertain"),
                )
            )

    def _record_reserved_reservation(self):
        with self.unit_of_work() as unit_of_work:
            store = unit_of_work.stores.ingress_reservations
            store.record_cloudflare(self.reservation())
            reserved = store.mark_reserved(
                "workspace-a",
                "reservation-001",
                expected_version=1,
                transitioned_at="2026-08-05T18:12:00Z",
                source_run_id="run-remove",
                source_activity_id="remove-public-ingress:001",
                source_event_id="event-remove",
            )
            unit_of_work.commit()
            return reserved

    def _seed_quiescent_graph_and_session(self):
        with self.unit_of_work() as unit_of_work:
            graph = GraphVersionRecord.from_graph(
                graph_id="graph-empty",
                workspace_id="workspace-a",
                version=1,
                graph=DeploymentGraph("empty"),
                created_by="operator-a",
                created_at="2026-08-05T18:15:00Z",
            )
            unit_of_work.stores.graphs.save(graph)
            unit_of_work.stores.workspaces.set_current_graph(
                "workspace-a", graph.graph_id
            )
            workspace = unit_of_work.stores.workspaces.set_desired_graph(
                "workspace-a", graph.graph_id
            )
            unit_of_work.commit()
        OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-05T18:16:00Z",
            id_factory=Sequence("session-release", "action-start-release"),
        ).execute(
            StartOperationSession(
                workspace_id="workspace-a",
                actor_id="operator-a",
                title="Release retained hostname",
                idempotency_key=IdempotencyKey("start-release"),
            )
        )
        return workspace


if __name__ == "__main__":
    unittest.main()
