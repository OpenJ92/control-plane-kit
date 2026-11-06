Source: [test_http_sample_compatibility.py](../../../../control-plane-kit-operations/tests/test_http_sample_compatibility.py).

These tests construct immutable owner-record values and actual Core graphs and
codecs. They test only the read-only registered compatibility calculation, not
database admission, installed state, signers or gateway conformance. Inputs expose
no store, credential resolver, runtime or transport effects. The existing execution
policy must remain denied. CPK/Secrets shapes retain liveness-only receivers and
both original checks; a distinct control:8000/application:8080 case prevents
confusing the gateway receiver destination with each check's local destination.
Cross-workspace/product/projection/slot, partial coverage, wrong-purpose trust and
misrouted receiver origins refuse. Recording decoder output is compatibility only.
