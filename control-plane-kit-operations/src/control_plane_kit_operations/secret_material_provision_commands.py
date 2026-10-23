"""Confidential, one-dispatch intake between committed custody transitions."""

import base64
import binascii
from dataclasses import dataclass
from typing import Callable

from control_plane_kit_core.identity import PrincipalAuthorizer, PrincipalKind, TrustedCommandContext
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretCustodian, SecretReference, SecretUseIntent, SecretValue
from control_plane_kit_operations._temporal import validate_canonical_utc_timestamp
from control_plane_kit_operations.secret_material_provision_attempts import (
    COMMAND_KIND, REQUIRED_SCOPES, SUPPORTED_INTENTS, ProvisionAttemptAdmissionService,
    ProvisionAttemptAuthorizationDenied, ProvisionAttemptConflict, ProvisionAttemptError,
    ProvisionAttemptIntent, ProvisionAttemptNotFound, ProvisionedSecretMaterialReceipt,
    _identifier, provision_attempt_id_for, validated_receipt,
)
from control_plane_kit_operations.secret_providers import (
    RegisteredSecretProviderStatus, SecretProviderNotFound, SecretProviderRegistrationError,
    secret_custody_grant_for,
)


MAX_MATERIAL_BYTES = 65536
MAX_ENCODED_CHARACTERS = 87384


class ProvisionCommandUnavailable(ProvisionAttemptError):
    """No claim about a failed or ambiguous durable write."""


class ProvisionRequestConflict(ProvisionAttemptConflict):
    """Navigation only; the exact status read independently checks authority."""

    def __init__(self, workspace_id, request_id):
        _identifier(workspace_id)
        _identifier(request_id)
        super().__init__("provision request already exists")
        self.workspace_id = workspace_id
        self.request_id = request_id


def _context(value):
    if type(value) is not TrustedCommandContext:
        raise ProvisionAttemptAuthorizationDenied("provision requires trusted workspace authority")
    _identifier(value.workspace_id)
    if (value.principal.identity.kind is not PrincipalKind.OPERATOR
            or not REQUIRED_SCOPES.issubset(value.granted_scopes)):
        raise ProvisionAttemptAuthorizationDenied("provision requires operator provision and register authority")


@dataclass(frozen=True)
class ProvisionSecretMaterialCommand:
    context: TrustedCommandContext
    request_id: str
    provider_registration_id: str
    secret_reference: SecretReference
    intent: SecretUseIntent

    def __post_init__(self):
        _context(self.context)
        _identifier(self.request_id)
        _identifier(self.provider_registration_id)
        if type(self.secret_reference) is not SecretReference:
            raise ProvisionAttemptError("provision reference is malformed")
        if type(self.intent) is not SecretUseIntent or self.intent not in SUPPORTED_INTENTS:
            raise ProvisionAttemptError("provision intent is unsupported")


def _validate_material(value):
    valid = False
    if type(value) is SecretValue:
        text = value.reveal()
        if type(text) is str and 0 < len(text) <= MAX_MATERIAL_BYTES:
            try:
                valid = len(text.encode("utf-8")) <= MAX_MATERIAL_BYTES
            except UnicodeEncodeError:
                pass
    if not valid:
        raise ProvisionAttemptError("provision material must be bounded nonempty UTF-8")


def decode_provision_request(context, payload):
    """Return safe command and separate ephemeral value, never a raw-input DTO.

    The caller must authorize before invoking this decoder. The HTTP service
    entrypoint does so with current authority; typed provision rechecks it.
    """
    _context(context)
    result = None
    if type(payload) is dict and set(payload) == {
        "request_id", "provider_registration_id", "secret_reference", "intent", "value_base64"
    }:
        encoded = payload["value_base64"]
        if type(encoded) is str and 0 < len(encoded) <= MAX_ENCODED_CHARACTERS:
            try:
                raw = base64.b64decode(encoded.encode("ascii"), validate=True)
                if (0 < len(raw) <= MAX_MATERIAL_BYTES
                        and base64.b64encode(raw).decode("ascii") == encoded):
                    value = SecretValue(raw.decode("utf-8"))
                    _validate_material(value)
                    command = ProvisionSecretMaterialCommand(context, payload["request_id"],
                        payload["provider_registration_id"], SecretReference(payload["secret_reference"]),
                        SecretUseIntent(payload["intent"]))
                    result = (command, value)
            except (ValueError, TypeError, UnicodeError, binascii.Error):
                pass
    if result is None:
        raise ProvisionAttemptError("provision request is malformed")
    return result


