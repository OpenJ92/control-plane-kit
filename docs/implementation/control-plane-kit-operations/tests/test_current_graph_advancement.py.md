Source: [test_current_graph_advancement.py](../../../../control-plane-kit-operations/tests/test_current_graph_advancement.py).

The established tests own guarded atomic advancement, idempotency, authorization,
run evidence and concurrency. The #2078 case requires the explicit diagnostic
owner entry to return only closed, correlated failure evidence while preserving
graph and run history. It shares one underlying computation with the existing
exception-raising entry; it does not replay the command or infer acceptance from
the diagnostic. The earlier unrun exception-attribute test was superseded to
avoid stale exception evidence. Focused target-red evidence is pending through
the ordinary Operations Docker suite.
