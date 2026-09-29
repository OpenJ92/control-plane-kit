# O1.B storage target checkpoint

Status: targets drafted for Meridian review; no execution or application source.
Base: joint freeze `847a7053def484e516e7214e9e563ed9f376b491`. Branch
`codex/1897-receiver-storage-targets` targets the unreleased B+C collection.
North released B targets only. B remains jointly unaccepted with C; private
storage tests establish integrity, never admission, action meaning, retirement
eligibility or permission to execute a receiver.

## Interface refinement

The two public graph-owned reads retain their frozen names and arguments.
Private graph-owned persistence names refine the provisional B responsibilities:

```python
graphs._reserve_receiver_introductions(
    graph, projection, *, action_id, session_id, draft_id=None, lifecycle_guard)
graphs._persist_receiver_bindings(graph, projection, *, lifecycle_guard)
graphs._record_receiver_first_acceptance(
    workspace_id, receiver_id, *, action_id, session_id, lifecycle_guard)
graphs._record_receiver_retirement(
    workspace_id, receiver_id, *, action_id, session_id, lifecycle_guard)
```

`graph` and `projection` are existing immutable record values. Their IDs,
workspace/source, digest and exact material must agree with stored originals;
self-consistent caller substitutions are insufficient. All row material is
derived. A guarded writer never treats syntactic action/session validity as C
authority. Returned immutable record fields match the frozen table columns.
`ReceiverLifecycleStorageError` is the bounded structural/material refusal;
`ReceiverLifecycleStorageConflict` subclasses it and reports exactly
`receiver identity is unavailable` for reservation/witness conflicts. Neither
retains input-bearing exception causes, contexts or diagnostic attributes.
No additional StoreBundle service, root export, caller row DTO or C token.

`receiver_storage_schema_contract.py` freezes the exact proposed B catalog
delta: two relations, 23 text columns, seven primary/unique keys with their
seven supporting indexes, 15 foreign keys and seven CHECK constraints. Only
the original-binding FK is deferred. All FK actions are NO ACTION. The expected
delta is thus 29 constraints and seven indexes; there are no execution indexes.
Existing exact-schema tests and accepted baseline hashes remain unchanged at
target-red. During separately released implementation, the full new semantic
catalog and SQL fingerprints must be recorded and independently reviewed;
they cannot be invented before that exact source exists. No existing exactness,
query-only reentry, rollback, old-baseline or no-repair assertion is relaxed.

## Law mapping and evidence boundary

| Target | Governing card | Observable law |
| --- | --- | --- |
| exact bytes/origin reload/replay | B1/B2/B3, G2 strengthened | Selected raw-byte digest and declaration identity, exact graph/projection/origin, immutable values and tenant-scoped lookup survive. |
| later binding | B2/B3 new | New selected material retains its original introduction. No C continuation claim. |
| global race | B1 new | Two real workspace transactions produce one retained owner and bounded foreign-safe loser. |
| provenance substitution | B2 new | Changed graph/action/draft cannot rewrite original tuple. |
| stored-material substitution | B3 new | Changed caller graph/projection bytes, phantom projection and wrong stored source refuse. |
| scope/slot refusal | B3 new | Material must agree with selected artifact and actual graph scope. |
| member completeness | B3 new | Missing, extra, crossed or foreign membership refuses, never returns a partial set. |
| wrong guard | L1 strengthened | Exact active store/UoW/workspace ownership precedes writes. |
| witness transitions | B4 new | Acceptance and retirement pair fields, write once, replay exactly and cannot clear/replace. |
| deferred original binding | U1 strengthened/B2 new | Commit-time failure removes graph/projection/action/draft/origin together. |
| exception after commit request | U1 strengthened | Late caller failure rolls back both new indexes and all prior command writes. |
| tombstone/history | B5 new/C1 strengthened | Tombstone retains provenance; current verification retains rows; references prohibit deletion. |
| exact catalog/FK/CHECK | C1 strengthened/B1–B4 new | Exact keys, references, nullability and deferred edge; actual PostgreSQL rejects crossed witnesses/scopes and malformed shapes. |
| derived digest drift | C2 strengthened/B3 new | Current reentry rejects inconsistent derived truth without repair. |

The fixture uses existing real Core codecs and PostgreSQL UoWs. It does not
model admission, effect history, clocks, providers or a replacement storage
service. Missing new interfaces are asserted inside test bodies, preserving
collection and attributing intended red to absent B behavior. Real C command
late-action/CAS and all-entrance closure remain C2/C3 integration targets.

Independent review must check target coverage and fixture validity before the
ordinary `./control-plane-kit-operations/test.sh` causal-red run, with clean
architecture-testing `7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`. Apparatus or
collection failure earns no behavioral credit and invokes the existing stop
rule. This document records no run, green result, source release or acceptance.

Security/data/history: all database mutations are confined to owning-suite
disposable schemas; public-key fixture material is not a credential. Global
conflicts must not expose foreign owners. No provider, retained tunnel/DNS/token,
live schema, migration/backfill/reset or destructive product action is involved.
