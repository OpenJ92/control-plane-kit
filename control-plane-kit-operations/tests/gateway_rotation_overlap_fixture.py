from __future__ import annotations

from control_plane_kit_core.algebra import BlockSockets, BlockSpec
from control_plane_kit_core.delegation_authority import (
    DelegationAuthorityBinding,
    DelegationVerifierProjection,
    materialize_delegation_verifiers,
)
from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.topology import DeploymentGraph, Node, RuntimeRecord
from control_plane_kit_core.types import BlockFamily, RuntimeKind
from control_plane_kit_operations.approvals import (
    ApprovalCommandService,
    DecideApproval,
    RequestGatewayKeyRotationApproval,
)
from control_plane_kit_operations.delegation_signing_keys import (
    RegisteredDelegationSigningKey,
    RegisteredDelegationSigningKeyStatus,
    delegation_signing_key_registration_id_for,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotation,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
    RequestGatewayKeyRotation,
)
from control_plane_kit_operations.records import (
    ApprovalDecisionKind,
    GraphVersionRecord,
    RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
    WorkspaceRecord,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    OperationCommandService,
    StartOperationSession,
)


PUBLIC_KEY_A = """-----BEGIN PUBLIC KEY-----
QUFB
-----END PUBLIC KEY-----
"""
PUBLIC_KEY_B = """-----BEGIN PUBLIC KEY-----
QkJC
-----END PUBLIC KEY-----
"""
PUBLIC_KEY_OTHER = """-----BEGIN PUBLIC KEY-----
T1RIRVI=
-----END PUBLIC KEY-----
"""


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def __call__(self) -> str:
        return next(self._values)


class SimulatedProcessLoss(BaseException):
    pass


class CrashControl:
    def __init__(self, crash_after_commit: int) -> None:
        self.crash_after_commit = crash_after_commit
        self.commits = 0


class CrashAfterCommitUnitOfWork:
    """Simulate process loss only after the physical Postgres commit succeeds."""

    def __init__(self, inner: PostgresUnitOfWork, control: CrashControl) -> None:
        self.inner = inner
        self.control = control
        self.commit_requested = False

    def __enter__(self) -> "CrashAfterCommitUnitOfWork":
        self.inner.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool | None:
        result = self.inner.__exit__(exc_type, exc, traceback)
        if exc_type is None and self.commit_requested:
            self.control.commits += 1
            if self.control.commits == self.control.crash_after_commit:
                raise SimulatedProcessLoss(
                    "simulated process loss after durable commit"
                )
        return result

    @property
    def stores(self):
        return self.inner.stores

    def commit(self) -> None:
        self.inner.commit()
        self.commit_requested = True

    def rollback(self) -> None:
        self.inner.rollback()


