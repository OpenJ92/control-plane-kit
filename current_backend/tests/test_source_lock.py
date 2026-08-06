from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from current_backend.source_lock import (
    BackendLock,
    BackendLockError,
    RepositorySpec,
    clone_backend,
    load_backend_lock,
    materialize_backend,
    resolve_local_backend,
    safe_extract_tar,
)


class BackendSourceLockTests(unittest.TestCase):
    def test_repository_lock_uses_one_server_products_root_coordinate(self) -> None:
        root = Path(__file__).resolve().parents[2]
        lock = load_backend_lock(root / "current-backend.lock.json")

        self.assertEqual(
            lock.server_products.commit,
            "37cabc3243269e63750cc23b298706e8297b1ee3",
        )
        self.assertEqual(
            {spec.coordinate_key for spec in lock.upstreams},
            {
                "control_plane_kit_commit",
                "control_plane_kit_interpreters_commit",
                "control_plane_kit_secrets_commit",
            },
        )

    def test_one_server_products_lock_derives_all_upstream_commits(self) -> None:
        with _backend_fixture() as fixture:
            resolved = resolve_local_backend(fixture.lock, fixture.paths)

        self.assertEqual(
            {
                repository.name: repository.commit
                for repository in resolved.repositories
            },
            {
                "control-plane-kit": fixture.commits["control-plane-kit"],
                "control-plane-kit-interpreters": fixture.commits[
                    "control-plane-kit-interpreters"
                ],
                "control-plane-kit-secrets": fixture.commits[
                    "control-plane-kit-secrets"
                ],
                "control-plane-kit-servers": fixture.commits[
                    "control-plane-kit-servers"
                ],
            },
        )

    def test_local_materialization_uses_git_objects_not_dirty_checkout(self) -> None:
        with _backend_fixture() as fixture:
            core = fixture.paths["control-plane-kit"]
            (core / "payload.txt").write_text("dirty\n", encoding="utf-8")
            status_before = _run(("git", "status", "--porcelain"), cwd=core)
            resolved = resolve_local_backend(fixture.lock, fixture.paths)
            with materialize_backend(resolved) as materialized:
                payload = (
                    materialized.path_for("control-plane-kit") / "payload.txt"
                ).read_text(encoding="utf-8")
            status_after = _run(("git", "status", "--porcelain"), cwd=core)

        self.assertEqual(payload, "control-plane-kit\n")
        self.assertEqual(status_after, status_before)

    def test_resolved_coordinates_are_bounded_and_sorted(self) -> None:
        with _backend_fixture() as fixture:
            document = resolve_local_backend(
                fixture.lock, fixture.paths
            ).to_document()

        self.assertEqual(document["schema"], "cpk.current-backend-sources.v1")
        self.assertEqual(
            [value["name"] for value in document["repositories"]],
            sorted(fixture.commits),
        )

    def test_clone_materialization_fetches_the_exact_locked_commit(self) -> None:
        with _backend_fixture(use_file_urls=True) as fixture:
            resolved = clone_backend(fixture.lock)
            with resolved, materialize_backend(resolved.backend) as materialized:
                payload = (
                    materialized.path_for("control-plane-kit") / "payload.txt"
                ).read_text(encoding="utf-8")

        self.assertEqual(payload, "control-plane-kit\n")
        self.assertFalse(resolved.root.exists())

    def test_repository_identity_substitution_fails_closed(self) -> None:
        with _backend_fixture() as fixture:
            substituted = _init_repository(
                fixture.root / "substituted",
                "OpenJ92/not-control-plane-kit",
                {"payload.txt": "substituted\n"},
            )
            paths = dict(fixture.paths)
            paths["control-plane-kit"] = substituted.path

            with self.assertRaisesRegex(
                BackendLockError, "repository identity mismatch"
            ):
                resolve_local_backend(fixture.lock, paths)

    def test_missing_locked_object_fails_closed(self) -> None:
        with _backend_fixture() as fixture:
            missing = "f" * 40
            lock = fixture.lock.with_server_products_commit(missing)
            with self.assertRaisesRegex(BackendLockError, "locked commit is missing"):
                resolve_local_backend(lock, fixture.paths)

    def test_failed_clone_removes_temporary_git_stores(self) -> None:
        with _backend_fixture(use_file_urls=True) as fixture:
            cloned = clone_backend(
                fixture.lock.with_server_products_commit("f" * 40)
            )
            with self.assertRaises(BackendLockError):
                with cloned:
                    self.fail("missing commit unexpectedly cloned")

        self.assertFalse(cloned.root.exists())

    def test_unknown_duplicate_and_traversing_lock_inputs_fail_closed(self) -> None:
        with _backend_fixture() as fixture:
            lock = fixture.lock

        with self.assertRaisesRegex(BackendLockError, "duplicate distributions"):
            BackendLock(
                schema=lock.schema,
                server_products=lock.server_products,
                coordinates_path=lock.coordinates_path,
                upstreams=(
                    lock.upstreams[0],
                    lock.upstreams[0],
                    lock.upstreams[2],
                ),
            )

        with self.assertRaisesRegex(BackendLockError, "safe relative path"):
            BackendLock(
                schema=lock.schema,
                server_products=lock.server_products,
                coordinates_path=Path("../coordinates.json"),
                upstreams=lock.upstreams,
            )

    def test_malformed_lock_and_coordinate_manifest_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "lock.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "cpk.current-backend-lock.v1",
                        "server_products": {
                            "name": "control-plane-kit-servers",
                            "identity": "OpenJ92/control-plane-kit-servers",
                            "url": "https://github.com/OpenJ92/control-plane-kit-servers.git",
                            "commit": "short",
                            "coordinates_path": "coordinates/server-products.json",
                        },
                        "upstreams": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BackendLockError, "full Git commit"):
                load_backend_lock(path)

        with _backend_fixture() as fixture:
            server = fixture.paths["control-plane-kit-servers"]
            (server / "coordinates" / "server-products.json").write_text(
                '{"schema":"wrong"}\n', encoding="utf-8"
            )
            bad_commit = _commit_all(server, "malformed coordinates")
            lock = fixture.lock.with_server_products_commit(bad_commit)
            with self.assertRaisesRegex(
                BackendLockError, "coordinate manifest schema"
            ):
                resolve_local_backend(lock, fixture.paths)

    def test_archive_traversal_and_duplicate_members_are_rejected(self) -> None:
        traversal = _tar_bytes((("../outside", b"bad"),))
        duplicate = _tar_bytes((("same", b"one"), ("same", b"two")))

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "destination"
            with self.assertRaisesRegex(BackendLockError, "unsafe archive path"):
                safe_extract_tar(traversal, destination)
            self.assertFalse((Path(directory) / "outside").exists())

            with self.assertRaisesRegex(BackendLockError, "duplicate archive path"):
                safe_extract_tar(duplicate, destination)

            occupied = Path(directory) / "occupied"
            occupied.mkdir()
            (occupied / "existing").write_text("owned\n", encoding="utf-8")
            with self.assertRaisesRegex(BackendLockError, "destination collision"):
                safe_extract_tar(_tar_bytes((("new", b"value"),)), occupied)

            occupied_file = Path(directory) / "occupied-file"
            occupied_file.write_text("owned\n", encoding="utf-8")
            with self.assertRaisesRegex(BackendLockError, "destination collision"):
                safe_extract_tar(
                    _tar_bytes((("new", b"value"),)), occupied_file
                )

    def test_materialized_sources_are_removed_when_consumer_fails(self) -> None:
        with _backend_fixture() as fixture:
            resolved = resolve_local_backend(fixture.lock, fixture.paths)
            root: Path | None = None
            with self.assertRaisesRegex(RuntimeError, "consumer failed"):
                with materialize_backend(resolved) as materialized:
                    root = materialized.root
                    raise RuntimeError("consumer failed")

        self.assertIsNotNone(root)
        assert root is not None
        self.assertFalse(root.exists())


