# Agent implementation companions

Decision and full rollout: [CPK #1799](https://github.com/OpenJ92/control-plane-kit/issues/1799).

These documents are navigation and reasoning aids for agents changing CPK.
They explain why an owner exists, what contracts a change must respect, and
where to verify those claims. They are not a second implementation, a complete
behavior audit, or authority to execute effects.

## Locate the companion

Append the repository-relative source path and `.md` to `docs/implementation/`.
Keep the original suffix: `current_backend/source_lock.py` maps to
`docs/implementation/current_backend/source_lock.py.md`.

Every companion starts with its source link and maintenance reminder:

> Source: `<repository-relative source path>`.
> Maintain this document alongside its source file. When the source or relevant
> imported contracts change, verify and update this companion in the same change.

Use a working relative link to the source. Do not copy local checkout paths or
private deployment coordinates into repository documents.

For the `current_backend/source_lock.py.md` companion above, the first line is:

```markdown
Source: [current_backend/source_lock.py](../../../current_backend/source_lock.py).
```

## What belongs here

Scale the note to the file's meaning; no word count or mandatory empty sections.

- Purpose, motivation and owned versus delegated responsibility.
- Change-sensitive inputs, outputs, rejection conditions and information loss.
- Selected contract-bearing imports or dynamic dependencies: name the symbol,
  the relied-upon constraint, its owner and why this file depends on it.
- Effects, authority, transactions, cleanup and uncertain outcomes where they
  matter to changing the owner.
- Governing test/law and decision links, evidence limits and known unknowns.

Do not transcribe signatures, schemas, constant tables, algorithms or assertion
catalogues. A short export/test note is complete when it identifies THIS file's
role, law or fixture assumption and points to its actual owner. A generic stub
or an unexamined confident summary is not authored coverage.

Distinguish governing requirements, recorded rationale, observed source
behavior and inference. Unknown original motivation is acceptable. If source,
tests or a historical decision disagree, state the discrepancy and link its
owning issue; neither implementation accidents nor old prose silently become
the intended contract.

## Relations spanning files

Keep one explanation per actual shared contract, information transformation or
effect/evidence boundary. Prefer an existing accurate architecture or testing
note; add a narrowly owned relation note only when a useful explanation is
missing. Name the owner and participating files. Each companion states its local
obligation and links the relation; a distant "see Core" link is insufficient.

Selected reverse links to consequential consumers are useful but non-exhaustive.
They are not a dependency registry. Search actual source and dependency
declarations when changing a contract, including injected services, codecs,
registries, lazy imports, command-line and selected image interfaces.

Verify the version/ref actually selected by each consumer. An upstream change
triggers impact discovery and a handoff; do not rewrite a pinned consumer to an
unadopted API. Review companions when a dependency pin changes even if local
implementation does not. Cross-repository adoption uses linked PRs, not a claim
that separate repositories update atomically.

## Maintenance within the existing work loop

Before design or implementation, read the relevant companion, owner source and
contract-bearing dependencies. Stop the traversal at an understood and verified
contract boundary; do not recursively read every import.

Review the actual source diff (`git diff --name-status` against the PR base)
before handing work off. Create, move or remove
companions with covered source files. Update affected meaning in the same PR.
If documented meaning is unchanged, record "companion reviewed; no semantic
update needed" in the existing decision log. Reading a header alone does not
establish freshness, and a source hash does not establish dependency compatibility.

Use one sentence in the existing PR log, as applicable: "Companions: updated
<paths>; reviewed/no meaning change <paths>; pending <scope>." Reviewers compare
the changed paths and inspect consequential claims against source. Do not create
a separate per-file report, timestamp/hash-only commit, documentation test matrix
or CI framework. Use existing Git and source search initially. A later small
read-only correspondence helper would be justified by actual repeated missed
paths, and could check existence only, never semantic correctness.

Known pending initial coverage does not block unrelated work. Bring newly
touched relevant files and their notes current. The reminder, paired edits and
review reduce omissions; none guarantees that prose is always correct.

## Coverage and review

Inventory actual tracked files at the branch being documented. Include authored
implementation, meaningful exports, schemas, build/process/configuration and
evidence-owning harnesses, plus concise test navigation. Classify exclusions
such as generated, vendor, cache and lock content with their authoritative owner
or generation source. Do not repurpose historical architecture inventories.

Initial coverage uses `pending`, `authored`, `reviewed` and `excluded` with reasons.
This is rollout/navigation accounting, not a perpetual freshness ledger. Update
entries when coverage changes; routine semantic review remains in PR history.
Initial source coordinates and review depth belong in the batch issue/PR;
mandatory per-companion hash stamps are not required. An accurately documented
unknown or defect can be authored and reviewed while its issue remains open.
Coverage status neither resolves that defect nor authorizes an unsafe next step.

Authors verify source and selected dependencies. A different reviewer checks
consequential authority, data, effect, cleanup and evidence claims directly and
samples routine navigation at an honestly reported depth. Complete the agreed
scope in coherent batches. The first mixed calibration is the first batch, not
permission to call a partial pilot complete.

On subsequent real work, record a concrete navigation hit, missed contract or
stale claim in the normal handoff. Use that evidence to improve or shorten notes;
do not claim this approach already prevents errors or create a metrics programme.
