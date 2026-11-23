# Implementation companions

A companion explains what a source file owns, which contracts it relies on,
and where to verify those claims before changing it. To find one, append the
repository-relative source path and `.md` to `docs/implementation/`, keeping
the original file extension. This convention comes from the
[implementation companion decision](https://github.com/OpenJ92/control-plane-kit/issues/1799).

For example, the [PostgreSQL readiness companion](control-plane-kit-core/src/control_plane_kit_core/postgres_readiness.py.md)
links back to [its Core source](../../control-plane-kit-core/src/control_plane_kit_core/postgres_readiness.py).
The note says that password validation preserves the supplied text.
To check that claim, follow `postgres_readiness_password_supported` to its
length and printable-ASCII checks, then read the [password-domain test](../../control-plane-kit-core/tests/test_postgres_readiness.py).
That predicate can establish whether the supplied value is supported.
Evidence of which version reached a running database belongs to the installation.

Before relying on a note, read the relevant entry point, its contract-bearing
imports and the test or decision it cites. When a dependency supplies the
behavior, check the version the consumer actually selects. If the note and
source disagree, record the discrepancy on the owning issue. Keep observed
behavior, intended behavior and open questions distinguishable.

When a change alters the documented behavior, update the companion in the same
PR. Start a new companion with a relative link to its source and a reminder
to maintain it alongside that source and its selected dependencies. Add, move
or remove the note with the corresponding source file. A dependency pin
change can also change the meaning of a note. If the contract still holds,
record the verification in the existing PR decision log.
