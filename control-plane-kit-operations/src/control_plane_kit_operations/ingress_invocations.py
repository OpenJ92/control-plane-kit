"""Closed, reference-only ingress invocation context and result values."""

from dataclasses import dataclass
import hashlib
import json
import re

import rfc8785

from control_plane_kit_core.public_ingress import (
    NamedPublicIngress, NamedPublicIngressCodec, IngressAuthorityReference, PublicIngressLifecycle,
)
from control_plane_kit_core.secrets import (
    SecretCustodyGrant, SecretCustodyReceipt, SecretCustodyStatus, SecretProviderEndpointReference,
    SecretReference, SecretUseIntent,
)
from control_plane_kit_operations._temporal import validate_canonical_utc_timestamp
from control_plane_kit_operations.effect_permissions import ExecutionCommandReference
from control_plane_kit_operations.execution_invocations import ExecutionInvocationKind as Kind, ExecutionInvocationOutcome as Outcome
from control_plane_kit_operations.ingress_authorities import (
    CloudflareZoneIngressAuthority, CloudflareZoneIngressAuthorityCodec, CloudflareOwnedIngressResource,
    GeneratedIngressSecretReference, GeneratedSecretPurpose, IngressAuthorityProviderKind, OwnedIngressResourceStatus,
)
from control_plane_kit_operations.records import OperationsRecordError

MAX_CANONICAL_INTEGER = 9007199254740991
INGRESS_KINDS = (Kind.INGRESS_PROVIDER_CREATE, Kind.INGRESS_CUSTODY_WRITE,
    Kind.INGRESS_CUSTODY_REVOKE, Kind.INGRESS_PROVIDER_REMOVE)
_ERROR = "ingress invocation value is malformed"


def require(condition):
    if not condition:
        raise OperationsRecordError(_ERROR)


def _text(value, maximum=2048):
    return type(value) is str and 1 <= len(value.encode("utf-8")) <= maximum and all(ord(c) >= 32 for c in value)


