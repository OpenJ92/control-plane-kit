Source: [control-plane-kit-operations/tests/test_delegation_signing_keys.py](../../../../control-plane-kit-operations/tests/test_delegation_signing_keys.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This class uses the owning Operations suite's Postgres URL, installs schema,
truncates workspaces with CASCADE, inserts two workspaces and admits two references.
Synthetic public PEM constants and reference handles supply key identities; no
private generation/signing provider is used. Even the fail-on-access connection
case belongs to this class with database setup, so the file is not a standalone
pure test harness.

The [service/store cases](../src/control_plane_kit_operations/delegation_signing_keys.py.md)
check repeat registration, workspace isolation, verify-only admission and support
for workload surface-read purpose. Changed public material/private reference under
the same key ID conflicts. Sequential activation preserves the previous key as
verify-only overlap; retirement followed by revocation removes it from the returned
verification set. The permission case rejects registration with only read scope;
it is not a complete matrix of all lifecycle scopes.

Temporal cases reject an impossible date at every store mutation before touching
the supplied fail-on-access connection, including no chained cause/context. Further
cases reject malformed duplicate/lifecycle replays and show that invalid activation
does not demote the existing signer, while invalid retirement/revocation preserve
the previous record. Direct SQL fixtures install seconds/microseconds/null values
and change the connection timezone; all exercised selectors must return canonical
UTC, including active, unambiguous-active, workspace and verification views.

The restart-safe test performs a fresh unit-of-work read, not a server or database
process restart. These tests do not establish concurrent activation/revocation,
fresh provider authority across transactions, cryptographic private/public pairing,
external verifier propagation or cleanup of private custody. Descriptor assertions
exclude PEM for a fixture; they are not a general secret-redaction audit.

The full 683-line owner, 391-line service and 469-line store were read alongside
the actual Core key, temporal and unit-of-work contracts. No executable test,
database operation, key generation or live credential access ran during this
documentation review.
