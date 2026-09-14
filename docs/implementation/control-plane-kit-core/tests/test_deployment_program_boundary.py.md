Source: [control-plane-kit-core/tests/test_deployment_program_boundary.py](../../../../control-plane-kit-core/tests/test_deployment_program_boundary.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Composition laws and a bounded import guard

The seven tests build generic textual service bindings for every role. They
check complete coverage and planning lookup; reject a missing recovery role and
duplicate planning role; show that reversed input normalizes to enum order;
inspect selected absent packaging words; and reject `fastapi-process` and a
`dockerfile` parameter as concrete vocabulary examples.

The stage tests assert the canonical six-stage order, corresponding roles and
all-true handoff flags from the factory. They round-trip that pipeline through
its descriptor and reject reversed stages and an execution stage assigned to
the lifecycle role. They do not exercise every malformed descriptor,
predecessor mismatch, custom false-handoff declaration or falsy default input.

The final test parses each Python file under Core's `operations` directory with
Python's `ast` module and rejects imports whose top-level module matches a
seven-name blacklist. This is a static import guard. It neither executes the
CPK deployment language nor proves complete package ownership, absence of
dynamic imports, transitive dependencies or external side effects. Product
separation is not exhaustively established merely by the test's name.

Full 164-line file, including its fixture helper and AST traversal, and full
[service-language owner](../src/control_plane_kit_core/operations/services.py.md)
read. These are construction, descriptor and selected source-structure laws;
they make no claim that a real application service, approval, durable handoff,
database or provider ran. No executable validation was performed.
