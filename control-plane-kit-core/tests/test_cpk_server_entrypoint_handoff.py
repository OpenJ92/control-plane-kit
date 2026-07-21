import unittest

from control_plane_kit_core.operations import (
    ApplicationServiceBinding,
    ControlPlaneProcessContract,
    ControlPlaneServiceRole,
    CpkServerEntrypointHandoffContract,
    CpkServerMaterialHandoffContract,
    CpkServerPublicationHandoffContract,
    DependencyReadinessKind,
    DeploymentProgramBoundary,
    EntrypointCompositionPolicy,
    ExternalEffectPolicy,
    HttpApiContract,
    InvalidCpkServerHandoffContract,
    McpStreamableHttpContract,
    ProcessStatePolicy,
    PublicationPolicy,
    ReadinessDependency,
    ServiceTransactionBoundary,
    StoreParticipation,
    UnitOfWorkBoundary,
    canonical_cpk_server_entrypoint_handoff,
    canonical_cpk_server_material_handoff,
    canonical_cpk_server_publication_handoff,
    operator_adapter_security_parity,
    operator_command_http_routes,
    operator_command_parity,
    operator_read_http_routes,
    operator_read_projection_parity,
)
from control_plane_kit_core.configuration import (
    ConfigurationArtifact,
    ConfigurationMediaType,
)
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.products import OciImageReference, OciPlatform, ProductIdentity
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery,
    SecretReference,
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


def _transaction_rule(role: ControlPlaneServiceRole) -> ServiceTransactionBoundary:
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


def _uow() -> UnitOfWorkBoundary:
    return UnitOfWorkBoundary(
        program=_program(),
        services=tuple(_transaction_rule(role) for role in ControlPlaneServiceRole),
    )


def _http_api() -> HttpApiContract:
    return HttpApiContract(
        operator_read_http_routes() + operator_command_http_routes()
    )


def _handoff() -> CpkServerEntrypointHandoffContract:
    mcp = McpStreamableHttpContract()
    projection_parity = operator_read_projection_parity(_http_api(), mcp)
    command_parity = operator_command_parity(_http_api(), mcp, _uow())
    return canonical_cpk_server_entrypoint_handoff(
        process=ControlPlaneProcessContract(
            dependencies=tuple(
                ReadinessDependency(kind)
                for kind in (
                    DependencyReadinessKind.STORE,
                    DependencyReadinessKind.RUNTIME_AUTHORITY,
                    DependencyReadinessKind.WORKER,
                    DependencyReadinessKind.HTTP_API,
                    DependencyReadinessKind.MCP_STREAMABLE_HTTP,
                    DependencyReadinessKind.OBSERVATION,
                )
            ),
            http_api=_http_api(),
            mcp=mcp,
        ),
        program=_program(),
        unit_of_work=_uow(),
        projection_parity=projection_parity,
        command_parity=command_parity,
        security_parity=operator_adapter_security_parity(
            projection_parity=projection_parity,
            command_parity=command_parity,
        ),
    )


def _material_handoff() -> CpkServerMaterialHandoffContract:
    return canonical_cpk_server_material_handoff(
        entrypoint=_handoff(),
        product_identity=ProductIdentity("control-plane-kit", "cpk-server", 1),
        public_environment=(
            PublicStaticEnvironmentBinding("CPK_MODE", "server"),
        ),
        required_environment_names=("CPK_PUBLIC_BASE_URL",),
        secret_deliveries=(
            SecretEnvironmentDelivery(
                "CPK_DATABASE_URL",
                SecretReference("secret://runtime/cpk/database-url"),
            ),
            SecretEnvironmentDelivery(
                "CPK_RUNTIME_AUTH_TOKEN",
                SecretReference("secret://runtime/cpk/runtime-auth"),
            ),
        ),
        required_secret_environment_names=(
            "CPK_DATABASE_URL",
            "CPK_RUNTIME_AUTH_TOKEN",
        ),
        configuration_artifacts=(
            ConfigurationArtifact(
                artifact_id="cpk-server-config",
                target_path="/etc/cpk/server.json",
                media_type=ConfigurationMediaType.JSON,
                content='{"mode":"server"}',
            ),
        ),
        required_configuration_targets=("/etc/cpk/server.json",),
    )


def _publication_handoff() -> CpkServerPublicationHandoffContract:
    return canonical_cpk_server_publication_handoff(
        material=_material_handoff(),
        image=OciImageReference(
            registry="ghcr.io",
            repository="openj92/control-plane-kit-servers/cpk-server",
            digest="sha256:" + "a" * 64,
            tag="0.1.0",
            platforms=(OciPlatform("linux", "amd64"),),
        ),
    )


