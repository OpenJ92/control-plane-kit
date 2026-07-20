"""Typed discovery projection and official-image CoreDNS integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from importlib.resources import files
from ipaddress import IPv4Address, IPv6Address, ip_address
import json
import re
from typing import Mapping, TypeAlias
from urllib.parse import urlsplit

from control_plane_kit.core.algebra import (
    ApplicationBlock,
    BlockSockets,
    PackageServerProduct,
    PackageServerSpec,
    ProductMaturity,
    ProviderSocket,
)
from control_plane_kit.core.capabilities import CapabilityName
from control_plane_kit.core.configuration import (
    ConfigurationArtifact,
    ConfigurationFileMode,
    ConfigurationMediaType,
)
from control_plane_kit.configuration_rendering import ConfigurationTemplate
from control_plane_kit.domains.discovery import (
    DiscoveryRegistrationRecord,
    DiscoveryRegistrationStatus,
)
from control_plane_kit.implementations import DockerImageImplementation, HostPublication
from control_plane_kit.core.types import Protocol
from control_plane_kit.core.verification import (
    DnsRecordType,
    DnsResolveCheck,
    HttpCheck,
    VerificationContract,
)


COREDNS_IMAGE = "coredns/coredns:1.14.6"
COREDNS_CONFIG_PATH = "/etc/coredns/Corefile"
COREDNS_ZONE_PATH = "/etc/coredns/zones/db.cpk"
MAX_DNS_RECORDS = 1_000
_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


@dataclass(frozen=True, order=True, slots=True)
class DnsName:
    """One canonical absolute DNS host name."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("DNS name must be text")
        candidate = self.value.rstrip(".").lower()
        labels = candidate.split(".")
        if (
            not candidate
            or len(candidate) > 253
            or any(not _DNS_LABEL.fullmatch(label) for label in labels)
        ):
            raise ValueError("DNS name is invalid")
        object.__setattr__(self, "value", f"{candidate}.")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, order=True, slots=True)
class DnsARecord:
    name: DnsName
    address: IPv4Address
    ttl_seconds: int = 60

    def __post_init__(self) -> None:
        _record_fields(self.name, self.address, IPv4Address, self.ttl_seconds)

    def descriptor(self) -> dict[str, object]:
        return _record_descriptor(self.name, "A", self.address, self.ttl_seconds)


@dataclass(frozen=True, order=True, slots=True)
class DnsAaaaRecord:
    name: DnsName
    address: IPv6Address
    ttl_seconds: int = 60

    def __post_init__(self) -> None:
        _record_fields(self.name, self.address, IPv6Address, self.ttl_seconds)

    def descriptor(self) -> dict[str, object]:
        return _record_descriptor(self.name, "AAAA", self.address, self.ttl_seconds)


CoreDnsRecord: TypeAlias = DnsARecord | DnsAaaaRecord


