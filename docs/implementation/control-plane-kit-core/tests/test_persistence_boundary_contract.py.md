Source: [control-plane-kit-core/tests/test_persistence_boundary_contract.py](../../../../control-plane-kit-core/tests/test_persistence_boundary_contract.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Six persistence-declaration tests

The tests inspect the canonical factory's store/handoff kind coverage and full
mutation subject-by-phase coverage. They round-trip the descriptor and reject
one extra top-level repository field and one unknown store enum value.

Store assertions select history, observation and ledger ordering policies, check
all canonical stores' no-secret/no-commit flags and Operations enforcement, and
exclude two database-library words from the descriptor. Handoff assertions check
the canonical UoW/caller-transaction requirements, absent Core driver/DDL powers
and Operations ownership. These declarations are not actual database behavior.

Mutation assertions inspect selected derived-resource candidate/visibility
choices and every canonical mutation's no-value/Operations flags. Three negative
descriptor mutations reject accepted secret values, a Core database-driver flag
and published mutation values. No mutable holder is instantiated by those tests.

The coverage assertions compare sets; they do not prove unique entries. Duplicate
collections, accepted noncanonical failure-policy/candidate combinations,
handoffs with both transaction flags false, collection size, every missing kind
and each malformed nested field are outside this file's assertions. It does not
verify real ordinal ordering, idempotency, rollback, cleanup or secret handling.

Full 154-line test and full
[persistence-contract owner](../src/control_plane_kit_core/operations/persistence.py.md)
read, with selected imported enum/service/transaction and actual store-bundle
context. No executable validation, database driver, schema operation, provider
call or source change accompanies this documentation.
