# Session Command Serialization

Status: accepted law card for HARDEN.TESTS.PARITY.SESSION-SERIALIZATION #1330.

## Scope

Every durable command that appends an operation action for an existing session
uses one ordering contract. The machine-readable inventory is
`control-plane-kit-operations/session-command-serialization.json`.

```text
serialize command identity
  -> read and replay existing durable result
    -> lock and re-read authoritative session lifecycle
      -> lock workspace graph truth when required
        -> lock command-specific dependent truth
          -> allocate the action ordinal while session remains locked
            -> write domain truth and action
              -> one application-owned commit
```

`start-operation-session` is the only pre-session exception. Its
workspace/idempotency advisory lock serializes creation before a session row
exists. It creates ordinal one in the same transaction as the session.

## Outcomes

- Same session, idempotency key, and canonical intent returns the exact original
  durable result, even after graph-pointer movement or session termination.
- Reusing the key with different intent is an explicit idempotency conflict.
- Distinct identities racing on one pointer do not become retries. One command
  wins; the other reports the truthful stale pointer or lifecycle state.
- A nonterminal command without replay evidence must observe `OPEN` while
  holding the session row lock.
- Close and cancel are write-once. No later nonterminal action may receive an
  ordinal after either terminal action.
- Domain truth and operation-action evidence commit or roll back together.

The contract does not promise concurrency inside one session. Session commands
serialize deliberately. Commands for independent sessions retain concurrency.

## Lock Roles

1. `command-identity`: a narrow transaction advisory lock for one session and
   idempotency key. It exists because the action row may not exist yet.
2. `session-lifecycle`: the authoritative session row selected `FOR UPDATE` and
   re-read after identity serialization.
3. `workspace-graph`: the workspace row selected `FOR UPDATE` only for commands
   that depend on graph pointers or revisions.
4. `dependent-truth`: narrowly named plan, approval, execution request, run, or
   rotation rows selected after session and workspace locks.
5. `action-ordinal`: `MAX(ordinal) + 1` allocated only while the session lock is
   held.

The order is acyclic. No command may acquire a workspace or dependent row and
then acquire its session row. Immutable foreign-key linkage may be read without
a lock to locate the session, but every mutable fact must be re-read after the
ordered locks are held.

## Enforced Owners

The #1331 through #1334 implementation applies the contract to:

- desired-graph edits and realized-projection publication;
- planning and close/cancel transitions;
- manual operation actions;
- plan and gateway-key-rotation approval requests and decisions;
- execution admission;
- claim and run lifecycle transitions;
- current-graph advancement; and
- gateway-key-rotation overlap and retirement projection derivation.

Lifecycle and advancement may read immutable execution-request or run linkage
to locate the owning session. They then acquire command identity, resolve
replay, lock the session, and re-read mutable request/run truth. Rotation
projection uses the same locator rule for immutable rotation-to-workspace
linkage, then locks workspace before rotation truth.

The machine-readable contract test discovers every operation-action writer and
requires identity, session, and ordinal calls in source order. Dedicated
Postgres tests prove both legal terminal race outcomes, exact duplicate replay,
conflicting reuse rejection, write-once close/cancel, independent-session
concurrency, monotonic ordinals, rollback, and bounded repeated runs without
deadlock.

Execution leases, worker fencing, and uncertain external effects remain owned by
#1092. Executable `DeploymentProgram` composition remains owned by #1096.
