Source: [control-plane-kit-core/tests/test_extract_d_closeout.py](../../../../control-plane-kit-core/tests/test_extract_d_closeout.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This extraction closeout witness checks that Core exports cpk-server handoff
contracts while excluding named process packaging artifacts and process module
names. It also verifies specific historical closeout wording in
[EXTRACT_D_TOPOLOGY.md](../../../../control-plane-kit-core/docs/EXTRACT_D_TOPOLOGY.md).

The handoff contracts live in Core as language; the server image/process belongs
outside Core. Phrase/path absence checks are historical architectural evidence,
not a current portfolio state machine or proof that a server image was produced.
Do not reinterpret the old mandatory-stop language as a new approval gate for
unrelated current work.