class _BackendFixture:
    def __init__(self, root: Path, *, use_file_urls: bool) -> None:
        self.root = root
        repositories: dict[str, _RepositoryFixture] = {}
        for name in (
            "control-plane-kit",
            "control-plane-kit-interpreters",
            "control-plane-kit-secrets",
        ):
            repositories[name] = _init_repository(
                root / name,
                f"OpenJ92/{name}",
                {"payload.txt": f"{name}\n"},
            )

        coordinates = {
            "schema": "cpk-servers.coordinates",
            "upstreams": {
                "control_plane_kit_commit": repositories[
                    "control-plane-kit"
                ].commit,
                "control_plane_kit_interpreters_commit": repositories[
                    "control-plane-kit-interpreters"
                ].commit,
                "control_plane_kit_secrets_commit": repositories[
                    "control-plane-kit-secrets"
                ].commit,
            },
            "products": [],
        }
        server = _init_repository(
            root / "control-plane-kit-servers",
            "OpenJ92/control-plane-kit-servers",
            {
                "coordinates/server-products.json": json.dumps(
                    coordinates, sort_keys=True
                )
                + "\n",
                "payload.txt": "control-plane-kit-servers\n",
            },
        )
        repositories["control-plane-kit-servers"] = server

        def url(name: str) -> str:
            if use_file_urls:
                return repositories[name].path.as_uri()
            return f"https://github.com/OpenJ92/{name}.git"

        self.lock = BackendLock(
            schema="cpk.current-backend-lock.v1",
            server_products=RepositorySpec(
                name="control-plane-kit-servers",
                identity="OpenJ92/control-plane-kit-servers",
                url=url("control-plane-kit-servers"),
                commit=server.commit,
                coordinate_key=None,
            ),
            coordinates_path=Path("coordinates/server-products.json"),
            upstreams=(
                RepositorySpec(
                    name="control-plane-kit",
                    identity="OpenJ92/control-plane-kit",
                    url=url("control-plane-kit"),
                    commit=None,
                    coordinate_key="control_plane_kit_commit",
                ),
                RepositorySpec(
                    name="control-plane-kit-interpreters",
                    identity="OpenJ92/control-plane-kit-interpreters",
                    url=url("control-plane-kit-interpreters"),
                    commit=None,
                    coordinate_key="control_plane_kit_interpreters_commit",
                ),
                RepositorySpec(
                    name="control-plane-kit-secrets",
                    identity="OpenJ92/control-plane-kit-secrets",
                    url=url("control-plane-kit-secrets"),
                    commit=None,
                    coordinate_key="control_plane_kit_secrets_commit",
                ),
            ),
        )
        self.paths = {
            name: repository.path for name, repository in repositories.items()
        }
        self.commits = {
            name: repository.commit for name, repository in repositories.items()
        }


