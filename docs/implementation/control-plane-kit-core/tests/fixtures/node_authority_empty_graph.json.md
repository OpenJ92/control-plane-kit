Source: [control-plane-kit-core/tests/fixtures/node_authority_empty_graph.json](../../../../../control-plane-kit-core/tests/fixtures/node_authority_empty_graph.json).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This canonical JSON fixture contains three product instances in a Docker
runtime, with no edges, endpoint bindings or per-node runtime-authority delivery
fields. Empty refers to delivery declarations, not to node count. The runtime
still names local-docker authority; that must not imply access delivered to
each child.

[The owning test](../test_node_authority_delivery.py.md) compares freshly
encoded graph text to these exact bytes, checks decode/re-encode preservation
and compares their digests. Its source records capture from historical source
0c53845; this companion read the fixture and test, not that historical checkout
or a historical executable run.

The OCI image and descriptor digest are fixture identity data. They do not
prove image publication, installed processes, custody behavior or Docker
resources. Update only with an intentional change to the governing canonical
descriptor law, not to silence a failing compatibility witness.
