Source: [control-plane-kit-core/tests/fixtures/node_control_public_material_v1.json](../../../../../control-plane-kit-core/tests/fixtures/node_control_public_material_v1.json).
Maintain this document alongside its source file. When vectors, law labels, projection metadata or consumer assumptions change, verify and update this companion in the same change.

This language-neutral JSON fixture supplies selected admission and rejection
examples for `cpk.node-control.public-material.v1`. Its declared projection is
`literal-and-one-pass-ascii-percent`. It is test input, not a registry of real
credentials, endpoint configuration, graph membership or execution authority.

The general `accepted`/`rejected` sections exercise public text. Accepted examples
include ordinary DNS-looking names, credential-related words without credential
envelopes, and host/port-like strings with zero, out-of-range or suffixed ports.
Rejected examples cover selected authorization/bearer and credential-assignment
forms, private-key armor, compact tokens, URLs, protocol-relative addresses,
valid host/port forms, IP literals, localhost forms and single-percent-encoded
envelopes. Credential and endpoint law labels are expected diagnostic matches.
The file is a finite set of examples, not a universal publicness detector.

The separate `authority_reference_accepted`/`authority_reference_rejected`
sections describe the stricter reference grammar as well as envelope rules.
For example, `custom+tcp://router.internal`, `//router.internal/control` and
`[2001:db8::1]:443` receive `bounded reference` failures there: shape admission
fails before endpoint classification. `https://router.internal/control` has
admissible reference syntax and reaches the endpoint rule. The general text
vectors classify these endpoint forms without the reference grammar gate.
Whitespace-bearing benign descriptions therefore do not automatically become
valid authority references.

The actual Python consumers read different sections:

- [test_node_control_public_material.py](../test_node_control_public_material.py.md)
  checks schema/projection metadata, feeds general vectors through a variable
  description, and changes only a workload grant's issuer for authority vectors.
- [test_node_control_public_wire_ownership.py](../test_node_control_public_wire_ownership.py.md)
  passes general vectors directly to the shared classifier. It does not consume
  the authority sections or assert fixture metadata; its rejected-label branch
  treats any non-credential label as the endpoint expectation.
- The selected issuer test in
  [test_node_control_surface_read_authority.py](../../../../../control-plane-kit-core/tests/test_node_control_surface_read_authority.py)
  reuses both authority sections for a surface-read grant. Its constructor calls
  the same reference classifier through the surface-read error boundary.

The [_node_control_public_wire.py companion](../../src/control_plane_kit_core/_node_control_public_wire.py.md)
explains literal plus one-pass ASCII projection, credential precedence and
shape precedence. The fixture does not exercise recursive decoding, every type
or size bound, every consumer field, or all lexical contexts. Passing vectors
does not establish trusted issuer identity or prove that admitted text contains
no secret. The [public contract](../../../../../control-plane-kit-core/docs/NODE_CONTROL_PUBLIC_MATERIAL.md)
assigns semantic publicness and graph provenance to authenticated producers and
their consuming boundaries; this file cannot establish those facts.

Review depth: full fixture, full public-material test, retained full shared
owner/ownership test, selected surface-read fixture use and actual issuer guard,
and relevant command-language constructors/codec/representation paths. No
non-Python implementation, complete node-control owner or complete surface-read
suite audit is claimed. No tests, imports, database or provider actions ran.
