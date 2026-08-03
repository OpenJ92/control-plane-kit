"""Current multi-repository backend validation support."""

from .source_lock import (
    BackendLock,
    BackendLockError,
    MaterializedBackend,
    RepositorySpec,
    ResolvedBackend,
    ResolvedRepository,
    clone_backend,
    load_backend_lock,
    materialize_backend,
    resolve_local_backend,
)

__all__ = [
    "BackendLock",
    "BackendLockError",
    "MaterializedBackend",
    "RepositorySpec",
    "ResolvedBackend",
    "ResolvedRepository",
    "clone_backend",
    "load_backend_lock",
    "materialize_backend",
    "resolve_local_backend",
]
