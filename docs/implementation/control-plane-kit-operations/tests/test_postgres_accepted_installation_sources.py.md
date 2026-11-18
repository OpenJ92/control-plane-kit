Source: [test_postgres_accepted_installation_sources.py](../../../../control-plane-kit-operations/tests/test_postgres_accepted_installation_sources.py).
Maintain this companion alongside its source.

The existing #1987 tests exercise original installation-source publication,
carry, replay, generated-allocation provenance and bounded reads through real
Operations/PostgreSQL owners with recording provider ports.

The #2076 case adds a first-attempt-connected initial deployment with separate
management and application ingresses and four managed receiver identities.
Management bootstrap enables health; application ingress follows application
health. The real compiler, commands, coordinator, effect records and advancement
produce all history. Recording connectors match their incoming selected token
to its owner-produced allocation, retaining the existing consumer-grant and
installation-receipt checks. No successful history or acceptance is seeded.

The test requires both original connector provenance chains and all four
receiver first-acceptances to commit with the planned current graph/projection.
Advancement preserves the entire recorded external-effects tuple and original
claim identities. An actual failure is inspected once without replay; existing
late/deferred rollback tests remain the negative evidence. This is Operations
composition evidence, not provider conformance or an explanation of the live
HTTP error. The first focused run passed the baseline but the new case failed
fixture setup because the cloned connector retained the API block role identity.
The fixture now changes both node and block role identities, matching the existing
managed-application clone pattern. The second focused run passed the baseline
and new-case setup, then exposed a test assertion comparing activity IDs directly
with dependency records. The assertion now compares their predecessor IDs,
preserving every required edge. Neither failure reached advancement or provides
behavioral reproduction evidence.

The corrected focused gate at source commit
`75bf3f43e75ca4cb0c92976ec48435ffd3e6606c` passed both selected methods:
`test_generated_acceptance_binds_actual_terminal_and_full_prefix` and
`test_two_managed_ingress_connectors_advance_with_original_sources`.
It reported two tests in 176.990 seconds, OK, and whole-gate exit zero after
integrity, behavior, compile and clean-import checks. Dedicated test resources
were verified absent. Log SHA-256:
`c84325d3e10050c342914716b2ff0df297dfe31cecc85e2704ede0485d7bde00`.
Meridian independently reviewed the source and retained test evidence as PASS.
This documentation follow-up does not change the tested source. The result is
focused Operations composition nonreproduction, not full-suite acceptance,
provider conformance, a diagnosis of the live error or permission to replay it.
See the [governing result](https://github.com/OpenJ92/control-plane-kit/issues/2076#issuecomment-6593705646)
for the prior failed attempts and next boundary.
