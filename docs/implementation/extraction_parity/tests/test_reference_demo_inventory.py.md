Source: [extraction_parity/tests/test_reference_demo_inventory.py](../../../../extraction_parity/tests/test_reference_demo_inventory.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These two tests call [the demo validator](../demos.py.md) with one synthetic
read-interface record, two scripts and an empty fixture set. Exact supplied
script coverage is accepted; a supplied set missing one declared script is
rejected. The other test rejects an extra demo field and the semantic
normalization label http-status.

The second test's name mentions states, but its body does not mutate a state.
Likewise, exactly-once in the first test's name is not a duplicate-declaration
case. Nonempty fixture coverage, reference identity, character limits, malformed
inputs and the shell's Git selection/cleanup are outside these two cases.
The source's broader validation branches require their own evidence when changed.

The complete 58-line owner was read. Its script/documentation names and HTTP
observable are fixture text; no script, request, frozen discovery or cleanup is
performed. A green result concerns these local validation cases rather than
live demonstration success. No executable validation ran for this companion.
