# Current operator workspace inventory

Issue: [#2041](https://github.com/OpenJ92/control-plane-kit/issues/2041).

`WorkspaceInventoryReadService` consumes an authenticated operator and the
configured installation identity. It ignores embedded static grants. Service
and worker principals are refused before parsing cursors or opening storage.
Limits default to 25 and accept 1–100. Responses contain only workspace ID,
display name (at most 512 Unicode characters, with ellipsis for longer names),
lifecycle and an optional opaque next cursor. Stored names are unchanged.

The existing `WorkspacePermissionStore.readable_workspaces` joins workspaces
to current grants for the exact installation, issuer, subject and principal
kind with workspace-read scope. SQL filters before `limit + 1`, orders IDs
bytewise, and checks the anchor in the same statement snapshot. All bounded
matching grants, including lookahead and anchor, use the existing typed decoder.
No schema change, metadata access, global inventory or provider effect occurs.

The unsigned cursor binds a seek ID to a digest of installation, full identity
and the durable-only profile. It is attacker-chosen seek input, not authority.
Malformed/foreign-scope cursors return 400. Hidden, missing and revoked anchors
return the same 409 and suppress candidate rows. Missing/mismatched installation
truth, malformed matching rows and storage failure return fixed 503 errors,
never a partial page. An operator with no readable workspace receives an empty
page, including an installation administrator with no ordinary read grants.

Pagination is live keyset traversal, not a snapshot across requests. Revoked
rows disappear on later pages; new IDs after the anchor may appear. Earlier
insertions require first-page refresh. Hidden-row changes cannot affect page
items, cursors, lookahead or errors. `next_cursor = null` ends this traversal;
it does not establish historical completeness.

Owning evidence: `test_postgres_workspace_inventory.py` in the normal Operations
Docker/PostgreSQL suite. First tests-only gate at `3bf7cede` established only a
missing interface. The first reader gate at `692cd7d2` passed 12 focused tests,
compile/import and cleanup. At `499541fd`, all 26 selected inventory, shared
admission and policy-coverage tests passed, followed by compile/import and
cleanup. Both reviewers gave source PASS to that adapter and the NUL-cursor
correction. Full package/combined CI acceptance remains separate. The shared
application binds this reader for the
canonical HTTP/MCP inventory route in the exact dynamic durable-only profile.
Consumer Servers adoption and live API evidence remain separate.
