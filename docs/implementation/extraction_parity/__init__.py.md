Source: [extraction_parity/__init__.py](../../../extraction_parity/__init__.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This docstring-only marker identifies extraction/parity tooling as separate from
the deployable runtime package. It exports no facade and performs no discovery,
validation, artifact writing or provider work on import. Executable entrances
select the individual modules, such as the reference inventory or validation
runner; their contracts and effects belong to those owners. Do not infer that
importing this package executes a frozen suite or establishes migration parity.