class SecretMaterialProvisionCommandService:
    """Current authority and portable custody IO, without raw durable state.

    The authorizer must consult current authoritative permission/denial evidence
    on every call. Immutable request grants alone are not that evidence. Servers
    supplies the actual adapter; this owner checks identity and the grant ceiling.
    SecretValue and transient Python strings are not promised to be zeroized.
    """

    def __init__(self, unit_of_work_factory, custodian: SecretCustodian,
                 *, authorizer: PrincipalAuthorizer, clock: Callable[[], str]):
        self._uow = unit_of_work_factory
        self._custodian = custodian
        self._authorizer = authorizer
        self._clock = clock
        self._admission = ProvisionAttemptAdmissionService(unit_of_work_factory)

    def provision_encoded(self, context, payload):
        self._authorize(context)
        command, value = decode_provision_request(context, payload)
        return self.provision(command, value)

    def provision(self, command, value):
        if type(command) is not ProvisionSecretMaterialCommand:
            raise ProvisionAttemptError("provision command is malformed")
        try:
            current = self._authorize(command.context)
            _validate_material(value)
            return ProvisionedSecretMaterialReceipt.from_attempt(self._provision(command, value, current))
        except ProvisionAttemptError:
            raise
        except Exception:
            pass
        raise ProvisionCommandUnavailable("provision outcome could not be durably completed")

    def _authorize(self, original):
        _context(original)
        current = None
        valid = False
        try:
            current = self._authorizer.authorize(original.principal, original.workspace_id)
            _context(current)
            valid = (current.workspace_id == original.workspace_id
                     and current.principal.identity == original.principal.identity
                     and set(current.granted_scopes).issubset(original.granted_scopes))
        except Exception:
            pass
        if not valid:
            raise ProvisionAttemptAuthorizationDenied("current provision authority is unavailable or denied")
        return current

    def _now(self):
        value = self._clock()
        validate_canonical_utc_timestamp(value)
        return value

    def _read(self, workspace_id, request_id):
        with self._uow() as uow:
            return uow.stores.secret_material_provision_attempts.get(workspace_id, request_id)

    def _prepare(self, command, current):
        grant = None
        try:
            with self._uow() as uow:
                provider = uow.stores.secret_providers.get_by_registration(
                    current.workspace_id, command.provider_registration_id)
                if provider.status is not RegisteredSecretProviderStatus.ACTIVE:
                    raise SecretProviderNotFound("active provision provider is unavailable")
                grant = secret_custody_grant_for(provider=provider, workspace_id=current.workspace_id,
                    reference=command.secret_reference, intent=command.intent, actor_subject=current.actor_id,
                    actor_scopes=current.granted_scopes, required_scope=PolicyScope.SECRET_MATERIAL_PROVISION,
                    correlation_id=provision_attempt_id_for(current.workspace_id, command.request_id),
                    operation_id=COMMAND_KIND)
        except (SecretProviderNotFound, SecretProviderRegistrationError):
            pass
        if grant is None:
            raise ProvisionAttemptConflict("provision provider authority is unavailable or changed")
        return grant

    def _recheck(self, command, current, grant):
        if self._prepare(command, current) != grant:
            raise ProvisionAttemptConflict("provision provider binding changed")
        with self._uow() as uow:
            history = uow.stores.secret_references.list_history(grant.workspace_id, grant.reference)
        if history:
            raise ProvisionAttemptConflict("provision destination is already admitted")

    def _provision(self, command, value, current):
        existing = None
        try:
            existing = self._read(current.workspace_id, command.request_id)
        except ProvisionAttemptNotFound:
            pass
        if existing is not None:
            raise ProvisionRequestConflict(current.workspace_id, command.request_id)
        grant = self._prepare(command, current)
        self._recheck(command, current, grant)
        intent = ProvisionAttemptIntent(command.request_id, current.principal.identity, grant, self._now())
        reserved = False
        try:
            with self._uow() as uow:
                uow.stores.secret_material_provision_attempts.reserve(intent)
                uow.commit()
                reserved = True
        except ProvisionAttemptConflict:
            pass
        if not reserved:
            existing = None
            try:
                existing = self._read(current.workspace_id, command.request_id)
            except ProvisionAttemptNotFound:
                pass
            if existing is not None:
                raise ProvisionRequestConflict(current.workspace_id, command.request_id)
            raise ProvisionAttemptConflict("provision reservation conflicts with current authority")
        current = self._authorize(command.context)
        self._recheck(command, current, grant)
        with self._uow() as uow:
            claim = uow.stores.secret_material_provision_attempts.claim_initial(
                current.workspace_id, command.request_id, claimed_at=self._now())
            uow.commit()
        if claim is None:
            raise ProvisionRequestConflict(current.workspace_id, command.request_id)

        # Only this invocation's newly committed initial claim can reach here.
        # This handler ends BEFORE invoking the provider or persisting outcomes.
        refusal = None
        try:
            current = self._authorize(command.context)
            self._recheck(command, current, grant)
        except ProvisionAttemptAuthorizationDenied:
            refusal = ProvisionAttemptAuthorizationDenied
        except ProvisionAttemptConflict:
            refusal = ProvisionAttemptConflict
        if refusal is not None:
            with self._uow() as uow:
                uow.stores.secret_material_provision_attempts.record_refusal(claim, observed_at=self._now())
                uow.commit()
            raise refusal("provision dispatch was refused before invocation")

        receipt = None
        try:
            receipt = validated_receipt(intent, self._custodian.store(grant, value))
        except Exception:
            pass
        # No provider exception object survives into storage, errors or history.
        with self._uow() as uow:
            store = uow.stores.secret_material_provision_attempts
            attempt = (store.record_uncertain(claim, observed_at=self._now()) if receipt is None
                       else store.record_receipt(claim, receipt, observed_at=self._now()))
            uow.commit()
        if receipt is None:
            return attempt
        current = self._authorize(command.context)
        try:
            return self._admission.admit(claim, actor=current.principal.identity,
                actor_scopes=current.granted_scopes, admitted_at=self._now())
        except (ProvisionAttemptConflict, SecretProviderRegistrationError):
            pass
        return self._read(claim.workspace_id, claim.request_id)
