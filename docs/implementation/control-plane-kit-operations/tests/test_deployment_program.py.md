Source: [test_deployment_program.py](../../../../control-plane-kit-operations/tests/test_deployment_program.py).
Maintain this companion alongside its source.

This existing contract suite checks typed immutable deployment references,
commands, closed fields, redaction and effect-free source/import boundaries.
The #1875 translation appends only proposed_graph_id to the exact preparation
field list and admits the shared pure proposed-name validator/error import from
graph_authoring. All prior fields, referenced type identities, invalid-input,
redaction and forbidden-effect assertions remain intact. Importing that owner
must remain acyclic and perform no IO; it does not execute its command service.
Invalid proposed-name cases also require detached bounded contract errors for
overlong, credential-shaped and hostile string-subclass inputs.

This schema expectation follows the reviewed target interface, not a new graph
synthesis or draft capability. Omitted command descriptors/fingerprints keep
their exact old shapes, while new behavior is protected by planning/preparation
and adapter targets. Executable evidence belongs to the ordinary owning suite.
