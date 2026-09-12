Source: [control-plane-kit-operations/tests/test_runtime_dispatcher_bootstrap.py](../../../../control-plane-kit-operations/tests/test_runtime_dispatcher_bootstrap.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

These five unittest methods exercise the package-exported [bootstrap configuration](../../../../control-plane-kit-operations/src/control_plane_kit_operations/runtime_dispatcher_bootstrap.py), not a concrete runtime dispatcher. They protect disabled rendering, sorted/deduplicated allowed kinds, the process-value/authority distinction and representative invalid text inputs. An AST check reads the owner file and rejects direct imports of the interpreter package and named SDK roots; it is not a transitive import or runtime security proof.

The fixtures are configuration values only. No provider is contacted, and an accepted enum is not evidence that its interpreter is installed, authorized or healthy. Execute validation through the owning Operations Docker-backed suite when authorized; this companion was produced by source reading, not a test invocation.
