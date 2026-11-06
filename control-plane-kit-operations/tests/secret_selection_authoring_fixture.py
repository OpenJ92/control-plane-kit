"""Real registration setup for secret-selection authoring tests; no provider IO."""

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretProviderEndpointReference, SecretProviderId, SecretReference
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand, RegisterSecretReferenceCommand,
    SecretProviderKind, SecretProviderRegistrationService,
)


def admit_selection_reference(unit_of_work_factory, reference, intent):
    service = SecretProviderRegistrationService(unit_of_work_factory)
    provider = service.register_provider(RegisterSecretProviderCommand(
        workspace_id="workspace-a", provider_id=SecretProviderId("workspace-secrets"),
        provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
        display_name="Workspace secrets",
        endpoint_reference=SecretProviderEndpointReference("workspace-secrets"),
        credential_reference=SecretReference("secret://bootstrap/workspace-secrets/client-token"),
        allowed_reference_prefixes=(SecretReference("secret://workspace-secrets/workspace-a"),),
        allowed_intents=(intent,), admitted_by="operator-a", admitted_at="2026-07-22T10:00:00Z",
        actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
    ))
    registered = service.register_reference(RegisterSecretReferenceCommand(
        workspace_id="workspace-a", reference=reference,
        provider_registration_id=provider.registration_id, allowed_intents=(intent,),
        admitted_by="operator-a", admitted_at="2026-07-22T10:01:00Z",
        actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
    ))
    return provider, registered