class CpkServerEntrypointHandoffTests(unittest.TestCase):
    def test_handoff_names_external_server_owner_and_core_contracts(self) -> None:
        handoff = _handoff()

        self.assertEqual(
            handoff.implementation_package,
            "control-plane-kit-servers/cpk-server",
        )
        self.assertEqual(
            handoff.composition_policy,
            EntrypointCompositionPolicy.ONE_DEPLOYMENT_PROGRAM,
        )
        self.assertEqual(
            handoff.state_policy,
            ProcessStatePolicy.PROCESS_GLOBALS_ARE_NOT_TRUTH,
        )
        self.assertEqual(handoff.import_direction, "cpk-server-imports-core")
        self.assertIs(handoff.process.http_api, handoff.http_api)

    def test_descriptor_is_closed_and_round_trips_without_process_packaging(self) -> None:
        handoff = _handoff()
        descriptor = handoff.descriptor()

        self.assertEqual(descriptor["kind"], "cpk-server-entrypoint-handoff")
        self.assertEqual(
            CpkServerEntrypointHandoffContract.from_descriptor(descriptor),
            handoff,
        )

        rendered = repr(descriptor).lower()
        self.assertNotIn("fastapi", rendered)
        self.assertNotIn("uvicorn", rendered)
        self.assertNotIn("dockerfile", rendered)
        self.assertNotIn("oci-image", rendered)

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerEntrypointHandoffContract.from_descriptor(
                {**descriptor, "extra": True}
            )


class CpkServerPublicationHandoffTests(unittest.TestCase):
    def test_publication_handoff_requires_pinned_image_and_live_evidence(self) -> None:
        handoff = _publication_handoff()

        self.assertTrue(handoff.runs_as_non_root)
        self.assertEqual(handoff.mutable_runtime_install_policy, "forbidden")
        self.assertEqual(handoff.filesystem_policy, "least-privilege-read-only-root")
        self.assertEqual(
            handoff.publication_policy,
            PublicationPolicy.PRIVATE_BY_DEFAULT_PUBLIC_ENDPOINT_EXPLICIT,
        )
        self.assertEqual(
            handoff.live_smoke_obligations,
            (
                "http-readiness",
                "http-read-route",
                "mcp-tool-call",
                "shutdown-cleanup",
            ),
        )
        self.assertEqual(
            handoff.cleanup_evidence_policy,
            "owned-resources-cleaned-retained-data-preserved",
        )

    def test_publication_descriptor_is_closed_and_round_trips(self) -> None:
        handoff = _publication_handoff()
        descriptor = handoff.descriptor()

        self.assertEqual(descriptor["kind"], "cpk-server-publication-handoff")
        self.assertEqual(
            CpkServerPublicationHandoffContract.from_descriptor(descriptor),
            handoff,
        )
        self.assertEqual(
            descriptor["image"]["digest"],
            "sha256:" + "a" * 64,
        )

        rendered = repr(descriptor).lower()
        self.assertNotIn("dockerfile", rendered)
        self.assertNotIn("pip install", rendered)
        self.assertNotIn("apt-get", rendered)

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerPublicationHandoffContract.from_descriptor(
                {**descriptor, "extra": True}
            )

    def test_publication_rejects_runtime_install_root_and_latest_tag(self) -> None:
        handoff = _publication_handoff()

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerPublicationHandoffContract(
                material=handoff.material,
                image=handoff.image,
                runs_as_non_root=False,
            )

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerPublicationHandoffContract(
                material=handoff.material,
                image=handoff.image,
                mutable_runtime_install_policy="allowed",
            )

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerPublicationHandoffContract(
                material=handoff.material,
                image=OciImageReference(
                    registry="ghcr.io",
                    repository="openj92/control-plane-kit-servers/cpk-server",
                    digest="sha256:" + "b" * 64,
                    tag="latest",
                    platforms=(OciPlatform("linux", "amd64"),),
                ),
            )

    def test_publication_requires_retained_data_and_public_contract_smoke(self) -> None:
        handoff = _publication_handoff()

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerPublicationHandoffContract(
                material=handoff.material,
                image=handoff.image,
                live_smoke_obligations=("http-readiness",),
            )

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerPublicationHandoffContract(
                material=handoff.material,
                image=handoff.image,
                cleanup_evidence_policy="delete-retained-data",
            )


