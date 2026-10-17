Source: [node_control_surface_reads.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_reads.py).
Maintain this companion with source and imported contract changes.

This owner defines static declaration identity and exact capabilities/status
read authority. Declaration v1 is variable-only; v2 requires nonempty health_reads
on the same surface exterior and may also contain variables. The constructor
requires explicit v2 selection for health. Both inconsistent profile/content
pairs fail; unchanged profile length preserves the 16,453-byte declaration limit.

Existing static requests and grants retain their profiles, fields and canonical
wire. Their declaration digest can bind either admitted declaration profile.
No dynamic health request or signing effect is introduced. Pure claim validation
is not cryptographic verification or current-attempt authorization. Existing
authority vectors and the new strict declaration matrix tests protect the boundary.
