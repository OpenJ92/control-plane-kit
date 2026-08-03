"""Resolve and materialize one immutable current-backend source snapshot."""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, replace
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
from typing import Iterator, Mapping
from urllib.parse import urlparse


LOCK_SCHEMA = "cpk.current-backend-lock.v1"
COORDINATE_SCHEMA = "cpk-servers.coordinates"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IDENTITY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_EXPECTED_UPSTREAMS = {
    "control-plane-kit": "control_plane_kit_commit",
    "control-plane-kit-interpreters": "control_plane_kit_interpreters_commit",
    "control-plane-kit-secrets": "control_plane_kit_secrets_commit",
}


class BackendLockError(RuntimeError):
    """The current-backend source lock or materialization is invalid."""


@dataclass(frozen=True)
class RepositorySpec:
    name: str
    identity: str
    url: str
    commit: str | None
    coordinate_key: str | None

    def __post_init__(self) -> None:
        if not self.name or not self.name.isascii():
            raise BackendLockError("repository name must be bounded ASCII")
        if not _IDENTITY.fullmatch(self.identity):
            raise BackendLockError("repository identity is malformed")
        if not self.url or not self.url.isascii():
            raise BackendLockError("repository URL must be bounded ASCII")
        if self.commit is not None:
            _require_commit(self.commit)
        if self.coordinate_key is not None and not re.fullmatch(
            r"[a-z][a-z0-9_]{0,79}", self.coordinate_key
        ):
            raise BackendLockError("coordinate key is malformed")


@dataclass(frozen=True)
class BackendLock:
    schema: str
    server_products: RepositorySpec
    coordinates_path: Path
    upstreams: tuple[RepositorySpec, ...]

    def __post_init__(self) -> None:
        if self.schema != LOCK_SCHEMA:
            raise BackendLockError("backend lock schema is unsupported")
        if self.server_products.name != "control-plane-kit-servers":
            raise BackendLockError("server-products root identity is required")
        if self.server_products.commit is None:
            raise BackendLockError("server-products requires a full Git commit")
        if self.server_products.coordinate_key is not None:
            raise BackendLockError("server-products cannot use an upstream key")
        _require_safe_relative_path(self.coordinates_path, "coordinates path")
        names = tuple(spec.name for spec in self.upstreams)
        if len(names) != len(set(names)):
            raise BackendLockError("backend lock has duplicate distributions")
        if set(names) != set(_EXPECTED_UPSTREAMS):
            raise BackendLockError("backend lock upstream inventory is incomplete")
        for spec in self.upstreams:
            if spec.commit is not None:
                raise BackendLockError("upstream commits must come from coordinates")
            if spec.coordinate_key != _EXPECTED_UPSTREAMS[spec.name]:
                raise BackendLockError("upstream coordinate key is inconsistent")

    def with_server_products_commit(self, commit: str) -> "BackendLock":
        return replace(
            self,
            server_products=replace(self.server_products, commit=commit),
        )


@dataclass(frozen=True)
class ResolvedRepository:
    name: str
    identity: str
    url: str
    commit: str
    object_store: Path


@dataclass(frozen=True)
class ResolvedBackend:
    lock: BackendLock
    repositories: tuple[ResolvedRepository, ...]

    def repository(self, name: str) -> ResolvedRepository:
        matches = tuple(value for value in self.repositories if value.name == name)
        if len(matches) != 1:
            raise BackendLockError(f"resolved repository is not unique: {name}")
        return matches[0]

    def to_document(self) -> dict[str, object]:
        return {
            "schema": "cpk.current-backend-sources.v1",
            "repositories": [
                {
                    "name": repository.name,
                    "identity": repository.identity,
                    "commit": repository.commit,
                }
                for repository in sorted(
                    self.repositories, key=lambda value: value.name
                )
            ],
        }


@dataclass(frozen=True)
class MaterializedBackend:
    root: Path
    repositories: Mapping[str, Path]

    def path_for(self, name: str) -> Path:
        try:
            return self.repositories[name]
        except KeyError as error:
            raise BackendLockError(
                f"materialized repository is unavailable: {name}"
            ) from error


def load_backend_lock(path: Path) -> BackendLock:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BackendLockError("backend lock could not be read") from error
    if not isinstance(raw, dict):
        raise BackendLockError("backend lock must be an object")
    _require_exact_keys(raw, {"schema", "server_products", "upstreams"}, "lock")
    server = _parse_repository(raw["server_products"], root=True)
    server_raw = raw["server_products"]
    assert isinstance(server_raw, dict)
    coordinates = server_raw.get("coordinates_path")
    if not isinstance(coordinates, str):
        raise BackendLockError("coordinates path must be text")
    upstream_raw = raw["upstreams"]
    if not isinstance(upstream_raw, list):
        raise BackendLockError("upstreams must be a list")
    upstreams = tuple(_parse_repository(value, root=False) for value in upstream_raw)
    return BackendLock(
        schema=_required_text(raw["schema"], "schema"),
        server_products=server,
        coordinates_path=Path(coordinates),
        upstreams=upstreams,
    )


