Source: [test_current_graph_advancement.py](../../../../control-plane-kit-operations/tests/test_current_graph_advancement.py).

The established tests own guarded atomic advancement, idempotency, authorization,
run evidence and concurrency. The #2078 case requires a rejected precondition to
preserve its public exception class, graph and run history while carrying only
closed, correlated diagnostic evidence. It does not replay the command or infer
rollback/acceptance from the diagnostic. Initial focused target-red evidence is
pending through the ordinary Operations Docker suite.
