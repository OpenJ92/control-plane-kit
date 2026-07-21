import unittest

from control_plane_kit_core.operations import (
    ApplicationServiceBinding,
    ControlPlaneServiceRole,
    DeploymentProgramBoundary,
    ExternalEffectPolicy,
    InvalidUnitOfWorkBoundary,
    ServiceTransactionBoundary,
    StoreParticipation,
    UnitOfWorkBoundary,
)


def _program() -> DeploymentProgramBoundary:
    return DeploymentProgramBoundary(
        tuple(
            ApplicationServiceBinding(
                role=role,
                service_name=f"{role.value}-service",
            )
            for role in ControlPlaneServiceRole
        )
    )


def _rule(role: ControlPlaneServiceRole) -> ServiceTransactionBoundary:
    if role is ControlPlaneServiceRole.READS:
        return ServiceTransactionBoundary(role, StoreParticipation.READ_ONLY)
    if role is ControlPlaneServiceRole.AUTHORIZATION:
        return ServiceTransactionBoundary(role, StoreParticipation.NONE)
    if role is ControlPlaneServiceRole.EXECUTION:
        return ServiceTransactionBoundary(
            role,
            StoreParticipation.READ_WRITE,
            owns_transaction=True,
            external_effect_policy=ExternalEffectPolicy.AFTER_COMMIT,
            uses_worker=True,
            uses_runtime_authority=True,
        )
    return ServiceTransactionBoundary(
        role,
        StoreParticipation.READ_WRITE,
        owns_transaction=True,
    )


class UnitOfWorkBoundaryTests(unittest.TestCase):
    def test_boundary_maps_every_program_role_once(self) -> None:
        boundary = UnitOfWorkBoundary(
            program=_program(),
            services=tuple(_rule(role) for role in ControlPlaneServiceRole),
        )

        self.assertEqual(
            [service.role for service in boundary.services],
            list(ControlPlaneServiceRole),
        )
        self.assertEqual(boundary.store_commit_policy, "stores-never-commit")

    def test_boundary_rejects_missing_or_duplicate_transaction_rules(self) -> None:
        missing = tuple(
            _rule(role)
            for role in ControlPlaneServiceRole
            if role is not ControlPlaneServiceRole.ADMISSION
        )
        with self.assertRaises(InvalidUnitOfWorkBoundary):
            UnitOfWorkBoundary(program=_program(), services=missing)

        duplicate = tuple(_rule(role) for role in ControlPlaneServiceRole) + (
            _rule(ControlPlaneServiceRole.PLANNING),
        )
        with self.assertRaises(InvalidUnitOfWorkBoundary):
            UnitOfWorkBoundary(program=_program(), services=duplicate)

    def test_boundary_rejects_store_commit_and_effects_inside_transactions(self) -> None:
        with self.assertRaises(InvalidUnitOfWorkBoundary):
            ServiceTransactionBoundary(
                ControlPlaneServiceRole.PLANNING,
                StoreParticipation.READ_WRITE,
                owns_transaction=False,
            )

        with self.assertRaises(InvalidUnitOfWorkBoundary):
            ServiceTransactionBoundary(
                ControlPlaneServiceRole.EXECUTION,
                StoreParticipation.READ_WRITE,
                owns_transaction=True,
                external_effect_policy=ExternalEffectPolicy.INSIDE_TRANSACTION,
            )

    def test_boundary_rejects_worker_or_runtime_authority_on_non_command_roles(self) -> None:
        with self.assertRaises(InvalidUnitOfWorkBoundary):
            ServiceTransactionBoundary(
                ControlPlaneServiceRole.READS,
                StoreParticipation.READ_ONLY,
                uses_worker=True,
            )

        with self.assertRaises(InvalidUnitOfWorkBoundary):
            ServiceTransactionBoundary(
                ControlPlaneServiceRole.AUTHORIZATION,
                StoreParticipation.NONE,
                uses_runtime_authority=True,
            )

    def test_descriptor_names_transaction_laws_without_database_implementation(self) -> None:
        boundary = UnitOfWorkBoundary(
            program=_program(),
            services=tuple(_rule(role) for role in ControlPlaneServiceRole),
        )

        descriptor = boundary.descriptor()

        self.assertEqual(descriptor["transaction_boundary"], "operator-command")
        self.assertEqual(descriptor["store_commit_policy"], "stores-never-commit")
        rendered = repr(descriptor).lower()
        self.assertNotIn("postgres", rendered)
        self.assertNotIn("psycopg", rendered)
        self.assertNotIn("fastapi", rendered)
        self.assertNotIn("dockerfile", rendered)


if __name__ == "__main__":
    unittest.main()
