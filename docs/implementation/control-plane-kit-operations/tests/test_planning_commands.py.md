Source: [test_planning_commands.py](../../../../control-plane-kit-operations/tests/test_planning_commands.py).
Maintain this companion alongside its source.

The existing real PostgreSQL suite protects desired graph/action atomicity,
replay ordering, concurrent idempotency, stale pointer CAS, session admission,
planning and late failure rollback. #1875 adds seven focused identity laws:
exact proposed name/descriptor persistence and replay, byte-identical legacy
omission fingerprint, fresh same-name refusal even for equal bytes, bounded
foreign-workspace collision, unrelated action-uniqueness failure preservation,
invalid name rejection and a concurrent cross-workspace primary-key race.

The race forwards real database operations and synchronizes both graph INSERTs
before execution; it does not emulate a store or decide a winner. Full owner row
snapshots protect rollback, while the race checks one durable winner and no
loser graph/projection/pointer/action write. Existing fixtures and assertions
remain intact. These are targets for missing proposed-ID behavior until the
ordinary owning gate records green; no provider or receiver effects occur.
