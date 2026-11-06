"""Dynamic ingress must not dispatch through an unqualified legacy adapter."""

from tests.test_postgres_coordinator_permissions import (
    _CoordinatorManagementPermissionFixture,
)


class PostgresIngressInvocationAdmissionTargetTests(
    _CoordinatorManagementPermissionFixture,
):
    async def test_missing_ingress_admission_owner_refuses_provider_dispatch(self):
        # This fixture selects AllocatePublicIngress, but its coordinator has
        # current workspace policy and a trusted managed command. Its ingress
        # adapter supplies only the existing legacy create/teardown interface.
        owner, command, _ = await self.prepared(legacy=True)
        before = self.rows("cpk_cloudflare_ingress_resources")

        result = await owner.execute_managed(command)

        self.assertEqual(
            self.ingress.create_allocation_names,
            [],
            "dynamic ingress reached provider create without its admission owner",
        )
        self.assertEqual(result.status.value, "unsupported")
        self.assertEqual(self.rows("cpk_cloudflare_ingress_resources"), before)
        self.assertEqual(await owner.execute_managed(command), result)
        self.assertEqual(self.ingress.create_allocation_names, [])
