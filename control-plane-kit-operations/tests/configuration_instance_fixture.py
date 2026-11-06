"""Pure #1918 contract fixtures; allocation preparation belongs to #1919."""
import importlib

from control_plane_kit_core.configuration import ConfigurationFileMode, ConfigurationMediaType
import control_plane_kit_core.planning as planning


def configuration_language():
    name = "control_plane_kit_core.configuration_instances"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as error:
        if error.name != name:
            raise
        raise AssertionError("missing #1918 configuration-instance language") from error


def configuration_ref(*, digest="a" * 64):
    return configuration_language().ConfigurationInstanceRef(
        allocation_id="allocation-a", workspace_id="workspace-a", runtime_id="docker",
        node_id="api", artifact_id="service-config", target_path="/etc/service/config.json",
        media_type=ConfigurationMediaType.JSON, file_mode=ConfigurationFileMode.READ_ONLY,
        content_digest=digest,
    )


def configuration_cleanup_activity():
    if not hasattr(planning, "CleanupConfigurationInstances"):
        raise AssertionError("missing #1918 CleanupConfigurationInstances operation")
    return planning.PlannedActivity(planning.ActivityId("cleanup-config"),
        planning.CleanupConfigurationInstances(instances=(configuration_ref(),)),
        risk=planning.RiskLevel.HIGH, impact=planning.ActivityImpact.DESTRUCTIVE)
