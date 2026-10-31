Source: [test_postgres_health_signing_authority.py](../../../../control-plane-kit-operations/tests/test_postgres_health_signing_authority.py).
Maintain this companion alongside its source.

#1977 adds a compatible verify-only key after original health preparation and
proves reload preserves the exact preparation/grants/intervals and history.
Revoking the original active signer then refuses live reload without rewriting
that history. This distinguishes existing-grant compatibility from a fresh
installation's complete current-snapshot requirement.

Current signing tests use V2 requests/grants, receiver audiences and the independently passed gateway target. Their request-substitution helper builds coherent V2 negative values only. Existing actor/fence, exact plan/pins/approval, independent key/use chain, progression, interval and comparator-refusal laws remain; all run over the actual V2 first-start fixture.

O2 / #1883 implementation validation is pending on [PR #1915](https://github.com/OpenJ92/control-plane-kit/pull/1915).
The reviewed target-only checkpoint does not establish implementation green.
