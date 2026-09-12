"""#1794: observer connections never imply workload authority delivery."""

from dataclasses import fields, replace
import unittest

from control_plane_kit_core.operations import RunId
from control_plane_kit_core.planning import (
    ActivityId, NodeTarget, RuntimeTarget, StartNode, StartRuntime, StopNode, StopRuntime,
)
from control_plane_kit_core.runtime_authority import (
    RemoteDockerTlsConnectionAdmission, RuntimeAuthorityReference,
    RuntimeEffectContractError, runtime_connection_secret_uses,
)
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservationRequest, runtime_effect_intent_for_request,
    runtime_effect_intent_fingerprint,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectKind, RuntimeEffectRequest, RuntimeEffectSource,
)
from control_plane_kit_core.secrets import SecretReference, SecretUseIntent
from control_plane_kit_core.types import RuntimeKind
from test_runtime_effect_observation import _delivery, _grant, _grant_product


AUTHORITY = RuntimeAuthorityReference("remote-docker")


def connection(prefix="connection"):
    return RemoteDockerTlsConnectionAdmission(
        AUTHORITY,
        SecretReference(f"secret://private/{prefix}/ca"),
        SecretReference(f"secret://private/{prefix}/cert"),
        SecretReference(f"secret://private/{prefix}/key"),
    )


def grants_for(admission, fresh="a"):
    return tuple(
        _grant(
            fresh=chr(ord(fresh) + index), label="connection",
            reference=reference.reference_id, intent=intent,
        )
        for index, (reference, intent) in enumerate(
            runtime_connection_secret_uses(admission, authority_ref=AUTHORITY)
        )
    )


def request(*, operation=None, grants=(), products=(), deliveries=(), authority=AUTHORITY):
    return RuntimeEffectRequest(
        effect_id="event-started-a", kind=RuntimeEffectKind.REALIZE_ACTIVITY,
        runtime_kind=RuntimeKind.DOCKER,
        source=RuntimeEffectSource(
            workspace_id="workspace-a", request_id="request-a", run_id=RunId("run-a"),
            plan_id="plan-a", base_graph_id="graph-base", desired_graph_id="graph-desired",
            intent_event_id="event-started-a",
        ),
        activity_id=ActivityId("activity-a"),
        operation=operation or StartNode(NodeTarget("api")), authority_ref=authority,
        authority_deliveries=deliveries, secret_resolution_grants=grants,
        products=products,
    )


def observe(runtime_request, admission):
    if "connection_admission" not in {item.name for item in fields(RuntimeEffectObservationRequest)}:
        raise AssertionError("missing independent observation connection admission")
    return RuntimeEffectObservationRequest(runtime_request, connection_admission=admission)


class ObservationConnectionAdmissionTests(unittest.TestCase):
    def test_process_empty_node_runtime_and_teardown_admit_independent_connection(self):
        admission = connection()
        for operation in (
            StartNode(NodeTarget("api")), StartRuntime(RuntimeTarget("docker")),
            StopNode(NodeTarget("api")), StopRuntime(RuntimeTarget("docker")),
        ):
            with self.subTest(operation=operation):
                original = request(operation=operation, grants=grants_for(admission))
                observed = observe(original, admission)
                self.assertIs(observed.runtime_request, original)
                self.assertEqual(observed.connection_admission, admission)
                self.assertEqual(observed.intent.authority_deliveries, ())
                self.assertEqual(observed.intent, runtime_effect_intent_for_request(original))

    def test_process_delivery_cannot_admit_connection_grants_without_carrier(self):
        delivery = _delivery()
        material = replace(_grant_product(), runtime_authority_deliveries=(delivery,))
        grant = _grant(
            fresh="a", label="client-key", reference="secret://local/workspace-a/docker/client-key",
            intent=SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_KEY,
        )
        original = request(grants=(grant,), products=(material,), deliveries=(delivery,))
        with self.assertRaises(RuntimeEffectContractError):
            RuntimeEffectObservationRequest(original)

    def test_absent_carrier_and_foreign_or_malformed_carrier_reject(self):
        admission = connection()
        original = request(grants=grants_for(admission))
        for invalid in (
            None, "secret://private/not-a-carrier", {},
            replace(admission, authority_ref=RuntimeAuthorityReference("foreign")),
        ):
            with self.subTest(carrier_type=type(invalid).__name__):
                with self.assertRaises(RuntimeEffectContractError) as caught:
                    observe(original, invalid)
                self.assertNotIn("secret://", str(caught.exception))
                self.assertIsNone(caught.exception.__context__)
        with self.assertRaises(RuntimeEffectContractError):
            observe(request(authority=None), admission)

    def test_mixed_grants_validate_every_domain_without_discarding_unrelated_uses(self):
        admission = connection()
        tls = grants_for(admission)
        product = _grant_product()
        extra = tuple(
            _grant(fresh=chr(ord("d") + index), label="product-use", reference=reference, intent=intent)
            for index, (reference, intent) in enumerate((
                ("secret://local/workspace-a/app/token", SecretUseIntent.APPLICATION_CONTROL_TOKEN),
                ("secret://local/workspace-a/oci/pull", SecretUseIntent.OCI_PULL_CREDENTIAL),
                ("secret://local/workspace-a/postgres/password", SecretUseIntent.POSTGRES_PASSWORD),
            ))
        )
        original = request(grants=tls + extra, products=(product,))
        self.assertIs(observe(original, admission).runtime_request, original)
        bad = (
            tls + extra + (extra[0],),
            tls + extra + (tls[0],),
            tls + (replace(extra[0], reference=SecretReference("secret://private/unrelated")),),
            (replace(tls[0], reference=SecretReference("secret://private/unrelated")),) + extra,
            tls + (replace(extra[0], workspace_id="foreign-workspace"),),
            (replace(tls[0], effect_id="foreign-effect"),) + extra,
        )
        for supplied in bad:
            with self.subTest(count=len(supplied)):
                with self.assertRaises(RuntimeEffectContractError):
                    observe(replace(original, secret_resolution_grants=supplied), admission)

    def test_fresh_grants_and_rotated_private_carrier_preserve_committed_identity(self):
        original = request()
        intent = runtime_effect_intent_for_request(original)
        plain = RuntimeEffectObservationRequest(original)
        for admission, fresh in ((connection(), "a"), (connection(), "d"), (connection("rotated"), "g")):
            observed = observe(replace(original, secret_resolution_grants=grants_for(admission, fresh)), admission)
            self.assertEqual(observed.intent, intent)
            self.assertEqual(observed.request_fingerprint, runtime_effect_intent_fingerprint(intent))
            self.assertEqual(observed.descriptor(), plain.descriptor())
            for surface in (repr(observed), repr(observed.descriptor())):
                for reference, _ in runtime_connection_secret_uses(admission, authority_ref=AUTHORITY):
                    self.assertNotIn(reference.reference_id, surface)

    def test_default_local_and_partial_connection_context_remain_structural(self):
        local = RuntimeEffectObservationRequest(request(authority=None))
        self.assertEqual(local.intent.authority_deliveries, ())
        admission = connection()
        for supplied in ((), grants_for(admission)[:1]):
            self.assertEqual(observe(request(grants=supplied), admission).connection_admission, admission)


if __name__ == "__main__":
    unittest.main()
