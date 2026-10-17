Source: [tests.yml](../../../../.github/workflows/tests.yml).

The Operations job receives a 75-minute budget after the O2 run at `09c9a00d`
exhausted its former 60-minute limit while still progressing. That run completed
2,061 tests with explicit passing results, including all receiver-health targets,
all formerly blocked tests and corrected contract assertions. Another 102 expected
methods had no completed result, and compilation/import had not been reached.

North explicitly approved the additional 15 minutes as discretionary completion
margin. It is not a predicted runtime, a guarantee of completion or acceptance of
partial execution. The full ordinary suite and compilation/import must pass.

Core remains at 20 minutes. Runner, architecture-testing pin, workflow permissions,
suite command, test selection, assertions, package script and cleanup are unchanged.
This changes maximum CI duration only; it introduces no application permission,
credential, provider or durable-data effect. Any further timeout or failure requires
its own evidence and disposition, not an automatic retry or budget increase.
