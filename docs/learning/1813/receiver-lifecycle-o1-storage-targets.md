# O1.B storage target checkpoint

Status: corrected ordinary causal-red independently passed at `820c9a4`.
Application source requires North's separate bounded release.
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
| multiple members/duplicate identity | B1/B3 new | Complete two-member positive and fresh duplicate-receiver negative; returned binding records are immutable. |
| wrong guard | L1 strengthened | All four writers require exact active store/UoW/workspace ownership, including the expired original store with its own guard. |
| witness transitions | B4 new | Acceptance and retirement pair fields, write once, replay exactly and cannot clear/replace. |
| deferred original binding | U1 strengthened/B2 new | Commit-time failure removes graph/projection/action/draft/origin together. |
| exception after commit request | U1 strengthened | Late caller failure rolls back both new indexes and all prior command writes. |
| tombstone/history | B5 new/C1 strengthened | Tombstone retains provenance; current verification retains rows; references prohibit deletion. |
| exact catalog/FK/CHECK | C1 strengthened/B1–B4 new | Exact keys, references, nullability and deferred edge; actual PostgreSQL rejects crossed witnesses/scopes and malformed shapes. |
| derived digest drift | C2 strengthened/B3 new | Current reentry rejects inconsistent derived truth without repair. |
| exact pre-B baseline | C2 strengthened | Removing only the B delta in the disposable namespace produces unchanged rejection with no reentry DDL/DML. |

The fixture uses existing real Core codecs and PostgreSQL UoWs. It does not
model admission, effect history, clocks, providers or a replacement storage
service. Missing new interfaces are asserted inside test bodies, preserving
collection and attributing intended red to absent B behavior. Real C command
late-action/CAS and all-entrance closure remain C2/C3 integration targets.

Meridian's first review held `d425734` for three causal-integrity corrections:
prove deferred failure reached the commit request, cover all witness writers and
expired same-store guards, and isolate provenance/source rejection from other
FK failures. The correction adds an explicit commit-reached marker, valid
pending/accepted guard matrices, a structurally valid replacement origin and
real same-workspace crossed projection/draft cases. It also asserts loser
rollback, an empty-member positive and a complete multi-member case. No run or
source release is inferred from these target corrections.

Independent review must check target coverage and fixture validity before the
ordinary `./control-plane-kit-operations/test.sh` causal-red run, with clean
architecture-testing `7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef`. Apparatus or
collection failure earns no behavioral credit and invokes the existing stop
rule. A target run is recorded below; no green result, source release or
acceptance is claimed.

## First ordinary run and fixture correction

Reviewed target `85e6f4ac85e42de766d8a73c7f8fc853d755c062` ran the ordinary
Operations wrapper with clean pinned architecture-testing. It exited 1 after
1,879 tests in 2,462.748 seconds: 17 failures and one error. The 1,861 existing
tests passed. Sixteen new assertions identified missing `receiver_introduction`;
one identified the absent introduction relation. Those are missing-behavior
evidence only at the first reached assertion, not credit for later target laws.

The multi-member fixture errored before B: renaming its node to `other` retained
`BlockSpec.role_id='api'`. Core's graph codec requires map key, node ID and block
role ID to agree (`topology/codec.py`, `_validate`). The correction
renames the role alongside the node and runtime membership. It preserves the
multi-member/duplicate-identity target and all graph validation assertions.
This fixture error is not intended red; the correction has not been executed.

Original log: `/tmp/cpk-1897-red-85e6f4a.log`, SHA256
`1ec4be3fbe3e337c738e199592e6ca4794f62bc3e24932217e8697e8c710a737`.
The exact `cpk-1897-target-red-postgres` container and `cpk-1897-target-red`
network were both confirmed absent after the wrapper exited. Collection and
package integrity completed; the wrapper's post-test compile/import stages did
not run after failure. No alternate runner, host Python or schema probe was used.
Meridian reviews this correction and terminal classification; North decides the
next validation/source boundary. No clean full target-red or B acceptance is
claimed for the first run.

## Corrected ordinary causal-red

The independently reviewed correction at
`820c9a4462bbea066b267caf722a85bf8d0ba44b` ran the same ordinary suite with the
clean pinned prerequisite: exit 1, 1,879 tests in 2,398.305 seconds, 18 failures
and zero errors. All 1,861 existing tests passed. Seventeen new assertions
identified the missing receiver read API; one identified the missing relation.
The multi-member case now passes Core graph validation and reaches that same
intended missing-API assertion. This supersedes the correction's unexecuted
status above, without changing the first run's mixed-result classification.

Meridian issued CAUSAL-RED PASS on this exact coordinate and log. Credit remains
limited to first missing surfaces; deeper target assertions must still become
green during implementation. Package integrity and behavioral collection
completed, but post-test compile/import stages did not run after failure.
The exact `cpk-1897-target-red2-postgres` container and `cpk-1897-target-red2`
network were confirmed absent afterward. Source and prerequisite remained clean.

Log `/tmp/cpk-1897-red-820c9a4.log`, SHA256
`f7645158b1b817d3aeceed960916a03fcdcc27e2b51c694005502de197aa338d`.
[Durable PR evidence](https://github.com/OpenJ92/control-plane-kit/pull/1905#issuecomment-5887846955)
and [issue evidence](https://github.com/OpenJ92/control-plane-kit/issues/1897#issuecomment-5887847245).
North owns the next source release. B remains staged and jointly unaccepted
with C; no intermediate deployment or C activation follows from target-red.

Security/data/history: all database mutations are confined to owning-suite
disposable schemas; public-key fixture material is not a credential. Global
conflicts must not expose foreign owners. No provider, retained tunnel/DNS/token,
live schema, migration/backfill/reset or destructive product action is involved.
