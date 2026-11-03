Source: [test_current_graph_advancement.py](../../../../control-plane-kit-operations/tests/test_current_graph_advancement.py).

The established tests own guarded atomic advancement, idempotency, authorization,
run evidence and concurrency. The #2078 case drives the existing authorized
advancement adapter and real owner, requiring closed, correlated failure evidence
while preserving graph and run history. The diagnostic entry shares one underlying computation with the existing
exception-raising entry; it does not replay the command or infer acceptance from
the diagnostic. The earlier unrun exception-attribute test was superseded to
avoid stale exception evidence. Focused target-red evidence is pending through
the ordinary Operations Docker suite. The final adapter-driven target at
`dad0f03164703eaa10c51f1f7ccddc808f70fedf` ran once in 5.833 seconds and failed at
the expected missing bounded application-error assertion; production was unchanged.
The implementation checks direct exception identity and cancellation, closed
serialized evidence and unrepresentable-correlation fallback. Real PostgreSQL
commit refusal and a close failure after successful commit both return unverified
outcomes while their authoritative graph/history states differ. Green is pending.