@dataclass(frozen=True, slots=True)
class CoreDnsConfiguration:
    """Bounded authoritative DNS projection retained as graph artifacts."""

    zone: DnsName
    records: tuple[CoreDnsRecord, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.zone, DnsName):
            raise TypeError("CoreDNS zone must be DnsName")
        if (
            not isinstance(self.records, tuple)
            or not self.records
            or len(self.records) > MAX_DNS_RECORDS
            or any(
                not isinstance(value, (DnsARecord, DnsAaaaRecord))
                for value in self.records
            )
        ):
            raise ValueError("CoreDNS records must be a bounded nonempty typed tuple")
        if any(
            value.name != self.zone
            and not value.name.value.endswith(f".{self.zone.value}")
            for value in self.records
        ):
            raise ValueError("CoreDNS record must belong to its authoritative zone")
        identities = tuple(_record_identity(value) for value in self.records)
        if len(set(identities)) != len(identities):
            raise ValueError("CoreDNS records must not contain duplicates")
        object.__setattr__(
            self,
            "records",
            tuple(sorted(self.records, key=_record_sort_key)),
        )

    @property
    def serial(self) -> int:
        payload = json.dumps(
            [value.descriptor() for value in sorted(self.records, key=_record_sort_key)],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return int.from_bytes(hashlib.sha256(payload).digest()[:4], "big") or 1

    def configuration_values(self) -> Mapping[str, object]:
        return {
            "zone": self.zone.value,
            "zone_path": COREDNS_ZONE_PATH,
            "serial": self.serial,
            "records": tuple(
                value.descriptor()
                for value in sorted(self.records, key=_record_sort_key)
            ),
        }


def project_discovery_to_coredns(
    zone: DnsName,
    registrations: tuple[DiscoveryRegistrationRecord, ...],
    *,
    observed_at: datetime,
) -> CoreDnsConfiguration:
    """Interpret one bounded registry result into explicit DNS graph data."""

    if not isinstance(zone, DnsName):
        raise TypeError("DNS projection zone must be DnsName")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("DNS projection time must be timezone-aware")
    if (
        not isinstance(registrations, tuple)
        or not registrations
        or len(registrations) > MAX_DNS_RECORDS
        or any(not isinstance(value, DiscoveryRegistrationRecord) for value in registrations)
    ):
        raise ValueError("DNS projection requires bounded typed registry records")

    records: list[CoreDnsRecord] = []
    for record in sorted(
        registrations,
        key=lambda value: (
            value.registration.identity.service_id,
            value.registration.identity.instance_id,
        ),
    ):
        if record.status is not DiscoveryRegistrationStatus.ACTIVE:
            raise ValueError("DNS projection rejects inactive registry records")
        if (
            record.updated_at > observed_at
            or record.registration.lease.issued_at > observed_at
        ):
            raise ValueError("DNS projection rejects future registry records")
        if record.registration.lease.expires_at <= observed_at:
            raise ValueError("DNS projection rejects expired registry records")
        endpoint = record.registration.endpoint
        parsed = urlsplit(endpoint.url)
        if parsed.port is None or not 1 <= parsed.port <= 65_535:
            raise ValueError("DNS projection rejects invalid endpoint ports")
        try:
            address = ip_address(parsed.hostname or "")
        except ValueError:
            raise ValueError(
                "DNS projection requires a literal IP endpoint target"
            ) from None
        service = _dns_label(record.registration.identity.service_id, "service")
        instance = _dns_label(record.registration.identity.instance_id, "instance")
        service_name = DnsName(f"{service}.{zone.value}")
        instance_name = DnsName(f"{instance}.{service}.{zone.value}")
        record_type = (
            DnsARecord if isinstance(address, IPv4Address) else DnsAaaaRecord
        )
        records.extend(
            (record_type(service_name, address), record_type(instance_name, address))
        )

    return CoreDnsConfiguration(zone, tuple(records))


def default_coredns_configuration() -> CoreDnsConfiguration:
    return CoreDnsConfiguration(
        DnsName("cpk.internal"),
        (DnsARecord(DnsName("dns.cpk.internal"), IPv4Address("127.0.0.1")),),
    )


def render_coredns_configuration(
    configuration: CoreDnsConfiguration,
) -> tuple[ConfigurationArtifact, ConfigurationArtifact]:
    if not isinstance(configuration, CoreDnsConfiguration):
        raise TypeError("CoreDNS renderer requires typed configuration")
    templates = files("control_plane_kit").joinpath("product_templates")
    corefile = ConfigurationTemplate(
        "coredns-corefile",
        "coredns-corefile",
        COREDNS_CONFIG_PATH,
        ConfigurationMediaType.TEXT,
        templates.joinpath("coredns.Corefile.j2").read_text(encoding="utf-8"),
        ConfigurationFileMode.READ_ONLY,
    ).render(configuration)
    zone = ConfigurationTemplate(
        "coredns-zone",
        "coredns-zone",
        COREDNS_ZONE_PATH,
        ConfigurationMediaType.TEXT,
        templates.joinpath("coredns.zone.j2").read_text(encoding="utf-8"),
        ConfigurationFileMode.READ_ONLY,
    ).render(configuration)
    return corefile, zone


def coredns_block(
    block_id: str = "coredns",
    *,
    display_name: str = "CoreDNS",
    image: str = COREDNS_IMAGE,
    configuration: CoreDnsConfiguration | None = None,
    host_publications: Mapping[str, HostPublication] | None = None,
) -> ApplicationBlock:
    config = default_coredns_configuration() if configuration is None else configuration
    if not isinstance(config, CoreDnsConfiguration):
        raise TypeError("CoreDNS block requires typed configuration")
    first = config.records[0]
    record_type = (
        DnsRecordType.A if isinstance(first, DnsARecord) else DnsRecordType.AAAA
    )
    return ApplicationBlock(
        PackageServerSpec(
            role_id=block_id,
            product=PackageServerProduct.COREDNS,
            maturity=ProductMaturity.OPERATIONAL,
            display_name=display_name,
            health_path="/health",
            capabilities=(CapabilityName.HEALTH_CHECKABLE, CapabilityName.RESTARTABLE),
            verification=VerificationContract(
                (
                    HttpCheck(
                        check_id="coredns-health",
                        provider_socket="health",
                        path="/health",
                    ),
                    HttpCheck(
                        check_id="coredns-ready",
                        provider_socket="ready",
                        path="/ready",
                    ),
                    DnsResolveCheck(
                        check_id="coredns-tcp-resolution",
                        provider_socket="dns-tcp",
                        query_name=first.name.value,
                        record_type=record_type,
                    ),
                    DnsResolveCheck(
                        check_id="coredns-udp-resolution",
                        provider_socket="dns-udp",
                        query_name=first.name.value,
                        record_type=record_type,
                    ),
                )
            ),
        ),
        DockerImageImplementation(
            image=image,
            command=("-conf", COREDNS_CONFIG_PATH),
            ports={"dns-tcp": 53, "dns-udp": 53, "health": 8080, "ready": 8181},
            host_publications=dict(host_publications or {}),
            configuration_artifacts=render_coredns_configuration(config),
        ),
        BlockSockets(
            providers=(
                ProviderSocket("dns-tcp", Protocol.DNS_TCP),
                ProviderSocket("dns-udp", Protocol.DNS_UDP),
                ProviderSocket("health", Protocol.HTTP),
                ProviderSocket("ready", Protocol.HTTP),
            )
        ),
    )


def _record_fields(name, address, address_type, ttl_seconds: int) -> None:
    if not isinstance(name, DnsName):
        raise TypeError("DNS record name must be DnsName")
    if not isinstance(address, address_type):
        raise TypeError("DNS record address has the wrong address family")
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86_400:
        raise ValueError("DNS record TTL must be between 1 and 86400 seconds")


def _record_descriptor(
    name,
    kind: str,
    address,
    ttl_seconds: int,
) -> dict[str, object]:
    return {
        "name": name.value,
        "kind": kind,
        "target": str(address),
        "ttl": ttl_seconds,
    }


def _record_identity(record: CoreDnsRecord) -> tuple[str, str, str]:
    descriptor = record.descriptor()
    return (
        str(descriptor["name"]),
        str(descriptor["kind"]),
        str(descriptor["target"]),
    )


def _record_sort_key(record: CoreDnsRecord) -> tuple[str, str, str]:
    return _record_identity(record)


def _dns_label(value: str, kind: str) -> str:
    candidate = value.lower()
    if not _DNS_LABEL.fullmatch(candidate):
        raise ValueError(f"DNS projection {kind} identity is not a DNS label")
    return candidate
