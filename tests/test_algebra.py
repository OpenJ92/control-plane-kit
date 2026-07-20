from unittest import TestCase, main

from control_plane_kit import Protocol, RequirementSocket, ProviderSocket, BlockSockets
from control_plane_kit.core.types import SocketBinding


class AlgebraTests(TestCase):
    def test_socket_names_are_accessible(self):
        sockets = BlockSockets(
            requirements=(RequirementSocket("DATABASE_URL", Protocol.POSTGRES, ("DATABASE_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        )

        self.assertEqual(sockets.requirement("DATABASE_URL").protocol, Protocol.POSTGRES)
        self.assertEqual(sockets.provider("internal").protocol, Protocol.HTTP)

    def test_requirement_socket_requires_env_binding(self):
        with self.assertRaises(ValueError):
            RequirementSocket("database", Protocol.POSTGRES, ())

    def test_runtime_control_socket_forbids_startup_environment_binding(self):
        socket = RequirementSocket(
            "active",
            Protocol.HTTP,
            (),
            binding=SocketBinding.RUNTIME_CONTROL,
        )

        self.assertEqual(socket.binding, SocketBinding.RUNTIME_CONTROL)
        with self.assertRaises(ValueError):
            RequirementSocket(
                "active",
                Protocol.HTTP,
                ("ACTIVE_URL",),
                binding=SocketBinding.RUNTIME_CONTROL,
            )


if __name__ == "__main__":
    main()