class _BackendFixtureContext:
    def __init__(self, *, use_file_urls: bool) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.fixture = _BackendFixture(
            Path(self._temporary.name), use_file_urls=use_file_urls
        )

    def __enter__(self) -> _BackendFixture:
        return self.fixture

    def __exit__(self, *args: object) -> None:
        self._temporary.cleanup()


def _backend_fixture(*, use_file_urls: bool = False) -> _BackendFixtureContext:
    return _BackendFixtureContext(use_file_urls=use_file_urls)


class _RepositoryFixture:
    def __init__(self, path: Path, commit: str) -> None:
        self.path = path
        self.commit = commit


def _init_repository(
    path: Path,
    identity: str,
    files: dict[str, str],
) -> _RepositoryFixture:
    path.mkdir(parents=True)
    _run(("git", "init", "--quiet"), cwd=path)
    _run(("git", "config", "user.email", "tests@openj92.dev"), cwd=path)
    _run(("git", "config", "user.name", "CPK Tests"), cwd=path)
    _run(
        ("git", "remote", "add", "origin", f"https://github.com/{identity}.git"),
        cwd=path,
    )
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    commit = _commit_all(path, "initial")
    return _RepositoryFixture(path, commit)


def _commit_all(path: Path, message: str) -> str:
    _run(("git", "add", "."), cwd=path)
    _run(("git", "commit", "--quiet", "-m", message), cwd=path)
    return _run(("git", "rev-parse", "HEAD"), cwd=path).strip()


def _run(command: tuple[str, ...], *, cwd: Path) -> str:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout


def _tar_bytes(entries: tuple[tuple[str, bytes], ...]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, content in entries:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


if __name__ == "__main__":
    unittest.main()