class GatewayRotationOverlapFixture:
    """Shared real-store fixture for exact A -> A+B rotation tests."""

    def seed_graph_and_keys(
        self,
        *,
        include_replacement_key: bool = True,
    ) -> None:
        authored = self.authored_graph()
        realized_a = materialize_delegation_verifiers(
            authored,
            (
                self.projection(
                    "gateway-a",
                    "projection-key-a",
                    self.public_key("key-a", PUBLIC_KEY_A),
                ),
                self.projection(
                    "gateway-other",
                    "projection-other",
                    self.public_key("key-other", PUBLIC_KEY_OTHER),
                ),
            ),
        )
        authored_record = GraphVersionRecord.from_graph(
            graph_id="graph-a",
            workspace_id="workspace-a",
            version=1,
            graph=authored,
            created_by="operator-a",
            created_at="2026-08-02T01:00:00Z",
        )
        projection_a = RealizedGraphProjectionRecord.from_graph(
            projection_id="projection-a",
            workspace_id="workspace-a",
            source_authored_graph_id="graph-a",
            projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
            projection_key="seed-a",
            graph=realized_a,
            created_by="operator-a",
            created_at="2026-08-02T01:00:00Z",
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.workspaces.create(WorkspaceRecord("workspace-a", "Workspace A"))
            stores.graphs.save(authored_record)
            stores.realized_graphs.save(projection_a)
            stores.workspaces.set_current_graph(
                "workspace-a", "graph-a", "projection-a"
            )
            stores.workspaces.set_desired_graph(
                "workspace-a", "graph-a", "projection-a"
            )
            stores.delegation_signing_keys.register(
                self.signing_key("key-a", PUBLIC_KEY_A)
            )
            stores.delegation_signing_keys.activate(
                "workspace-a",
                DelegationKeyPurpose.GATEWAY_PROBE,
                "cpk-server",
                "key-a",
                activated_by="operator-a",
                activated_at="2026-08-02T01:00:01Z",
            )
            if include_replacement_key:
                stores.delegation_signing_keys.register(
                    self.signing_key("key-b", PUBLIC_KEY_B)
                )
            unit_of_work.commit()

    def seed_rotation_approval(self) -> None:
        OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-02T01:01:00Z",
            id_factory=Sequence("rotation-session", "rotation-session-action"),
        ).execute(
            StartOperationSession(
                "workspace-a",
                "operator-a",
                "Rotate gateway key",
                IdempotencyKey("start-rotation-session"),
            )
        )
        rotations = GatewayKeyRotationService(self.unit_of_work, clock=lambda: 1_000)
        requested = rotations.request(
            RequestGatewayKeyRotation(
                workspace_id="workspace-a",
                gateway_node_id="gateway-a",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server",
                old_key_id="key-a",
                new_secret_reference=SecretReference(
                    "secret://workspace-secrets/keys/key-b"
                ),
                key_generation_correlation="generate-key-b",
                maximum_grant_lifetime_seconds=60,
                clock_skew_seconds=5,
                correlation_id="rotation-a",
                requested_by="operator-a",
                requested_at="2026-08-02T01:01:01Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            )
        )
        approvals = ApprovalCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-02T01:01:02Z",
            id_factory=Sequence(
                "rotation-approval-request",
                "rotation-approval-request-action",
                "rotation-approval-decision",
                "rotation-approval-decision-action",
            ),
        )
        approval = approvals.execute(
            RequestGatewayKeyRotationApproval(
                session_id="rotation-session",
                rotation_id=requested.rotation_id,
                actor_id="operator-a",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                idempotency_key=IdempotencyKey("request-rotation-approval"),
            )
        )
        awaiting = rotations.advance(
            AdvanceGatewayKeyRotation(
                requested.rotation_id,
                "await-approval",
                requested.status,
                requested.version,
                GatewayKeyRotationStatus.AWAITING_APPROVAL,
                "operator-a",
                "2026-08-02T01:01:03Z",
                (PolicyScope.DELEGATION_KEY_ROTATE,),
                approval_request_id=approval.request.request_id,
            )
        )
        decision = approvals.execute(
            DecideApproval(
                session_id="rotation-session",
                request_id=approval.request.request_id,
                actor_id="manager-a",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE,),
                decision=ApprovalDecisionKind.APPROVED,
                idempotency_key=IdempotencyKey("approve-rotation"),
            )
        )
        approved = rotations.advance(
            AdvanceGatewayKeyRotation(
                awaiting.rotation_id,
                "approve-rotation",
                awaiting.status,
                awaiting.version,
                GatewayKeyRotationStatus.APPROVED,
                "operator-a",
                "2026-08-02T01:01:04Z",
                (PolicyScope.DELEGATION_KEY_ROTATE,),
                approval_decision_id=decision.decision.decision_id,
            )
        )
        prepared = rotations.advance(
            AdvanceGatewayKeyRotation(
                approved.rotation_id,
                "prepare-generation",
                approved.status,
                approved.version,
                GatewayKeyRotationStatus.GENERATION_PREPARED,
                "operator-a",
                "2026-08-02T01:01:05Z",
                (PolicyScope.DELEGATION_KEY_ROTATE,),
                generation_provider_registration_id="provider-a",
                generation_action_digest="a" * 64,
            )
        )
        generated = rotations.advance(
            AdvanceGatewayKeyRotation(
                prepared.rotation_id,
                "complete-generation",
                prepared.status,
                prepared.version,
                GatewayKeyRotationStatus.KEY_GENERATED,
                "operator-a",
                "2026-08-02T01:01:06Z",
                (PolicyScope.DELEGATION_KEY_ROTATE,),
                new_key_id="key-b",
                new_secret_version_id="version-b",
                new_secret_version_number=1,
            )
        )
        self.rotation_id = generated.rotation_id
        self.rotation_version = generated.version
        self.approval_request_id = approval.request.request_id
        self.approval_decision_id = decision.decision.decision_id
        self.approval_review_digest = approval.request.subject.review_digest

    @staticmethod
    def authored_graph() -> DeploymentGraph:
        bindings = tuple(
            DelegationAuthorityBinding(
                node_id,
                DelegationKeyPurpose.GATEWAY_PROBE,
                "cpk-server",
            )
            for node_id in ("gateway-a", "gateway-other")
        )
        nodes = {
            node_id: Node(
                node_id=node_id,
                block_family=BlockFamily.PROXY,
                block_spec=BlockSpec(node_id),
                kind="container-server",
                runtime_id="docker",
                sockets=BlockSockets(),
            )
            for node_id in ("gateway-a", "gateway-other")
        }
        return DeploymentGraph(
            "gateway-island",
            nodes=nodes,
            runtimes={
                "docker": RuntimeRecord(
                    "docker",
                    RuntimeKind.DOCKER,
                    children=tuple(nodes),
                )
            },
            delegation_authorities=bindings,
        )

    @staticmethod
    def public_key(key_id: str, pem: str) -> DelegationPublicKey:
        return DelegationPublicKey(key_id, DelegationKeyAlgorithm.ED25519, pem)

    @classmethod
    def projection(
        cls,
        node_id: str,
        projection_id: str,
        key: DelegationPublicKey,
    ) -> DelegationVerifierProjection:
        return DelegationVerifierProjection(
            node_id,
            DelegationKeyPurpose.GATEWAY_PROBE,
            "cpk-server",
            f"gateway:workspace-a:{node_id}",
            projection_id,
            (key,),
        )

    @classmethod
    def signing_key(
        cls,
        key_id: str,
        pem: str,
    ) -> RegisteredDelegationSigningKey:
        public_key = cls.public_key(key_id, pem)
        private_reference = SecretReference(
            f"secret://workspace-secrets/keys/{key_id}"
        )
        return RegisteredDelegationSigningKey(
            registration_id=delegation_signing_key_registration_id_for(
                workspace_id="workspace-a",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server",
                public_key=public_key,
                private_key_reference=private_reference,
            ),
            workspace_id="workspace-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            public_key=public_key,
            private_key_reference=private_reference,
            admitted_by="operator-a",
            admitted_at="2026-08-02T01:00:00Z",
            status=RegisteredDelegationSigningKeyStatus.VERIFY_ONLY,
        )
