Source: [extraction_parity/runner.py](../../../extraction_parity/runner.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

capture_command interprets an argv sequence into a raw observation; comparison
semantics remain in the [differential language](differential.py.md). It creates a
temporary behavior-file location, supplies CPK_PARITY_BEHAVIOR_PATH and declared
secrets through the environment, and launches the command directly with piped
stdout/stderr. It inherits the ambient environment, working directory and process
authority. This function supplies neither a container nor a sandbox or approval
boundary; any isolation must come from its caller. Secret entries are applied
last, without a reserved-name guard for the behavior-path variable.

The selector loop retains a combined stdout/stderr byte budget, at most 1 MiB.
Exceeding that budget or the requested deadline kills the direct child and marks
output-limit or timeout, with no exit code. The loop continues draining pipes and
ends with process.wait() without a timeout. Descendants retaining a pipe, or a
child closing its pipes before exiting, can therefore outlive the requested
wall-time bound. There is no process-group cleanup or encompassing exceptional-
path cleanup guarantee. The timeout comparison also does not explicitly reject
non-finite numeric inputs. Launch OSError becomes infrastructure-failure.

Only completed processes, including nonzero exits, trigger behavior and artifact
reads. Each read requests at most 1 MiB plus one overflow-detection byte. Read,
behavior-JSON and raw-secret rejection errors caught in this block convert the
observation to infrastructure-failure and discard its behavior and artifacts.
Caller-selected artifact paths
are not constrained to the temporary directory, checked for regular-file type,
or proven to have been produced by this execution. These reads occur after the
process loop and have no separate deadline. ArtifactInput itself is only a frozen
record; artifact metadata, identity and behavior receive their closed-language
validation when decode_observation runs after the command's effects.

Declared secrets require environment-safe names and at least eight UTF-8 bytes.
Exact declared byte sequences are rejected from argv before execution and from
raw behavior/artifact bytes after reading. Captured logs replace exact matches,
longest first, with named markers. If replacement expands a stream beyond its
bound, that stream becomes an output-limit redaction marker or empty bytes.
Raw retention is truncated before redaction: this is not a scrubber for partial,
encoded, transformed or undeclared secrets. Ambient credentials and observation
metadata do not receive uniform declared-secret screening. The closed observation
codec validates structure, not whole-document confidentiality or producer trust.

The observation records caller-declared identity/source_digest, argv, process
outcome, hashed log payloads, behavior and artifacts sorted by name. It does not
hash source files to establish the claimed source identity. TemporaryDirectory
removes the capture directory on exit; arbitrary command effects and caller
artifact files have no rollback or cleanup here. Structured outcomes preserve
capture failure information, but uncaught exceptions can still prevent a new
observation from being returned or written.

evidence_record first revalidates the comparison result, hashes its canonical
JSON and emits passing only for equivalent results. The
[evidence-index decoder](validation.py.md) checks the record's shape. This is
deterministic document consistency, not authenticated execution or a fresh rerun.

The capture CLI writes any successfully constructed observation and returns zero,
including timeout/output-limit/infrastructure-failure observations. Its exit
status alone is not process-success evidence. The compare CLI reads bounded JSON,
writes the result and then the evidence index, and returns zero only for
equivalence. Imported JSON input limits are checked after full file reads.
Each output uses a fixed sibling .tmp file followed by os.replace; the two writes
are not a transaction, and there is no fsync, writer isolation or temporary-file
cleanup guarantee. A later error can leave a new result beside older evidence.

The [focused runner tests](tests/test_differential_runner.py.md) exercise small
Python subprocesses and selected secret/output/evidence cases. This documentation
review read the full owner and relevant imported contracts; it ran no command
capture, test suite or live validation.
