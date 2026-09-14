Source: [control-plane-kit-core/tests/test_node_control_topology.py](../../../../control-plane-kit-core/tests/test_node_control_topology.py).
Maintain this document alongside its source file. When required architecture phrases, their governing decisions or evidence limits change, verify and update this companion in the same change.

This 79-line file contains four documentation guards. Each reads
[NODE_CONTROL_TOPOLOGY.md](../../../../control-plane-kit-core/docs/NODE_CONTROL_TOPOLOGY.md)
and requires a tuple of exact substrings to be present. It imports only `Path`
and `unittest`; it does not import Core, compile a graph, construct contracts,
publish routes or exercise an interpreter.

The required text falls into four groups:

- Future `/__control` and legacy `/__deploy` prefix labels, plus the sentence
  forbidding silent unauthenticated or divergent route families.
- SDK distribution/repository/optional FastAPI-extra names, the single
  `ControlPlaneVariable[State, Command, Result]` extension model, and phrases
  naming rejected reflection and a second durable handler interface.
- Operator-to-server, server-to-gateway and gateway-to-workload boundaries,
  distinct transit/end-to-end grant labels, and selected forbidden substitutions.
- Router, weighted balancer and discovery adopter names, plus the historical
  #1148 pure-contract handoff and selected excluded actions.

The first group does not assert the installed route prefix or compatibility
behavior. The second does not install the SDK or inspect its dependency graph.
The third does not authenticate a caller or exercise grant verification. The
fourth does not establish adopter readiness or execute the referenced issue.
Presence checks do not verify statement ordering, uniqueness, surrounding
meaning or absence of contradictory prose elsewhere in the document.

The governing note combines a historical pre-implementation freeze/source dry
run with later reference, control-surface and public-material contracts. Its
issue-status and handoff language must not be mistaken for current rollout
evidence. Selected present [route source](../../../../control-plane-kit-core/src/control_plane_kit_core/control_routes.py)
still declares `DEFAULT_CONTROL_PREFIX` as `/__deploy`, while the
[delegation-key enum](../../../../control-plane-kit-core/src/control_plane_kit_core/delegation_keys.py)
contains additional node-control purposes beyond gateway probe. Those source checks are contextual reads for
this companion, not assertions made by this test. The prose remains an
architecture contract whose semantic consistency needs human review.

The [graph-reference test companion](test_node_control_graph_references.py.md)
describes separate executable assertions about nominal roles and wire values.
Even that suite does not prove admitted graph membership. Static control-surface
compilation, authentication, relay behavior, replay and provider execution need
their own owning tests and evidence.

Review depth: full four-test file and governing topology note, plus selected
current prefix/key-purpose source for the historical/current distinction. No
SDK, deployed server, complete graph implementation or current issue-status
audit is claimed. No tests, imports, database or provider actions ran. This
artifact guard preserves written decisions; it grants no mutation, route
publication or retry authority.