def _sha(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _canonical(payload, maximum):
    try:
        # Both canonical bytes and the expanded representation must fit. PostgreSQL
        # independently bounds jsonb text; neither representation permits secrets.
        encoded = rfc8785.dumps(payload)
        require(len(encoded) <= maximum and len(json.dumps(payload, ensure_ascii=False,
            allow_nan=False).encode("utf-8")) <= maximum)
        return encoded
    except (ValueError, TypeError, UnicodeError):
        raise OperationsRecordError(_ERROR) from None


def _bounded_tree(value):
    if type(value) is dict:
        for key, item in value.items():
            require(_text(key, 200))
            _bounded_tree(item)
    elif type(value) in (tuple, list):
        for item in value:
            _bounded_tree(item)
    elif type(value) is str:
        require(_text(value))
    elif type(value) is int:
        require(-MAX_CANONICAL_INTEGER <= value <= MAX_CANONICAL_INTEGER)
    else:
        require(value is None or type(value) is bool)


def custody_grant_from_descriptor(value):
    require(type(value) is dict)
    payload = dict(value)
    payload["reference"] = SecretReference(payload.pop("reference_id"))
    payload["endpoint_reference"] = SecretProviderEndpointReference(payload["endpoint_reference"])
    payload["credential_reference"] = SecretReference(payload["credential_reference"])
    payload["intent"] = SecretUseIntent(payload["intent"])
    result = SecretCustodyGrant(**payload)
    require(result.descriptor() == value and _text(result.reference.reference_id, 1024))
    return result


def custody_receipt_from_descriptor(value):
    require(type(value) is dict)
    payload = dict(value)
    payload["reference"] = SecretReference(payload.pop("reference_id"))
    payload["status"] = SecretCustodyStatus(payload["status"])
    result = SecretCustodyReceipt(**payload)
    require(result.descriptor() == value and result.status is SecretCustodyStatus.ACTIVE
        and type(result.version_number) is int and 1 <= result.version_number <= MAX_CANONICAL_INTEGER
        and _text(result.reference.reference_id, 1024))
    return result


def resource_from_descriptor(value):
    require(type(value) is dict)
    payload = dict(value)
    payload["authority_ref"] = IngressAuthorityReference(payload["authority_ref"])
    payload["provider_kind"] = IngressAuthorityProviderKind(payload["provider_kind"])
    payload["lifecycle"] = PublicIngressLifecycle(payload["lifecycle"])
    payload["status"] = OwnedIngressResourceStatus(payload["status"])
    result = CloudflareOwnedIngressResource(**payload)
    require(result.descriptor() == value and 1 <= result.epoch <= MAX_CANONICAL_INTEGER)
    return result


def generated_secret_from_descriptor(value):
    require(type(value) is dict)
    payload = dict(value)
    payload["secret_ref"] = SecretReference(payload["secret_ref"])
    payload["purpose"] = GeneratedSecretPurpose(payload["purpose"])
    result = GeneratedIngressSecretReference(**payload)
    require(result.descriptor() == value and 1 <= result.provider_version_number <= MAX_CANONICAL_INTEGER)
    return result


@dataclass(frozen=True, slots=True, repr=False)
class IngressInvocationContextRecord:
    command: ExecutionCommandReference
    workspace_id: str
    activity_id: str
    original_event_id: str
    created_at: str
    operation: str
    plan_id: str
    base_projection_id: str
    desired_projection_id: str
    ingress_registration_id: str
    ingress: NamedPublicIngress
    authority: CloudflareZoneIngressAuthority
    authorization_id: str
    custody_grant: SecretCustodyGrant
    original_resource: CloudflareOwnedIngressResource | None = None
    original_secret: GeneratedIngressSecretReference | None = None
    version: int = 1

    def __post_init__(self):
        require(type(self.version) is int and self.version == 1
            and type(self.command) is ExecutionCommandReference
            and type(self.ingress) is NamedPublicIngress
            and type(self.authority) is CloudflareZoneIngressAuthority
            and type(self.custody_grant) is SecretCustodyGrant
            and type(self.operation) is str and self.operation in ("allocate", "remove"))
        self.command.__post_init__()
        self.ingress.__post_init__()
        self.authority.__post_init__()
        self.custody_grant.__post_init__()
        validate_canonical_utc_timestamp(self.created_at)
        require(all(_text(value) for value in (self.workspace_id, self.original_event_id,
            self.plan_id, self.base_projection_id, self.desired_projection_id, self.ingress_registration_id))
            and type(self.activity_id) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", self.activity_id)
            and type(self.authorization_id) is str and re.fullmatch(r"suse_[0-9a-f]{64}", self.authorization_id)
            and self.authority.allows_hostname(self.ingress.hostname)
            and self.custody_grant.workspace_id == self.workspace_id
            and self.custody_grant.run_id == self.command.run_id
            and self.custody_grant.activity_id == self.activity_id
            and self.custody_grant.effect_id == self.original_event_id
            and self.custody_grant.intent is SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN
            and self.custody_grant.provider_registration_id == self.authority.generated_secret_provider_registration_id
            and _text(self.custody_grant.reference.reference_id, 1024))
        if self.operation == "allocate":
            require(self.original_resource is None and self.original_secret is None)
        else:
            require(type(self.original_resource) is CloudflareOwnedIngressResource
                and type(self.original_secret) is GeneratedIngressSecretReference)
            resource, secret = self.original_resource, self.original_secret
            resource.__post_init__()
            secret.__post_init__()
            require(resource.workspace_id == secret.workspace_id == self.workspace_id
                and resource.ingress_id == self.ingress.ingress_id
                and resource.authority_ref == self.ingress.authority_ref
                and resource.hostname == self.ingress.hostname and resource.zone_id == self.authority.zone_id
                and resource.lifecycle == self.ingress.lifecycle and resource.status is OwnedIngressResourceStatus.ACTIVE
                and (resource.source_run_id, resource.source_activity_id, resource.source_event_id)
                    == (secret.source_run_id, secret.source_activity_id, secret.source_event_id)
                and secret.secret_ref == self.custody_grant.reference
                and secret.provider_registration_id == self.custody_grant.provider_registration_id)
        payload = self._material()
        _bounded_tree(payload)
        _canonical(dict(payload, context_id=self.context_id), 32768)

    @property
    def ingress_id(self):
        return self.ingress.ingress_id

    @property
    def sequence(self):
        return ((Kind.SECRET_RESOLUTION, Kind.INGRESS_PROVIDER_CREATE, Kind.INGRESS_CUSTODY_WRITE)
            if self.operation == "allocate" else
            (Kind.SECRET_RESOLUTION, Kind.INGRESS_CUSTODY_REVOKE, Kind.INGRESS_PROVIDER_REMOVE))

    def _material(self):
        return dict(version=self.version, command=dict(run_id=self.command.run_id,
            idempotency_key=self.command.idempotency_key, intent_fingerprint=self.command.intent_fingerprint),
            workspace_id=self.workspace_id, ingress_id=self.ingress_id, activity_id=self.activity_id,
            original_event_id=self.original_event_id, created_at=self.created_at, operation=self.operation,
            plan_id=self.plan_id, base_projection_id=self.base_projection_id,
            desired_projection_id=self.desired_projection_id, ingress_registration_id=self.ingress_registration_id,
            ingress=self.ingress.descriptor(), authority=self.authority.storage_descriptor(),
            authorization_id=self.authorization_id, custody_grant=self.custody_grant.descriptor(),
            original_resource=None if self.original_resource is None else self.original_resource.descriptor(),
            original_secret=None if self.original_secret is None else self.original_secret.descriptor())

    @property
    def context_id(self):
        return hashlib.sha256(_canonical(dict(domain="cpk.ingress-invocation-context.v1",
            **self._material()), 32768)).hexdigest()

    def descriptor(self):
        return dict(context_id=self.context_id, **self._material())

    def target_fingerprint(self, kind):
        require(kind in self.sequence)
        target = (self.authorization_id if kind is Kind.SECRET_RESOLUTION else
            self.custody_grant.descriptor() if kind in (Kind.INGRESS_CUSTODY_WRITE, Kind.INGRESS_CUSTODY_REVOKE)
            else dict(ingress=self.ingress.descriptor(), authority=self.authority.storage_descriptor(),
                resource=None if self.original_resource is None else self.original_resource.descriptor()))
        return hashlib.sha256(_canonical(dict(domain="cpk.ingress-unit-target.v1",
            context_id=self.context_id, kind=kind.value, target=target), 32768)).hexdigest()

    @classmethod
    def from_descriptor(cls, value):
        try:
            require(type(value) is dict)
            payload = dict(value)
            payload.pop("context_id")
            payload.pop("ingress_id")
            payload["command"] = ExecutionCommandReference(**payload["command"])
            payload["ingress"] = NamedPublicIngressCodec().decode(payload["ingress"])
            payload["authority"] = CloudflareZoneIngressAuthorityCodec().decode(payload["authority"])
            payload["custody_grant"] = custody_grant_from_descriptor(payload["custody_grant"])
            if payload["original_resource"] is not None:
                payload["original_resource"] = resource_from_descriptor(payload["original_resource"])
            if payload["original_secret"] is not None:
                payload["original_secret"] = generated_secret_from_descriptor(payload["original_secret"])
            record = cls(**payload)
            require(record.descriptor() == value)
            return record
        except (KeyError, ValueError, TypeError, AttributeError):
            raise OperationsRecordError(_ERROR) from None


def _provider_id(value):
    require(type(value) is str and re.fullmatch(r"[a-zA-Z0-9._:-]{1,128}", value)
        and not any(word in value.lower() for word in ("token", "secret", "password", "key")))


def _stopped_result(payload):
    require(set(payload) == {"version", "tag", "stage", "category", "mutation_certainty",
        "cleanup_result", "tunnel_id", "dns_record_id", "dns_provenance"})
    stage, category, mutation, cleanup, tunnel, dns, provenance = (payload[key] for key in
        ("stage", "category", "mutation_certainty", "cleanup_result", "tunnel_id", "dns_record_id", "dns_provenance"))
    require(all(type(value) is str for value in (stage, category, mutation, cleanup, provenance)))
    provider_categories = ("provider-status", "malformed-response", "transport")
    observation_categories = (*provider_categories, "hostname-occupied", "dns-conflict")
    for value in (tunnel, dns):
        if value is not None:
            _provider_id(value)
    require((dns is None and provenance == "none") or (dns is not None and provenance in ("observed-only", "owned")))
    valid = False
    if stage == "dns-pre-observation":
        valid = (tunnel is None and provenance != "owned" and mutation == "none"
            and cleanup == "not-required" and category in observation_categories
            and (dns is None or category in ("hostname-occupied", "dns-conflict")))
    elif stage == "tunnel-allocation":
        valid = (tunnel is None and dns is None and mutation == "uncertain"
            and cleanup == "withheld" and category in provider_categories)
    elif stage == "tunnel-configuration":
        valid = (tunnel is not None and dns is None and mutation in ("tunnel-created", "uncertain")
            and ((category == "transport") == (mutation == "uncertain"))
            and cleanup in ("complete", "uncertain") and category in provider_categories)
    elif stage == "dns-pre-mutation-observation":
        valid = (tunnel is not None and provenance != "owned" and category in observation_categories
            and ((mutation == "tunnel-created" and cleanup in ("complete", "uncertain")
                    and (category == "hostname-occupied" or category in provider_categories and dns is None))
                or (mutation == "uncertain" and cleanup == "withheld"
                    and (category == "dns-conflict" or category == "malformed-response" and dns is None))))
    elif stage == "dns-reconciliation":
        valid = (tunnel is not None and dns is None and mutation == "uncertain"
            and cleanup == "withheld" and category in provider_categories)
    elif stage == "dns-create":
        valid = (tunnel is not None and provenance != "owned" and mutation == "uncertain"
            and cleanup in ("complete", "uncertain", "withheld") and category in provider_categories)
    elif stage == "tunnel-token":
        valid = (tunnel is not None and provenance == "owned" and mutation == "dns-and-tunnel-created"
            and cleanup == "complete" and category in provider_categories)
    elif stage == "cleanup":
        valid = (tunnel is not None and provenance == "owned" and mutation == "uncertain"
            and cleanup == "uncertain" and category == "cleanup")
    require(valid)
    return Outcome.UNKNOWN if mutation == "uncertain" or cleanup == "uncertain" else Outcome.FAILED


def validate_ingress_result(context, kind, outcome, payload):
    """Validate a closed fact sum; no arbitrary provider/error dictionaries."""
    require(type(context) is IngressInvocationContextRecord and kind in context.sequence
        and kind in INGRESS_KINDS and type(outcome) is Outcome and type(payload) is dict
        and type(payload.get("version")) is int and payload["version"] == 1 and type(payload.get("tag")) is str)
    tag = payload["tag"]
    if tag == "provider-created":
        require(kind is Kind.INGRESS_PROVIDER_CREATE and outcome is Outcome.RETURNED
            and set(payload) == {"version", "tag", "tunnel_id", "dns_record_id"})
        _provider_id(payload["tunnel_id"])
        _provider_id(payload["dns_record_id"])
    elif tag == "provider-create-stopped":
        require(kind is Kind.INGRESS_PROVIDER_CREATE and outcome is _stopped_result(payload))
    elif tag == "provider-create-unclassified":
        require(kind is Kind.INGRESS_PROVIDER_CREATE and outcome is Outcome.UNKNOWN and set(payload) == {"version", "tag"})
    elif tag == "custody-write-returned":
        require(kind is Kind.INGRESS_CUSTODY_WRITE and outcome is Outcome.RETURNED
            and set(payload) == {"version", "tag", "receipt"})
        receipt = custody_receipt_from_descriptor(payload["receipt"])
        require(receipt.matches(context.custody_grant))
    elif tag in ("custody-revoke-returned", "provider-removal-returned"):
        require(kind is (Kind.INGRESS_CUSTODY_REVOKE if tag == "custody-revoke-returned" else Kind.INGRESS_PROVIDER_REMOVE)
            and outcome is Outcome.RETURNED and set(payload) == {"version", "tag"})
    elif tag in ("custody-write-unknown", "custody-revoke-unknown", "provider-removal-unknown"):
        expected_kind = {"custody-write-unknown": Kind.INGRESS_CUSTODY_WRITE,
            "custody-revoke-unknown": Kind.INGRESS_CUSTODY_REVOKE,
            "provider-removal-unknown": Kind.INGRESS_PROVIDER_REMOVE}[tag]
        keys = {"version", "tag", "reason"}
        if kind is Kind.INGRESS_PROVIDER_REMOVE:
            keys.add("failed_stages")
        require(kind is expected_kind and outcome is Outcome.UNKNOWN and set(payload) == keys
            and type(payload["reason"]) is str and payload["reason"] in
                ("provider-error", "interrupted", "invalid-receipt" if kind is Kind.INGRESS_CUSTODY_WRITE else "invalid-result"))
        if kind is Kind.INGRESS_PROVIDER_REMOVE and payload["failed_stages"] is not None:
            stages = payload["failed_stages"]
            require(type(stages) is list and 1 <= len(stages) <= 3
                and stages == [stage for stage in ("dns", "connections", "tunnel") if stage in stages])
    else:
        raise OperationsRecordError(_ERROR)
    _bounded_tree(payload)
    _canonical(payload, 8192)


@dataclass(frozen=True, slots=True, repr=False)
class IngressInvocationResultRecord:
    invocation_id: str
    context_id: str
    recorded_at: str
    # Canonical immutable bytes prevent mutation after owner validation.
    canonical_result: bytes

    def __post_init__(self):
        require(_sha(self.invocation_id) and _sha(self.context_id) and type(self.canonical_result) is bytes
            and len(self.canonical_result) <= 8192)
        validate_canonical_utc_timestamp(self.recorded_at)
        payload = json.loads(self.canonical_result)
        require(type(payload) is dict and _canonical(payload, 8192) == self.canonical_result)

    def descriptor(self):
        return json.loads(self.canonical_result)

    @classmethod
    def create(cls, context, invocation, outcome, payload, recorded_at):
        validate_ingress_result(context, invocation.kind, outcome, payload)
        require(invocation.command == context.command and invocation.activity_id == context.activity_id
            and invocation.workspace_id == context.workspace_id
            and invocation.target_fingerprint == context.target_fingerprint(invocation.kind))
        return cls(invocation.invocation_id, context.context_id, recorded_at, _canonical(payload, 8192))