class CpkServerMaterialHandoffTests(unittest.TestCase):
    def test_material_handoff_names_runtime_requirements_without_secret_values(self) -> None:
        handoff = _material_handoff()

        self.assertEqual(handoff.product_identity.key, "control-plane-kit/cpk-server/1")
        self.assertEqual(handoff.required_environment_names, ("CPK_PUBLIC_BASE_URL",))
        self.assertEqual(
            handoff.required_secret_environment_names,
            ("CPK_DATABASE_URL", "CPK_RUNTIME_AUTH_TOKEN"),
        )
        self.assertEqual(
            handoff.required_configuration_targets,
            ("/etc/cpk/server.json",),
        )
        self.assertEqual(handoff.descriptor_filename, "control-plane-instance.product.cpk.json")
        self.assertEqual(handoff.descriptor_admission_policy, "ordinary-external-product-data")
        self.assertEqual(handoff.self_registration_policy, "not-auto-registered")

    def test_material_descriptor_is_closed_and_round_trips(self) -> None:
        handoff = _material_handoff()
        descriptor = handoff.descriptor()

        self.assertEqual(descriptor["kind"], "cpk-server-material-handoff")
        self.assertEqual(CpkServerMaterialHandoffContract.from_descriptor(descriptor), handoff)

        rendered = repr(descriptor).lower()
        self.assertNotIn("postgres://", rendered)
        self.assertNotIn("do-not-disclose", rendered)
        self.assertNotIn("private.endpoint", rendered)

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerMaterialHandoffContract.from_descriptor({**descriptor, "extra": True})

    def test_required_secret_and_configuration_material_must_be_declared(self) -> None:
        handoff = _material_handoff()

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerMaterialHandoffContract(
                entrypoint=handoff.entrypoint,
                product_identity=handoff.product_identity,
                public_environment=handoff.public_environment,
                required_environment_names=handoff.required_environment_names,
                secret_deliveries=handoff.secret_deliveries[:1],
                required_secret_environment_names=handoff.required_secret_environment_names,
                configuration_artifacts=handoff.configuration_artifacts,
                required_configuration_targets=handoff.required_configuration_targets,
            )

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerMaterialHandoffContract(
                entrypoint=handoff.entrypoint,
                product_identity=handoff.product_identity,
                public_environment=handoff.public_environment,
                required_environment_names=handoff.required_environment_names,
                secret_deliveries=handoff.secret_deliveries,
                required_secret_environment_names=handoff.required_secret_environment_names,
                configuration_artifacts=(),
                required_configuration_targets=handoff.required_configuration_targets,
            )

    def test_material_handoff_rejects_baked_private_environment_values(self) -> None:
        handoff = _material_handoff()

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerMaterialHandoffContract(
                entrypoint=handoff.entrypoint,
                product_identity=handoff.product_identity,
                public_environment=(
                    PublicStaticEnvironmentBinding(
                        "CPK_PUBLIC_BASE_URL",
                        "https://private.endpoint",
                    ),
                ),
                required_environment_names=handoff.required_environment_names,
                secret_deliveries=handoff.secret_deliveries,
                required_secret_environment_names=handoff.required_secret_environment_names,
                configuration_artifacts=handoff.configuration_artifacts,
                required_configuration_targets=handoff.required_configuration_targets,
            )

    def test_descriptor_policy_rejects_auto_trusted_self_registration(self) -> None:
        handoff = _material_handoff()

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerMaterialHandoffContract(
                entrypoint=handoff.entrypoint,
                product_identity=handoff.product_identity,
                public_environment=handoff.public_environment,
                required_environment_names=handoff.required_environment_names,
                secret_deliveries=handoff.secret_deliveries,
                required_secret_environment_names=handoff.required_secret_environment_names,
                configuration_artifacts=handoff.configuration_artifacts,
                required_configuration_targets=handoff.required_configuration_targets,
                self_registration_policy="auto-trusted",
            )

    def test_process_http_api_must_cover_read_and_command_routes(self) -> None:
        handoff = _handoff()

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerEntrypointHandoffContract(
                process=ControlPlaneProcessContract(
                    http_api=HttpApiContract(operator_read_http_routes()),
                    mcp=handoff.mcp,
                ),
                program=handoff.program,
                unit_of_work=handoff.unit_of_work,
                projection_parity=handoff.projection_parity,
                command_parity=handoff.command_parity,
                security_parity=handoff.security_parity,
            )

    def test_handoff_rejects_process_global_truth_and_wrong_owner(self) -> None:
        handoff = _handoff()

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerEntrypointHandoffContract(
                process=handoff.process,
                program=handoff.program,
                unit_of_work=handoff.unit_of_work,
                projection_parity=handoff.projection_parity,
                command_parity=handoff.command_parity,
                security_parity=handoff.security_parity,
                state_policy=ProcessStatePolicy.PROCESS_GLOBALS_OWN_TRUTH,
            )

        with self.assertRaises(InvalidCpkServerHandoffContract):
            CpkServerEntrypointHandoffContract(
                process=handoff.process,
                program=handoff.program,
                unit_of_work=handoff.unit_of_work,
                projection_parity=handoff.projection_parity,
                command_parity=handoff.command_parity,
                security_parity=handoff.security_parity,
                implementation_package="control-plane-kit-core",
            )


if __name__ == "__main__":
    unittest.main()
