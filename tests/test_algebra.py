from unittest import TestCase, main

from control_plane_kit import Protocol, EnvironmentRequirementSocket, ProviderSocket, RoleSockets


class AlgebraTests(TestCase):
    def test_socket_names_are_accessible(self):
        sockets = RoleSockets(
            requirements=(EnvironmentRequirementSocket("DATABASE_URL", Protocol.POSTGRES, ("DATABASE_URL",)),),
            providers=(ProviderSocket("internal", Protocol.HTTP),),
        )

        self.assertEqual(sockets.requirement("DATABASE_URL").protocol, Protocol.POSTGRES)
        self.assertEqual(sockets.provider("internal").protocol, Protocol.HTTP)

    def test_environment_requirement_socket_requires_env_binding(self):
        with self.assertRaises(ValueError):
            EnvironmentRequirementSocket("database", Protocol.POSTGRES, ())


if __name__ == "__main__":
    main()
