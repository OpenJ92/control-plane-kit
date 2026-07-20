from dataclasses import dataclass
import unittest

from control_plane_kit import (
    EndpointMaterial,
    EndpointScope,
    HttpCheck,
    LiteralEndpointMaterial,
    Protocol,
    StaticVerificationInterpreter,
    VerificationCapability,
    VerificationCheckMaterial,
    VerificationDispatchError,
    VerificationIdentity,
    VerificationInterpreterRegistry,
    VerificationCompleted,
    VerificationOutcome,
    VerificationUnsupported,
)


def http_material() -> VerificationCheckMaterial:
    return VerificationCheckMaterial(
        "api",
        "graph-1",
        HttpCheck(
            check_id="semantic-http",
            provider_socket="internal",
            path="/verify",
        ),
        EndpointMaterial(
            "internal",
            Protocol.HTTP,
            EndpointScope.PRIVATE,
            LiteralEndpointMaterial("http://api:8080"),
        ),
    )


@dataclass(frozen=True)
class MismatchedInterpreter:
    @property
    def capabilities(self):
        return frozenset((VerificationCapability.HTTP,))

    def execute(self, material):
        return VerificationCompleted(
            VerificationIdentity("other", material.graph_id, material.check.check_id),
            VerificationCapability.HTTP,
            VerificationOutcome.PASSED,
            1,
        )


class VerificationDispatchTests(unittest.TestCase):
    def test_missing_capability_is_explicitly_unsupported(self) -> None:
        material = http_material()

        result = VerificationInterpreterRegistry({}).execute(material)

        self.assertIsInstance(result, VerificationUnsupported)
        self.assertIs(result.capability, VerificationCapability.HTTP)
        self.assertEqual(result.identity, VerificationIdentity("api", "graph-1", "semantic-http"))

    def test_static_interpreter_is_pure_deterministic_and_identity_checked(self) -> None:
        material = http_material()
        identity = VerificationIdentity("api", "graph-1", "semantic-http")
        expected = VerificationCompleted(
            identity,
            VerificationCapability.HTTP,
            VerificationOutcome.PASSED,
            1,
        )
        interpreter = StaticVerificationInterpreter(
            frozenset((VerificationCapability.HTTP,)),
            {identity: expected},
        )
        registry = VerificationInterpreterRegistry(
            {VerificationCapability.HTTP: interpreter}
        )

        self.assertEqual(registry.execute(material), expected)
        self.assertEqual(registry.execute(material), expected)
        self.assertEqual(expected.descriptor(), registry.execute(material).descriptor())

    def test_registry_rejects_capability_and_result_identity_lies(self) -> None:
        with self.assertRaises(VerificationDispatchError):
            VerificationInterpreterRegistry(
                {VerificationCapability.REDIS: MismatchedInterpreter()}
            )

        registry = VerificationInterpreterRegistry(
            {VerificationCapability.HTTP: MismatchedInterpreter()}
        )
        with self.assertRaises(VerificationDispatchError):
            registry.execute(http_material())


if __name__ == "__main__":
    unittest.main()
