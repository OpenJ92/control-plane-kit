Source: [extraction_parity/tests/test_differential_language.py](../../../../extraction_parity/tests/test_differential_language.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

The fixture builds observation dictionaries with synthetic source digests,
fixed command text, hashed in-memory stdout/stderr, caller-supplied behavior and
no artifacts. It does not run that command. Tests cover closed observation/policy
shapes, invalid incidental values, normalization idempotence and equivalence of
two explicitly tagged ports while retaining different raw observations.

A response-body change stays visible even when all incidental kinds are allowed.
Result tests reject changed raw digest, claimed status, normalized values and
extra fields. A timeout becomes infrastructure-failure rather than behavior
drift. Matching completed exit code 2 and different log text still compare as
equivalent, explicitly protecting audit-output versus behavior semantics.

The test named for pinned source identity checks digest syntax, not actual source
provenance. The cases do not cover artifact comparison, byte/depth/item limits,
all reserved-syntax paths, base64 spelling, Python numeric-type equality or
mutating retained raw objects. Producer honesty about incidental tagging also
lies outside these fixtures. Decoding equality is not an immutable codec round
trip or proof that a subprocess ran.

The complete 135-line owner and [comparison implementation](../differential.py.md)
were read, with selected observation-runner capture/evidence boundaries. No test,
capture command or artifact generation was executed for this companion.
