Source: [control-plane-kit-operations/tests/test_activity_identity.py](../../../../control-plane-kit-operations/tests/test_activity_identity.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These five tests protect canonical activity identity at six Operations value
boundaries, public Core imports and selected ownership-inventory rows. They do
not call [activity_journal_events](../src/control_plane_kit_operations/activity_journal.py.md)
or exercise saga mapping/order semantics. The suite constructs values and reads
source/inventory files; it opens no database connection, contacts no Cloudflare
or secret provider and performs no runtime mutation. None of the tests was run
for this documentation.

The six factories construct ExternalReadinessAttestation, ActivityEventRecord,
CloudflareOwnedIngressResource, GeneratedIngressSecretReference, AuthorizeSecretUse
and AuthorizedSecretUse. They vary activity_id or source_activity_id while keeping
other fields fixed. Hostnames, tunnel/DNS IDs, public-ingress authority and secret
references are synthetic data. Constructing owned ingress or an authorized-use
record here does not create a tunnel, resolve a token, establish provider custody
or perform service authorization.

The positive matrix supplies a one-character ID and a 200-character ID to every
factory, checks value preservation and requires exact str storage. It does not
store an ActivityId object or normalize caller text. Actual
[Core ActivityId](../../../../control-plane-kit-core/src/control_plane_kit_core/planning/activity_plan.py)
uses the private canonical predicate: exact str, leading ASCII alphanumeric,
then ASCII alphanumeric/dot/underscore/colon/hyphen, maximum length 200. That
grammar is source context; these positive examples do not exhaust every allowed
punctuation or case combination.

The negative matrix passes object(), True, a str subclass, empty/space-only text,
newline-bearing text, a leading hyphen, slash, embedded space and length 201 to
all six factories. Each must raise its owner-specific InvalidOperationCommand,
OperationsRecordError, IngressAuthorityRegistrationError or
SecretProviderRegistrationError. The shared assertion requires no cause/context,
combined str/repr length at most 512 and omission of selected input canaries.
This covers the named invalid identities; it does not universally audit errors,
descriptors, arbitrary secret-like strings or every constructor field.

Actual helpers in [admission](../src/control_plane_kit_operations/admission.py.md),
[records](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py),
[ingress authorities](../src/control_plane_kit_operations/ingress_authorities.py.md)
and [secret providers](../src/control_plane_kit_operations/secret_providers.py.md)
delegate to the public ActivityId constructor, then raise a fixed local error
outside their ValueError handler. That placement accounts for the selected
cause/context-free translations. Optional None handling in record/secret fields
and other run/source linkage requirements are not varied by this matrix.

The import test parses the four consumer source files and requires a literal
from control_plane_kit_core.planning import ActivityId import in each. It scans
all Operations Python source and rejects literal imported-module strings exactly
equal to control_plane_kit_core._activity_identity. It also checks that public
planning.ActivityId is the same object exported from planning.activity_plan.
This preserves the intended public interface without moving the private grammar
into Operations or creating another implementation.

The AST check has a precise scope: it collects ast.Import names and
ast.ImportFrom.module strings. It does not prove all imports execute, detect
every dynamic/relative import spelling or prohibit every possible private Core
dependency. Requiring a public import alone would not prove a validator actually
uses it; the constructor matrices and inspected helper source provide the related
behavioral evidence for these four consumers. This is not a complete package
graph/ownership audit.

Two inventory tests read JSON from the required CPK_PACKAGE_MODULE_INVENTORY
environment path. One requires exactly one row for the private activity grammar,
owned by core with its canonical module/source destination and no public exports.
The other checks exactly one row each for private run grammar and public
operations.run_identity, with public exports [] and [RunId] respectively. Despite
exhaustively_inventoried in their names, these assertions cover those three
selected module rows, not every file in the repository. They do not independently
resolve the recorded sources, prove the supplied inventory is current or exercise
RunId's behavioral validation.

The Core/Operations boundary is intentional: Core owns the canonical identity
language and public value; Operations consumes it while returning errors native
to each operational interface. This suite checks that boundary and selected
error laws without adding an application/provider dependency. Full journal
conversion, plan membership, run consistency, ordinal ordering, persistence and
authorization still need their own tests and service contracts.

Read depth: full 289-line test and 77-line journal owner, full small private
activity grammar and selected public ActivityId/export, four actual consumer
helpers/record constructors and journal consumers. Relevant provider/ingress
owner context was retained. Selected neighboring journal assertions were read
separately, not credited to this suite. No executable validation, source changes,
credentials, database/provider/runtime or inventory mutation occurred for these
notes; documentation adds no security or mutation surface.
