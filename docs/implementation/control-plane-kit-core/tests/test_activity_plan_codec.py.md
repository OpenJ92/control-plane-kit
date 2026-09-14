Source: [control-plane-kit-core/tests/test_activity_plan_codec.py](../../../../control-plane-kit-core/tests/test_activity_plan_codec.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Plan representation laws

These pure tests exercise the public
[plan codec](../src/control_plane_kit_core/planning/codec.py.md) over typed plans.
They cover round-trip retention, deterministic rendering after typed
normalization, operation/target variants, selected review subjects and retained
risk/impact labels. Rejection rows cover unknown schema/operations/review
reasons, non-JSON extras, wrong container/target shapes, extra fields and invalid
dependency graphs.

The permutation tests construct normalized ActivityPlan values before encoding;
they do not establish that arbitrary reordered wire descriptors are accepted.
The review diagnostic contains only a subject/reason, so its selected absence
assertions do not prove a general secret-value sanitizer. The full-plan tests
also must not be credited as strict admission tests for the separate exported
single-operation helpers.

All behavioral bodies were read; companion creation ran no suite. These laws
cover representation, not persisted approval identity, transactions or runtime
execution. Canonical compensation and its required version-one field receive
additional coverage in
[test_compensation_planning.py](../../../../control-plane-kit-core/tests/test_compensation_planning.py).
