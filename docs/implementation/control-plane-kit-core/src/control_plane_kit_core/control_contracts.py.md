Source: [control-plane-kit-core/src/control_plane_kit_core/control_contracts.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/control_contracts.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Validate supplied control values without owning runtime state

ControlVariableSpec declares a value kind, mutability, requiredness, reload
intent and metadata. ControlContract groups uniquely named declarations.
load reads only the supplied mapping: the canonical variable name wins, then
its metadata env alias, then None. Unknown supplied keys are ignored. Required
means not None; text values can still be empty. load_from_process deliberately
raises rather than reading the environment.

TEXT, SECRET and RUNTIME_VALUE accept bounded NUL-free text. SECRET is raw
supplied text in this contract, not the separate secret-reference language.
HTTP/POSTGRES validation checks text, an allowed [Protocol](types.py.md) scheme
and nonempty URL authority. It does not apply the stricter runtime-endpoint
rules for credentials, explicit ports, query or fragment. TCP requires
host:decimal-port with port 1–65535 and no scheme. These are shape checks, not
DNS, connection, credentials or egress authorization.

RUNTIME_MAP validates nonempty string keys and text values, returning sorted
entries. Individual text values are limited to 16384 UTF-8 bytes; this does not
bound map cardinality or total map size. Metadata has at most sixteen fields
and 512 bytes per value, but keys, variable names and descriptions are not
uniformly bounded or sanitized.

ControlContractSnapshot copies the outer values mapping into a mapping proxy.
Normal load validates first; direct snapshot construction does not revalidate
declared values or exact key membership, and nested maps are not deeply frozen.
get returns retained values. prepare_patch checks canonical names, mutability
and each supplied value, then returns a candidate dict without changing the
snapshot or executing the declared reload policy. It does not use env aliases
or implement transactional apply.

# Projection contracts differ by entry point

A variable descriptor normally omits the value. With include_value=True,
SECRET values and explicit redact_value requests become presence/redaction
markers; other values are validated and returned. Passing unsafe=False alone
does not cause that variable-level value redaction. The unsafe argument is not
an authorization check.

Snapshot.descriptor explicitly redacts every value. unsafe_descriptor reveals
non-secret values and adds an unsafe marker, while SECRET remains redacted.
This difference is protected by the [tests](../../tests/test_control_contracts.py.md).
Neither projection sanitizes all declaration metadata. Raw values also remain
in snapshot storage and ordinary dataclass representations, so safe-descriptor
claims must not be extended to repr or arbitrary serialization.

ControlContractDiagnostic has no constructor validation despite its bounded
description. Generated validation messages avoid the supplied value in ordinary
cases but retain variable names; unknown patch keys are stringified, and
wrapped URL parsing errors retain causes. This module is not a hostile-object
or universal traceback-redaction boundary.

No process read, mutable holder, route handler, reload, approval or provider
effect occurs. Interpreters and durable services must supply the authority,
state-transition and operational-history rules around these declarations.
