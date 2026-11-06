Source: [test_secret_selection_preparation.py](../../../../../control-plane-kit-operations/tests/test_secret_selection_preparation.py).
Maintain this companion alongside its source.

The real-store preparation fixture observes open UoWs and supplies only metadata
observations. Existing tests own matching observations, current source and
authority fences, carry-forward and original lineage. The public command class
added for #1989 proves that those laws compose with the durable action owner:
exact selection/action publication, scope/reader refusal, original replay after
later edits/revocation, changed-choice conflict, reference race, final same-key
winner and metadata-callback key substitution. It does not emulate a provider
or Operations store. Existing unprepared/saved-selection refusals remain covered
by test_secret_selection_authoring_entrances.py.
