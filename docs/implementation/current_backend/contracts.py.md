Source: [current_backend/contracts.py](../../../current_backend/contracts.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This owner turns the [backend contract manifest](../current-backend.contracts.json.md)
and materialized repository paths into static architecture/composition evidence.
The motivation in [Current Backend Validation](../../../current_backend/README.md)
is to check one selected multi-repository backend before its executable stages.
It reads source, metadata and descriptor bytes; it does not import those packages,
invoke acceptance scripts, contact registries or mutate a runtime.

The JSON loader rejects unexpected fields, requires the five named distributions,
checks inventory references/uniqueness, and validates lexical path/glob and text
shapes. These checks belong to load_backend_contracts, not the frozen dataclass
constructors: directly constructed or replaced values do not automatically
receive the loader's validation. MaterializedBackend.path_for supplies paths
and raises BackendLockError for missing names. Exact Git selection and safe
archive extraction belong to [source_lock](source_lock.py.md); a directly
constructed path mapping is not itself provenance or a filesystem sandbox.

Source ownership is checked over declared globs and three conventional source
layouts. Overlapping ownership and unowned candidates are findings, but this is
not every Python file anywhere in a repository. Absolute syntactic imports and
base project.dependencies build separate internal-edge sets; their union must
respect allowed dependencies and remain acyclic, and imported internal edges
must be declared. Longest-prefix matching assigns owners. Relative/dynamic
imports, optional dependency edges and arbitrary Python resolution are outside
that graph. Pin-surface scanning is separate: matching GitHub archive URLs
anywhere in the named text, including comments or extras, must agree with the
Servers coordinate manifest. This is regex/text correspondence, not dependency
resolution or complete URL/subdirectory validation.

Product checks compare coordinate/catalogue/packaged product IDs and selected
coordinate projections, descriptor path coverage and parent ownership, descriptor
byte hashes, packaged entry equality and the packaged-catalogue checksum. The
descriptor payload is hashed rather than interpreted as a Core product. Image
references/digests are compared as recorded data, without registry reads or
publisher verification. Descriptor-path coverage uses sets; do not infer a
general one-product-per-path uniqueness law from the finding text.

Protocol checks require a uniquely named top-level class and directly declared
method names on both sides. They do not compare signatures, inheritance, return
values or behavior. Acceptance checks require existing command/residue files,
declared classification/caller/flags, selected script markers and absence of a
mock-word regex match. Markers can occur in comments, as the fixture deliberately
demonstrates. A passing acceptance name is static eligibility evidence, not an
executed HTTP/MCP scenario, executable-bit check or provider permission.

Success returns sorted edge/name inventories and a source count. Findings are
deduplicated/sorted and at most 64 are displayed, with an omitted count; that
caps finding count rather than input size, processing cost or total message
bytes. Selected read/parse failures become findings, but exceptions are not
universally normalized and loader causes can survive. Treat repository text
and extracted trees as trusted inputs, not a hostile request/redaction boundary.

The [runner](../../../current_backend/runner.py) turns BackendContractError into
a failed contract stage, or records selected success counts before later stages.
[Contract tests](tests/test_contracts.py.md) exercise a synthetic four-repository
fixture. They preserve these structural laws without establishing real package,
image, cryptographic, provider or restart/history behavior.
