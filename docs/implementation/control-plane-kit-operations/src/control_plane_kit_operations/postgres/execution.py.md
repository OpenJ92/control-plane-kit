Source: [execution.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py).
Maintain this companion alongside its source.


B1 configuration commands pass their active reader to both original receiver
scope verification and prior receiver acceptance evidence. Prior accepted
history retains its existing plan/run/event/action validation while sharing the
current command's cumulative budget. It cannot begin another independent
transport allowance inside a configuration start. Recorded accepted-history
fixtures prove read semantics only, not new deployment or advancement.

Configuration command-receipt completion reserves its entire bounded returned
receipt before executing the UPDATE. A data-modifying CTE projects guarded SQL
text (13 fields, each at most 65,536 bytes, plus a validity marker), then the
existing typed receipt decoder validates reconstructed fields. This prevents a
small mutation acknowledgement from spending the remaining budget before a
second unreserved full receipt read. A zero-row CAS still costs one statement;
no store commits independently, and outcome retention limits are unchanged.

Event insertion joins an active configuration ledger and reserves a scalar
RETURNING value before SQL. Existing bounded event/run readers and ordinal
allocation share it. This accounting change does not authorize standalone
advancement events; their B2 prepared writer boundary is a subsequent change.
