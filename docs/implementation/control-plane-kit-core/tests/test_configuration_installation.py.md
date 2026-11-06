Source: [test_configuration_installation.py](../../../../control-plane-kit-core/tests/test_configuration_installation.py).

Eleven focused methods protect #1964's pure installation receipt and reader.
They cover all disposition/prior/current relationships, exact fields and digest
types, frozen detached descriptors, canonical bytes and malformed/duplicate/
noncanonical/over-bound refusal, matching Start/Reconcile contexts, successful
result-only receipt extraction, absence versus malformed presence, partial or
changed selection refusal, full result bounds and forged nominal inputs.

Existing request and invocation fixture modules provide real public requests,
ordinary results and endpoint observations. They are imported as modules so
their TestCase classes are not collected again. No provider mechanics or durable
success history is recreated. Golden canonical representation uses the existing
RFC8785 dependency. The 8,192/8,193-byte tests measure the complete actual result.

The absence law includes unrelated completion-shaped evidence to prove key
separation; it does not assert that the separate completion reader accepts an
unsupported/uncertain completion. The existing completion tests remain unchanged.
All eleven targets were independently reviewed and failed for the intended
missing module before application implementation, with the other 959 Core tests
passing. See [law, source and evidence record](../../../learning/1813/configuration-installation-receipts-1964.md).
Only the unchanged Docker-backed Core package script executes validation.
