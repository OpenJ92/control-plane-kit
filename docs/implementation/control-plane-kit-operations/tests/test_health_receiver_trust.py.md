Source: [test_health_receiver_trust.py](../../../../control-plane-kit-operations/tests/test_health_receiver_trust.py).
Maintain this companion alongside its source.

Pure tests preserve immutable/redacted family facts, exact nominal references/purposes, issuer limits, reconstructed fingerprints and exact-string key IDs, independent16-versus17 bounds and duplicate key ID/material refusals. Gateway facts come from the transit fixture port; own-health fact values come from the actual shared Core wire decoder. Both exercise the same public Operations value constructors.

The registry test now covers transit-only exact product/purpose uniqueness, empty composition, immutable profile and wrong descriptor/reference selection. The public workload-binding rejection is additionally exercised by the shared Postgres targets. Removed own-health registry entries are not a compatibility path; they are intentionally invalid. These are value/provenance tests, not credential verification or live receiving evidence.
