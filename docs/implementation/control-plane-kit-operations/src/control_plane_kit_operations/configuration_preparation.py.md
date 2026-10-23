Source: [configuration_preparation.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/configuration_preparation.py).
Maintain this companion alongside its source.

#1923 adds original configuration provenance inside Operations. Closed provenance evidence and capacity values; complete evidence never grants reuse or release authority.

The first-start owner rederives selected material under the existing lifecycle lock before IDs or writes. Exact original replay reads retained intent and protective claims without selecting current catalogs. Public evidence states are complete, unavailable, or capacity; none authorize dispatch, reuse, acceptance, deletion, or release.

Transport reservations include statements, scalar markers, returned values and relational identities. Failed reads retain their reservation. Caches stay within one UoW; command accounting can span the coordinator and nested start while fresh mutable authority is reread.

B1 provenance is integrated. B2 adds `ConfigurationAcceptedBinding` and
`ConfigurationCurrentEvidence`: immutable observations of complete current
membership and requested direct source/birth proofs. The manifest count may
exceed returned bindings for a narrowed request. Non-complete states contain no
partial coordinates or proofs. These values confer no mutation or cleanup
authority. The first public-read focused gate passed all 38 selected tests plus
compilation/import; complete B2 validation remains pending. Provider behavior
remains outside this module's authority.

## #1993 shared record policy

`MAX_CONFIGURATION_EVIDENCE_RECORDS = 8192` is the single owner of the
cumulative logical-command record allowance. The capacity predicate and both
PostgreSQL remaining-record calculations consume it. This replaces the earlier
4096 cumulative allowance for all joined configuration reads and mutations,
including advancement, start, transfer and cleanup. Independent native limits
are unchanged. Literal policy tests retain exact 8192/8193 boundaries.

The accounted-byte law remains `value_octets + 128*records + 16*scalar_markers +
256*statements`, capped at 16 MiB; the separate acceptance snapshot cap remains
3 MiB. Doubling records can increase SQL/CPU work; these limits do not establish
latency or process-memory guarantees. Larger retained histories can still
refuse. No authority, transaction, history freshness or retry semantics change.
Focused owner validation passed at `1dacbc4a`; full owning validation is pending.
Servers #252 acceptance remains separately required after dependency adoption.
