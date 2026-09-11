Source: [extraction_parity/differential.py](../../../extraction_parity/differential.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This module defines observation, normalization-policy and comparison-result
documents and compares them without executing either implementation. Observation
decoding checks closed fields, reference/successor role, command/process shape,
payload hashes/lengths, behavior values and unique artifact names. source_digest
must have canonical SHA-256 syntax, but the decoder does not inspect source bytes
or prove that the claimed command produced the observation.

capture_payload emits base64, decoded-byte length and SHA-256 for supplied bytes.
Decoding verifies those bytes and their digest after strict base64 decoding;
it does not re-encode and compare the text to enforce a unique base64 spelling.
Each captured payload is capped at 1 MiB, while behavior has separate depth,
per-container item, text and canonical-serialized-size limits. Artifact and
command lists are separately bounded. These component checks are not a single
1-MiB document or pre-decoding allocation limit: payload decoding and behavior
traversal/serialization precede some size checks. Errors are not universally
normalized for arbitrary malformed Python inputs.

Only explicit $incidental values can be normalized. The four allowed kinds have
their own value checks: integer ports, nonblank generated IDs, offset-bearing
timestamps and constrained container names. A policy chooses which kinds become
$normalized markers; unselected tags remain intact. Normalized input is accepted
by normalize_value for idempotence, but rejected in raw observations. Ordinary
untagged fields remain in the comparison. The producer still decides what to tag:
the language cannot establish that labeling a particular value incidental is
semantically justified.

If either process is not completed, the result is infrastructure-failure and
behavior/artifact comparison is skipped. Otherwise, differences come from exit
code, normalized behavior and full artifact records sorted by name. Matching
nonzero exits can be equivalent. stdout/stderr are retained as audit payloads
but do not determine equivalence; command text and implementation identities
also remain provenance declarations rather than equality requirements. Behavior
uses Python value equality, not canonical-JSON byte or strict numeric-type
equality. Artifact comparison includes record metadata/encoded payload fields,
not just decoded content hashes.

Results retain both raw observations, normalized values, policy, identities and
canonical hashes of the raw observation documents. decode_result recomputes
normalization, comparison status/differences and those hashes. This establishes
internal consistency of the supplied document, not authenticated producer
identity or independent execution. Decoders return their input dictionaries;
the constructed result retains the original raw objects, so this is not an
immutable snapshot against later caller mutation. Raw audit data is not redacted
by this language.

The [observation runner](../../../extraction_parity/runner.py) supplies capture,
declared-secret handling and producer-supplied source identity, then hashes a
validated result into an evidence record. [Parity validation](validation.py.md)
later checks the serialized index's shape/status references rather than rerunning
that capture. [Focused language tests](tests/test_differential_language.py.md)
protect selected normalization, drift and consistency laws; no executable or
live evidence was produced by this documentation review.
