Source: [test_support/package_integrity.py](../../../test_support/package_integrity.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This root-owned tool inspects Python ASTs and gate text for selected package-test
integrity laws. It returns immutable report values containing sorted/deduplicated
findings, recognized test identities, mock locations and matched skip approvals.
valid means no findings, not that unittest ran or every assertion is meaningful.
The [Core gate](../../../control-plane-kit-core/test.sh) runs its self-tests and
uses the scanner; the [Operations gate](../../../control-plane-kit-operations/test.sh)
also invokes it against that package's explicit roots. These callers determine
coverage. The tool does not automatically scan every repository file or the
backend runner's own tests.

Python discovery walks the supplied roots, omitting __pycache__. The scan flags
syntactic mutable control_plane_kit/pytest imports and recognizes TestCase-like
class bases, including selected direct unittest aliases. It reports nested
classes, test classes in undiscoverable filenames, top-level test functions,
pass/ellipsis-only methods after an optional docstring, and sole-pass exception
handlers in test roots. Harmless pass statements in unrelated helper/context
bodies are not blanket violations. Mock imports and selected call names are
recorded, not automatically rejected. These are syntax heuristics, not full
Python name resolution, inherited/dynamic discovery or an assertion-quality
proof; a matching name can be incidental, and unrecognized forms need review.

Recognized unconditional skips and literal boolean conditions are rejected.
Other recognized conditional skips require an exact class/method identity and
literal reason matching the approval list. Parsing checks entry shapes, nonblank
identities, unique identities and nonblank reasons of at most 240 characters;
unused approvals become stale findings. Missing approval files mean no approvals.
Conditions are not executed. This is repository test-policy data, not operator
permission or a runtime authorization mechanism.

The gate check is an uppercase-name regex over all lines, including comments;
it is not a shell interpreter and does not recognize every proof-changing option.
The CLI defaults to src, tests, test.sh and tests/approved_skips.json beneath the
chosen package root. It prints one contract=1 count summary followed by mock
locations/findings, then returns 0 or 1. The [backend runner](../current_backend/runner.py.md)
recognizes that exact summary format and also requires successful package exit.
Counts are static recognized identities/locations, not executed-test totals.

Selected read/parse failures become findings. Path containment conversions,
some decoding/read errors and other exceptional cases are not universally
normalized. Source files, approval lists, traversal and diagnostic output have
no general byte/count budget, and findings can contain paths and raw parser/error
text. Inputs are trusted repository trees; this tool is not a hostile-input
sandbox, secret scrubber or runtime-effect checker.

[The root self-tests](tests/test_package_integrity.py.md) protect targeted
positive/negative cases, including aliases and allowed helper bodies. Keep
those root-specific assertions and the consumer's selected checker revision in
view when changing shared-looking copies in other repositories. Source review
of this companion did not execute the scanner or package gates.