def resolve_local_backend(
    lock: BackendLock,
    repository_paths: Mapping[str, Path],
) -> ResolvedBackend:
    expected_names = {lock.server_products.name, *(spec.name for spec in lock.upstreams)}
    if set(repository_paths) != expected_names:
        raise BackendLockError("local repository path inventory is inconsistent")

    server_path = Path(repository_paths[lock.server_products.name]).resolve()
    _verify_repository(server_path, lock.server_products)
    server_commit = lock.server_products.commit
    assert server_commit is not None
    _require_git_object(server_path, server_commit)
    commits = _read_upstream_commits(lock, server_path, server_commit)

    resolved: list[ResolvedRepository] = []
    for spec in (*lock.upstreams, lock.server_products):
        path = Path(repository_paths[spec.name]).resolve()
        _verify_repository(path, spec)
        commit = server_commit if spec is lock.server_products else commits[spec.name]
        _require_git_object(path, commit)
        resolved.append(
            ResolvedRepository(
                name=spec.name,
                identity=spec.identity,
                url=spec.url,
                commit=commit,
                object_store=path,
            )
        )
    return ResolvedBackend(lock=lock, repositories=tuple(resolved))


class ClonedBackend(AbstractContextManager["ClonedBackend"]):
    """Temporary exact Git object stores for CI and isolated validation."""

    def __init__(self, lock: BackendLock) -> None:
        self.lock = lock
        self.root = Path(tempfile.mkdtemp(prefix="cpk-current-backend-clones-"))
        self._backend: ResolvedBackend | None = None

    @property
    def backend(self) -> ResolvedBackend:
        if self._backend is None:
            raise BackendLockError("cloned backend has not entered its context")
        return self._backend

    def __enter__(self) -> "ClonedBackend":
        try:
            server = self.lock.server_products
            server_commit = server.commit
            assert server_commit is not None
            server_path = self.root / server.name
            _fetch_exact_repository(server, server_commit, server_path)
            commits = _read_upstream_commits(self.lock, server_path, server_commit)
            paths: dict[str, Path] = {server.name: server_path}
            for spec in self.lock.upstreams:
                path = self.root / spec.name
                _fetch_exact_repository(spec, commits[spec.name], path)
                paths[spec.name] = path
            self._backend = resolve_local_backend(self.lock, paths)
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args: object) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def clone_backend(lock: BackendLock) -> ClonedBackend:
    return ClonedBackend(lock)


