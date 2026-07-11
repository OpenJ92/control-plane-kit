# Definition Of Done

A change is done when:

- the graph model remains serializable,
- examples still run without Docker or network access,
- `python3 -m unittest` passes,
- README or docs are updated when public concepts change,
- runtime effects are isolated behind runtime interpreters,
- security-sensitive values are not written into descriptors or examples.
