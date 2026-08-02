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

## Current Violations And Handoff

The #1330 audit found these pre-contract shapes:

- desired-graph checks replay before workspace serialization, then locks the
  session only while allocating an ordinal;
- planning locks workspace before reading session and later locks session for
  ordinal allocation;
- terminal/manual commands read session and replay evidence before their late
  ordinal lock;
- approvals use a late session lock and recheck, but lack command-identity
  serialization before the initial replay read;
- admission locks workspace and its own idempotency scope before the session;
- lifecycle paths lock execution request/run truth before session action order;
- realized-projection publication and current-graph advancement lock workspace
  or runtime truth before session action order.

#1331 must add only the named command-identity and exact session `FOR UPDATE`
primitives needed by this contract. #1332 and #1333 apply them to desired graph,
planning, and terminal transitions. #1334 audits every remaining mapped writer
and proves the order with independent Postgres connections.

Execution leases, worker fencing, and uncertain external effects remain owned by
#1092. Executable `DeploymentProgram` composition remains owned by #1096.