@contextmanager
def materialize_backend(
    backend: ResolvedBackend,
) -> Iterator[MaterializedBackend]:
    root = Path(tempfile.mkdtemp(prefix="cpk-current-backend-sources-"))
    paths: dict[str, Path] = {}
    try:
        for repository in backend.repositories:
            if repository.name in paths:
                raise BackendLockError("source archive destination collision")
            destination = root / repository.name
            archive = _git_bytes(
                repository.object_store,
                ("archive", "--format=tar", repository.commit),
                "source archive failed",
            )
            if len(archive) > MAX_ARCHIVE_BYTES:
                raise BackendLockError("source archive exceeds bounded size")
            safe_extract_tar(archive, destination)
            paths[repository.name] = destination
        yield MaterializedBackend(root=root, repositories=dict(paths))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def safe_extract_tar(content: bytes, destination: Path) -> None:
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise BackendLockError("source archive destination collision")
    destination.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:") as archive:
            members = archive.getmembers()
            for member in members:
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or not path.parts
                    or any(part in {"", ".", ".."} for part in path.parts)
                ):
                    raise BackendLockError("unsafe archive path")
                normalized = path.as_posix()
                if normalized in seen:
                    raise BackendLockError("duplicate archive path")
                seen.add(normalized)
                if not (member.isdir() or member.isfile()):
                    raise BackendLockError("unsupported archive member type")
                target = (destination / Path(*path.parts)).resolve()
                if destination.resolve() not in (target, *target.parents):
                    raise BackendLockError("unsafe archive path")
            for member in members:
                path = PurePosixPath(member.name)
                target = destination / Path(*path.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise BackendLockError("source archive file is unreadable")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
    except (tarfile.TarError, OSError) as error:
        raise BackendLockError("source archive could not be extracted") from error


def _parse_repository(value: object, *, root: bool) -> RepositorySpec:
    if not isinstance(value, dict):
        raise BackendLockError("repository declaration must be an object")
    expected = {"name", "identity", "url", "commit", "coordinates_path"} if root else {
        "name",
        "identity",
        "url",
        "coordinate_key",
    }
    _require_exact_keys(value, expected, "repository")
    url = _required_text(value["url"], "repository URL")
    _require_production_url(url)
    identity = _required_text(value["identity"], "repository identity")
    if _identity_from_url(url) != identity.lower():
        raise BackendLockError("repository URL and identity disagree")
    return RepositorySpec(
        name=_required_text(value["name"], "repository name"),
        identity=identity,
        url=url,
        commit=(
            _required_text(value["commit"], "repository commit") if root else None
        ),
        coordinate_key=(
            None
            if root
            else _required_text(value["coordinate_key"], "coordinate key")
        ),
    )


def _read_upstream_commits(
    lock: BackendLock,
    server_path: Path,
    server_commit: str,
) -> dict[str, str]:
    raw = _git_text(
        server_path,
        ("show", f"{server_commit}:{lock.coordinates_path.as_posix()}"),
        "coordinate manifest is unavailable",
    )
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise BackendLockError("coordinate manifest is malformed") from error
    if not isinstance(document, dict) or document.get("schema") != COORDINATE_SCHEMA:
        raise BackendLockError("coordinate manifest schema is unsupported")
    upstreams = document.get("upstreams")
    if not isinstance(upstreams, dict):
        raise BackendLockError("coordinate manifest upstreams are malformed")
    expected_keys = {spec.coordinate_key for spec in lock.upstreams}
    if set(upstreams) != expected_keys:
        raise BackendLockError("coordinate manifest upstream inventory is inconsistent")
    commits: dict[str, str] = {}
    for spec in lock.upstreams:
        assert spec.coordinate_key is not None
        commit = upstreams.get(spec.coordinate_key)
        if not isinstance(commit, str):
            raise BackendLockError("coordinate manifest commit is malformed")
        _require_commit(commit)
        commits[spec.name] = commit
    return commits


def _verify_repository(path: Path, spec: RepositorySpec) -> None:
    if not path.is_dir():
        raise BackendLockError(f"repository path is unavailable: {spec.name}")
    origin = _git_text(
        path,
        ("remote", "get-url", "origin"),
        "repository origin is unavailable",
    ).strip()
    if spec.url.startswith("file:"):
        if origin != spec.url:
            raise BackendLockError("repository clone URL mismatch")
        return
    if _identity_from_url(origin) != spec.identity.lower():
        raise BackendLockError("repository identity mismatch")


def _fetch_exact_repository(
    spec: RepositorySpec,
    commit: str,
    destination: Path,
) -> None:
    _run_git(destination.parent, ("init", "--quiet", destination.as_posix()))
    _run_git(destination, ("remote", "add", "origin", spec.url))
    _run_git(
        destination,
        ("fetch", "--quiet", "--depth=1", "origin", commit),
        error="locked commit fetch failed",
    )
    _require_git_object(destination, commit)


def _require_git_object(path: Path, commit: str) -> None:
    _require_commit(commit)
    result = _run_git(
        path,
        ("cat-file", "-e", f"{commit}^{{commit}}"),
        check=False,
    )
    if result.returncode != 0:
        raise BackendLockError("locked commit is missing")


def _git_text(
    path: Path,
    arguments: tuple[str, ...],
    error: str,
) -> str:
    result = _run_git(path, arguments, check=False)
    if result.returncode != 0:
        raise BackendLockError(error)
    return result.stdout.decode("utf-8")


def _git_bytes(
    path: Path,
    arguments: tuple[str, ...],
    error: str,
) -> bytes:
    result = _run_git(path, arguments, check=False)
    if result.returncode != 0:
        raise BackendLockError(error)
    return result.stdout


def _run_git(
    path: Path,
    arguments: tuple[str, ...],
    *,
    check: bool = True,
    error: str = "Git command failed",
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ("git", "-C", path.as_posix(), *arguments),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise BackendLockError(error)
    return result


def _identity_from_url(url: str) -> str:
    value = url.strip()
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    else:
        parsed = urlparse(value)
        if parsed.hostname != "github.com":
            raise BackendLockError("repository URL is not an allowed GitHub URL")
        path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not _IDENTITY.fullmatch(path):
        raise BackendLockError("repository URL identity is malformed")
    return path.lower()


def _require_production_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise BackendLockError("repository URL must use HTTPS GitHub provenance")
    _identity_from_url(url)


def _require_commit(value: str) -> None:
    if not _COMMIT.fullmatch(value):
        raise BackendLockError("repository coordinate must be a full Git commit")


def _require_safe_relative_path(path: Path, label: str) -> None:
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise BackendLockError(f"{label} must be a safe relative path")


def _require_exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    label: str,
) -> None:
    if set(value) != expected:
        raise BackendLockError(f"{label} fields are incomplete or unknown")


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise BackendLockError(f"{label} must be bounded text")
    return value
