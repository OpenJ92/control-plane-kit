"""Validate architecture and composition contracts across exact backend sources."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tomllib
from typing import Iterable, Mapping

from .source_lock import BackendLockError, MaterializedBackend


CONTRACT_SCHEMA = "cpk.current-backend-contracts.v1"
COORDINATE_SCHEMA = "cpk-servers.coordinates"
CATALOGUE_SCHEMA = "cpk-servers.descriptor-catalogue"
MAX_FINDINGS = 64
_NAME = re.compile(r"^[a-z][a-z0-9-]{0,79}$")
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*_?$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_ARCHIVE_COORDINATE = re.compile(
    r"https://github\.com/OpenJ92/(?P<repository>[A-Za-z0-9_.-]+)"
    r"/archive/(?P<commit>[0-9a-f]{40})\.zip"
)


class BackendContractError(RuntimeError):
    """The exact backend snapshot violates a closed current contract."""


@dataclass(frozen=True)
class DistributionContract:
    name: str
    repository: str
    project_path: PurePosixPath
    source_globs: tuple[str, ...]
    module_prefixes: tuple[str, ...]
    allowed_internal_dependencies: frozenset[str]
    forbidden_imports: tuple[str, ...]


@dataclass(frozen=True)
class PinSurface:
    repository: str
    path: PurePosixPath
    coordinate_repositories: frozenset[str]


@dataclass(frozen=True)
class ProtocolContract:
    name: str
    protocol_repository: str
    protocol_path: PurePosixPath
    protocol_class: str
    methods: tuple[str, ...]
    implementation_repository: str
    implementation_path: PurePosixPath
    implementation_class: str


@dataclass(frozen=True)
class AcceptanceContract:
    name: str
    repository: str
    command_path: PurePosixPath
    classification: str
    authoritative_caller: str
    provider_mutating: bool
    published_digest: bool
    diagnostic_only: bool
    uses_application_mocks: bool
    residue_command_path: PurePosixPath


@dataclass(frozen=True)
class BackendContracts:
    distributions: tuple[DistributionContract, ...]
    pin_surfaces: tuple[PinSurface, ...]
    protocols: tuple[ProtocolContract, ...]
    acceptance: tuple[AcceptanceContract, ...]


@dataclass(frozen=True)
class BackendContractReport:
    source_files: int
    import_edges: tuple[tuple[str, str], ...]
    declared_edges: tuple[tuple[str, str], ...]
    products: tuple[str, ...]
    protocols: tuple[str, ...]
    acceptance: tuple[str, ...]

    def to_document(self) -> dict[str, object]:
        return {
            "schema": "cpk.current-backend-contract-report.v1",
            "source_files": self.source_files,
            "import_edges": [list(edge) for edge in self.import_edges],
            "declared_edges": [list(edge) for edge in self.declared_edges],
            "products": list(self.products),
            "protocols": list(self.protocols),
            "acceptance": list(self.acceptance),
        }


def load_backend_contracts(path: Path) -> BackendContracts:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BackendContractError("backend contracts could not be read") from error
    _exact_keys(
        raw,
        {"schema", "distributions", "pin_surfaces", "protocols", "acceptance"},
        "contracts",
    )
    if raw["schema"] != CONTRACT_SCHEMA:
        raise BackendContractError("backend contract schema is unsupported")
    distributions = tuple(_distribution(value) for value in _list(raw, "distributions"))
    names = tuple(value.name for value in distributions)
    if len(names) != len(set(names)) or set(names) != {
        "core",
        "operations",
        "interpreters",
        "secrets",
        "server-products",
    }:
        raise BackendContractError("distribution inventory is inconsistent")
    known = set(names)
    repositories = {value.repository for value in distributions}
    for value in distributions:
        if not value.allowed_internal_dependencies <= known - {value.name}:
            raise BackendContractError(
                f"distribution has unknown dependency: {value.name}"
            )
    prefixes = tuple(
        prefix for value in distributions for prefix in value.module_prefixes
    )
    _require_unique(prefixes, "module prefix")
    contracts = BackendContracts(
        distributions=distributions,
        pin_surfaces=tuple(_pin_surface(value) for value in _list(raw, "pin_surfaces")),
        protocols=tuple(_protocol(value) for value in _list(raw, "protocols")),
        acceptance=tuple(_acceptance(value) for value in _list(raw, "acceptance")),
    )
    _require_unique((value.name for value in contracts.protocols), "protocol")
    _require_unique((value.name for value in contracts.acceptance), "acceptance")
    _require_unique(
        (f"{value.repository}/{value.path}" for value in contracts.pin_surfaces),
        "pin surface",
    )
    for surface in contracts.pin_surfaces:
        if surface.repository not in repositories or not surface.coordinate_repositories <= repositories:
            raise BackendContractError("pin surface repository is unknown")
    for protocol in contracts.protocols:
        if {
            protocol.protocol_repository,
            protocol.implementation_repository,
        } - repositories:
            raise BackendContractError("protocol repository is unknown")
    for acceptance in contracts.acceptance:
        if acceptance.repository not in repositories:
            raise BackendContractError("acceptance repository is unknown")
    return contracts


def validate_backend(
    backend: MaterializedBackend,
    contracts: BackendContracts,
) -> BackendContractReport:
    findings: list[str] = []
    distributions = {value.name: value for value in contracts.distributions}
    module_owners = {
        prefix: value.name
        for value in contracts.distributions
        for prefix in value.module_prefixes
    }
    source_owners: dict[tuple[str, str], str] = {}
    imports: dict[str, set[str]] = {name: set() for name in distributions}
    declared: dict[str, set[str]] = {name: set() for name in distributions}

    for distribution in contracts.distributions:
        root = _repository_path(backend, distribution.repository, findings)
        if root is None:
            continue
        project = root / distribution.project_path
        dependencies = _project_dependencies(project, distribution.name, findings)
        for dependency in dependencies:
            owner = _module_owner(_normalized_dependency(dependency), module_owners)
            if owner is not None and owner != distribution.name:
                declared[distribution.name].add(owner)
        matched = 0
        for pattern in distribution.source_globs:
            for path in sorted(root.glob(pattern)):
                if not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                identity = (distribution.repository, relative)
                previous = source_owners.get(identity)
                if previous is not None:
                    findings.append(
                        f"source has duplicate owners: {distribution.repository}/{relative}"
                    )
                    continue
                source_owners[identity] = distribution.name
                matched += 1
                for imported in _python_imports(path, findings):
                    _check_forbidden_import(
                        distribution, imported, relative, findings
                    )
                    owner = _module_owner(imported, module_owners)
                    if owner is not None and owner != distribution.name:
                        imports[distribution.name].add(owner)
        if matched == 0:
            findings.append(f"distribution has no owned source: {distribution.name}")

    for repository in sorted({value.repository for value in contracts.distributions}):
        root = _repository_path(backend, repository, findings)
        if root is None:
            continue
        candidates: set[str] = set()
        for pattern in (
            "src/**/*.py",
            "control-plane-kit-*/src/**/*.py",
            "products/*/src/**/*.py",
        ):
            candidates.update(
                path.relative_to(root).as_posix()
                for path in root.glob(pattern)
                if path.is_file()
            )
        owned = {
            path for (owner_repository, path), _owner in source_owners.items()
            if owner_repository == repository
        }
        for path in sorted(candidates - owned):
            findings.append(f"current source has no owner: {repository}/{path}")

    _check_dependency_graph(distributions, imports, declared, findings)
    commits = _coordinate_commits(backend, findings)
    _check_pin_surfaces(backend, contracts.pin_surfaces, commits, findings)
    products = _check_products(backend, findings)
    protocols = _check_protocols(backend, contracts.protocols, findings)
    acceptance = _check_acceptance(backend, contracts.acceptance, findings)
    _raise_findings(findings)
    return BackendContractReport(
        source_files=len(source_owners),
        import_edges=tuple(
            sorted((source, target) for source, targets in imports.items() for target in targets)
        ),
        declared_edges=tuple(
            sorted((source, target) for source, targets in declared.items() for target in targets)
        ),
        products=products,
        protocols=protocols,
        acceptance=acceptance,
    )


def _check_dependency_graph(
    distributions: Mapping[str, DistributionContract],
    imports: Mapping[str, set[str]],
    declared: Mapping[str, set[str]],
    findings: list[str],
) -> None:
    graph = {name: set(imports[name]) | set(declared[name]) for name in distributions}
    for source, targets in graph.items():
        allowed = distributions[source].allowed_internal_dependencies
        for target in sorted(targets - allowed):
            findings.append(f"forbidden dependency edge: {source} -> {target}")
        for target in sorted(imports[source] - declared[source]):
            findings.append(f"undeclared internal dependency: {source} -> {target}")
    for source in sorted(graph):
        stack: list[tuple[str, tuple[str, ...]]] = [(source, (source,))]
        while stack:
            node, path = stack.pop()
            for target in graph[node]:
                if target == source:
                    findings.append(f"dependency cycle: {' -> '.join((*path, target))}")
                    stack.clear()
                    break
                if target not in path:
                    stack.append((target, (*path, target)))


def _check_forbidden_import(
    distribution: DistributionContract,
    imported: str,
    path: str,
    findings: list[str],
) -> None:
    for prefix in distribution.forbidden_imports:
        if imported == prefix or imported.startswith(f"{prefix}."):
            findings.append(
                f"forbidden import in {distribution.name}: {path} imports {imported}"
            )
            return


def _coordinate_commits(
    backend: MaterializedBackend,
    findings: list[str],
) -> dict[str, str]:
    root = _repository_path(backend, "control-plane-kit-servers", findings)
    if root is None:
        return {}
    raw = _json_object(root / "coordinates/server-products.json", "coordinates", findings)
    if raw.get("schema") != COORDINATE_SCHEMA:
        findings.append("coordinate manifest schema is inconsistent")
        return {}
    upstreams = raw.get("upstreams")
    if not isinstance(upstreams, dict):
        findings.append("coordinate upstreams are malformed")
        return {}
    mapping = {
        "control-plane-kit": "control_plane_kit_commit",
        "control-plane-kit-interpreters": "control_plane_kit_interpreters_commit",
        "control-plane-kit-secrets": "control_plane_kit_secrets_commit",
    }
    result: dict[str, str] = {}
    for repository, key in mapping.items():
        value = upstreams.get(key)
        if not isinstance(value, str) or not _COMMIT.fullmatch(value):
            findings.append(f"coordinate commit is malformed: {key}")
        else:
            result[repository] = value
    return result


def _check_pin_surfaces(
    backend: MaterializedBackend,
    surfaces: tuple[PinSurface, ...],
    commits: Mapping[str, str],
    findings: list[str],
) -> None:
    for surface in surfaces:
        root = _repository_path(backend, surface.repository, findings)
        if root is None:
            continue
        path = root / surface.path
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            findings.append(f"pin surface is unavailable: {surface.repository}/{surface.path}")
            continue
        found: dict[str, set[str]] = {}
        for match in _ARCHIVE_COORDINATE.finditer(text):
            found.setdefault(match.group("repository"), set()).add(match.group("commit"))
        for repository in sorted(surface.coordinate_repositories):
            expected = commits.get(repository)
            actual = found.get(repository, set())
            if expected is None or actual != {expected}:
                findings.append(
                    f"pin mismatch: {surface.repository}/{surface.path} -> {repository}"
                )
        unexpected = set(found) - surface.coordinate_repositories
        if unexpected:
            findings.append(
                f"unowned pin coordinate: {surface.repository}/{surface.path} -> {sorted(unexpected)}"
            )


def _check_products(
    backend: MaterializedBackend,
    findings: list[str],
) -> tuple[str, ...]:
    root = _repository_path(backend, "control-plane-kit-servers", findings)
    if root is None:
        return ()
    coordinates = _json_object(root / "coordinates/server-products.json", "coordinates", findings)
    catalogue = _json_object(root / "catalogue/products.json", "catalogue", findings)
    packaged = _json_object(
        root / "src/control_plane_kit_servers/catalogue.json",
        "packaged catalogue",
        findings,
    )
    if catalogue.get("schema") != CATALOGUE_SCHEMA:
        findings.append("catalogue schema is inconsistent")
    coordinate_products = _indexed_products(coordinates.get("products"), "coordinates", findings)
    catalogue_products = _indexed_products(catalogue.get("products"), "catalogue", findings)
    packaged_products = _indexed_products(packaged.get("products"), "packaged catalogue", findings)
    if set(coordinate_products) != set(catalogue_products) or set(catalogue_products) != set(packaged_products):
        findings.append("product inventories disagree")
    descriptor_paths = {
        path.relative_to(root).as_posix()
        for path in root.glob("products/*/product*.cpk.json")
        if path.is_file()
    }
    catalogue_paths = {
        value.get("descriptor_path")
        for value in catalogue_products.values()
        if isinstance(value.get("descriptor_path"), str)
    }
    if descriptor_paths != catalogue_paths:
        findings.append("descriptor inventory has missing or duplicate catalogue ownership")
    for product_id in sorted(set(coordinate_products) & set(catalogue_products)):
        coordinate = coordinate_products[product_id]
        entry = catalogue_products[product_id]
        expected = _coordinate_catalogue_projection(coordinate)
        for key, value in expected.items():
            if entry.get(key) != value:
                findings.append(f"catalogue coordinate mismatch: {product_id}.{key}")
        descriptor_path = entry.get("descriptor_path")
        if isinstance(descriptor_path, str):
            try:
                safe_descriptor_path = _safe_relative_text(
                    descriptor_path, "descriptor path"
                )
            except BackendContractError:
                findings.append(f"descriptor path is unsafe: {product_id}")
                continue
            owner_directory = entry.get("owner_directory")
            if not isinstance(owner_directory, str):
                findings.append(f"descriptor owner is malformed: {product_id}")
                continue
            try:
                safe_owner = _safe_relative_text(
                    owner_directory, "descriptor owner"
                )
            except BackendContractError:
                findings.append(f"descriptor owner is unsafe: {product_id}")
                continue
            if safe_descriptor_path.parent != safe_owner:
                findings.append(f"descriptor owner mismatch: {product_id}")
            descriptor = root / safe_descriptor_path
            try:
                digest = hashlib.sha256(descriptor.read_bytes()).hexdigest()
            except OSError:
                findings.append(f"descriptor is unavailable: {product_id}")
            else:
                if entry.get("descriptor_sha256") != digest:
                    findings.append(f"descriptor checksum mismatch: {product_id}")
        if packaged_products.get(product_id) != entry:
            findings.append(f"packaged catalogue mismatch: {product_id}")
    checksum_path = root / "src/control_plane_kit_servers/catalogue.json.sha256"
    try:
        checksum = checksum_path.read_text(encoding="ascii").split()[0]
        actual = hashlib.sha256(
            (root / "src/control_plane_kit_servers/catalogue.json").read_bytes()
        ).hexdigest()
    except (OSError, UnicodeError, IndexError):
        findings.append("packaged catalogue checksum is unavailable")
    else:
        if checksum != actual:
            findings.append("packaged catalogue checksum mismatch")
    return tuple(sorted(catalogue_products))


def _check_protocols(
    backend: MaterializedBackend,
    protocols: tuple[ProtocolContract, ...],
    findings: list[str],
) -> tuple[str, ...]:
    for contract in protocols:
        protocol_methods = _class_methods(
            backend,
            contract.protocol_repository,
            contract.protocol_path,
            contract.protocol_class,
            findings,
        )
        implementation_methods = _class_methods(
            backend,
            contract.implementation_repository,
            contract.implementation_path,
            contract.implementation_class,
            findings,
        )
        for method in contract.methods:
            if method not in protocol_methods:
                findings.append(f"protocol method is missing: {contract.name}.{method}")
            if method not in implementation_methods:
                findings.append(f"protocol implementation is missing: {contract.name}.{method}")
    return tuple(sorted(value.name for value in protocols))


def _check_acceptance(
    backend: MaterializedBackend,
    contracts: tuple[AcceptanceContract, ...],
    findings: list[str],
) -> tuple[str, ...]:
    for contract in contracts:
        root = _repository_path(backend, contract.repository, findings)
        if root is None:
            continue
        command = root / contract.command_path
        residue = root / contract.residue_command_path
        if not command.is_file() or not residue.is_file():
            findings.append(f"acceptance command is unavailable: {contract.name}")
            continue
        if contract.classification not in {"source-built", "published-digest", "provider-mutating", "diagnostic"}:
            findings.append(f"acceptance classification is unsupported: {contract.name}")
        if contract.authoritative_caller != "cpk-server" or contract.diagnostic_only:
            findings.append(f"source-live acceptance bypasses cpk-server: {contract.name}")
        if contract.uses_application_mocks:
            findings.append(f"source-live acceptance uses application mocks: {contract.name}")
        if contract.classification == "source-built" and (contract.published_digest or contract.provider_mutating):
            findings.append(f"source-live acceptance overclaims evidence: {contract.name}")
        try:
            source = command.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            findings.append(f"acceptance command is unreadable: {contract.name}")
            continue
        required_markers = (
            "products/cpk_server/Dockerfile",
            "Authorization: Bearer",
            "Mcp-Method: tools/call",
            "/workspaces",
        )
        if not all(marker in source for marker in required_markers):
            findings.append(f"acceptance command lacks authoritative composition: {contract.name}")
        if re.search(r"\b(mock|unittest\.mock)\b", source, re.IGNORECASE):
            findings.append(f"acceptance command contains application mock: {contract.name}")
    return tuple(sorted(value.name for value in contracts))


def _class_methods(
    backend: MaterializedBackend,
    repository: str,
    relative: PurePosixPath,
    class_name: str,
    findings: list[str],
) -> frozenset[str]:
    root = _repository_path(backend, repository, findings)
    if root is None:
        return frozenset()
    path = root / relative
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        findings.append(f"protocol source is unreadable: {repository}/{relative}")
        return frozenset()
    matches = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name]
    if len(matches) != 1:
        findings.append(f"protocol class is not unique: {repository}/{relative}:{class_name}")
        return frozenset()
    return frozenset(
        node.name
        for node in matches[0].body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def _project_dependencies(path: Path, owner: str, findings: list[str]) -> tuple[str, ...]:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        dependencies = raw["project"].get("dependencies", [])
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError):
        findings.append(f"project metadata is unavailable: {owner}")
        return ()
    if not isinstance(dependencies, list) or not all(isinstance(value, str) for value in dependencies):
        findings.append(f"project dependencies are malformed: {owner}")
        return ()
    return tuple(dependencies)


def _python_imports(path: Path, findings: list[str]) -> frozenset[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        findings.append(f"Python source is unreadable: {path.name}")
        return frozenset()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None and node.level == 0:
            imported.add(node.module)
    return frozenset(imported)


def _module_owner(module: str, owners: Mapping[str, str]) -> str | None:
    matches = [
        (prefix, owner)
        for prefix, owner in owners.items()
        if module == prefix or module.startswith(f"{prefix}.") or (prefix.endswith("_") and module.startswith(prefix))
    ]
    if not matches:
        return None
    return max(matches, key=lambda value: len(value[0]))[1]


def _normalized_dependency(value: str) -> str:
    name = re.split(r"[\s\[<>=!~@]", value, maxsplit=1)[0]
    return name.strip().replace("-", "_")


def _repository_path(
    backend: MaterializedBackend,
    repository: str,
    findings: list[str],
) -> Path | None:
    try:
        return backend.path_for(repository)
    except BackendLockError:
        findings.append(f"repository is unavailable: {repository}")
        return None


def _indexed_products(value: object, owner: str, findings: list[str]) -> dict[str, dict[str, object]]:
    if not isinstance(value, list):
        findings.append(f"{owner} products are malformed")
        return {}
    result: dict[str, dict[str, object]] = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("product_id"), str):
            findings.append(f"{owner} product is malformed")
            continue
        product_id = item["product_id"]
        if product_id in result:
            findings.append(f"{owner} has duplicate product: {product_id}")
        result[product_id] = item
    return result


def _coordinate_catalogue_projection(value: Mapping[str, object]) -> dict[str, object]:
    image = value.get("image")
    if not isinstance(image, dict):
        image = {}
    registry = image.get("registry")
    repository = image.get("repository")
    tag = image.get("tag")
    return {
        "owner_directory": value.get("owner_directory"),
        "descriptor_path": value.get("descriptor_path"),
        "source_commit": value.get("source_commit"),
        "image_ref": f"{registry}/{repository}:{tag}",
        "image_digest": image.get("digest"),
    }


def _json_object(path: Path, owner: str, findings: list[str]) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        findings.append(f"{owner} is unreadable")
        return {}
    if not isinstance(value, dict):
        findings.append(f"{owner} must be an object")
        return {}
    return value


def _raise_findings(findings: Iterable[str]) -> None:
    unique = tuple(sorted(set(findings)))
    if not unique:
        return
    bounded = unique[:MAX_FINDINGS]
    suffix = "" if len(unique) <= MAX_FINDINGS else f"\n... {len(unique) - MAX_FINDINGS} more"
    raise BackendContractError("current backend contract violations:\n- " + "\n- ".join(bounded) + suffix)


def _distribution(value: object) -> DistributionContract:
    keys = {
        "name", "repository", "project_path", "source_globs", "module_prefixes",
        "allowed_internal_dependencies", "forbidden_imports",
    }
    _exact_keys(value, keys, "distribution")
    return DistributionContract(
        name=_name(value["name"], "distribution name"),
        repository=_text(value["repository"], "repository"),
        project_path=_path(value["project_path"], "project path"),
        source_globs=_globs(value["source_globs"]),
        module_prefixes=_modules(value["module_prefixes"]),
        allowed_internal_dependencies=frozenset(_texts(value["allowed_internal_dependencies"], "allowed dependencies", allow_empty=True)),
        forbidden_imports=_modules(value["forbidden_imports"], allow_empty=True),
    )


def _pin_surface(value: object) -> PinSurface:
    _exact_keys(value, {"repository", "path", "coordinate_repositories"}, "pin surface")
    return PinSurface(
        repository=_text(value["repository"], "repository"),
        path=_path(value["path"], "pin path"),
        coordinate_repositories=frozenset(_texts(value["coordinate_repositories"], "coordinate repositories")),
    )


def _protocol(value: object) -> ProtocolContract:
    keys = {
        "name", "protocol_repository", "protocol_path", "protocol_class", "methods",
        "implementation_repository", "implementation_path", "implementation_class",
    }
    _exact_keys(value, keys, "protocol")
    return ProtocolContract(
        name=_name(value["name"], "protocol name"),
        protocol_repository=_text(value["protocol_repository"], "protocol repository"),
        protocol_path=_path(value["protocol_path"], "protocol path"),
        protocol_class=_text(value["protocol_class"], "protocol class"),
        methods=_texts(value["methods"], "protocol methods"),
        implementation_repository=_text(value["implementation_repository"], "implementation repository"),
        implementation_path=_path(value["implementation_path"], "implementation path"),
        implementation_class=_text(value["implementation_class"], "implementation class"),
    )


def _acceptance(value: object) -> AcceptanceContract:
    keys = {
        "name", "repository", "command_path", "classification", "authoritative_caller",
        "provider_mutating", "published_digest", "diagnostic_only", "uses_application_mocks",
        "residue_command_path",
    }
    _exact_keys(value, keys, "acceptance")
    booleans = {key: value[key] for key in ("provider_mutating", "published_digest", "diagnostic_only", "uses_application_mocks")}
    if not all(type(item) is bool for item in booleans.values()):
        raise BackendContractError("acceptance flags must be booleans")
    return AcceptanceContract(
        name=_name(value["name"], "acceptance name"),
        repository=_text(value["repository"], "acceptance repository"),
        command_path=_path(value["command_path"], "acceptance command"),
        classification=_text(value["classification"], "acceptance classification"),
        authoritative_caller=_text(value["authoritative_caller"], "authoritative caller"),
        provider_mutating=booleans["provider_mutating"],
        published_digest=booleans["published_digest"],
        diagnostic_only=booleans["diagnostic_only"],
        uses_application_mocks=booleans["uses_application_mocks"],
        residue_command_path=_path(value["residue_command_path"], "residue command"),
    )


def _exact_keys(value: object, expected: set[str], owner: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise BackendContractError(f"{owner} fields are inconsistent")


def _list(value: Mapping[str, object], key: str) -> list[object]:
    result = value.get(key)
    if not isinstance(result, list) or not result:
        raise BackendContractError(f"{key} must be a nonempty list")
    return result


def _text(value: object, owner: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii() or len(value) > 240:
        raise BackendContractError(f"{owner} must be bounded ASCII")
    return value


def _name(value: object, owner: str) -> str:
    text = _text(value, owner)
    if not _NAME.fullmatch(text):
        raise BackendContractError(f"{owner} is malformed")
    return text


def _texts(value: object, owner: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise BackendContractError(f"{owner} must be a {'possibly empty ' if allow_empty else ''}list")
    result = tuple(_text(item, owner) for item in value)
    if len(result) != len(set(result)):
        raise BackendContractError(f"{owner} contains duplicates")
    return result


def _modules(value: object, *, allow_empty: bool = False) -> tuple[str, ...]:
    result = _texts(value, "module prefixes", allow_empty=allow_empty)
    if not all(_MODULE.fullmatch(item) for item in result):
        raise BackendContractError("module prefix is malformed")
    return result


def _globs(value: object) -> tuple[str, ...]:
    result = _texts(value, "source globs")
    for pattern in result:
        path = PurePosixPath(pattern)
        if path.is_absolute() or ".." in path.parts or not pattern.endswith(".py"):
            raise BackendContractError("source glob is unsafe")
    return result


def _path(value: object, owner: str) -> PurePosixPath:
    return _safe_relative_text(_text(value, owner), owner)


def _safe_relative_text(text: str, owner: str) -> PurePosixPath:
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise BackendContractError(f"{owner} must be a safe relative path")
    return path


def _require_unique(values: Iterable[str], owner: str) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise BackendContractError(f"{owner} inventory contains duplicates")
