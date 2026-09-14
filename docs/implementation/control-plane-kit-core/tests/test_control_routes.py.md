Source: [control-plane-kit-core/tests/test_control_routes.py](../../../../control-plane-kit-core/tests/test_control_routes.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Static protocol catalogue checks

The [route catalogue](../src/control_plane_kit_core/control_routes.py.md) tests
compare exact common-status names/methods/paths/scopes and the set of all twelve
route families. Target and discovery families receive exact method/path
comparisons. Lookup preserves the canonical family object's identity for enum
and string inputs, while an unknown name raises KeyError.

The logs descriptor is compared as JSON-friendly data, and two control_path
examples protect default and configured prefixes. These assertions do not
exercise every route scope, path-template behavior, malformed direct route
construction or all prefix forms.

Full 134-line test file and full route owner read. No route is served or called;
authentication, payload validation, bounded logs, network exposure, mutation
and provider behavior are outside this static catalogue test's evidence.
