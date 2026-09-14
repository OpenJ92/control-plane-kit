Source: [activity_plan.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/activity_plan.py).
Maintain this companion with source and imported contract changes.

The closed operation union additionally admits exact ObserveManagementBootstrap
and ObserveNodeHealth values from the lower pure request module. Both require
NoCompensationRequired. ActivityPlan retains canonical ordering, structural
dependency/cycle checks, risks and destructive markers. Existing WaitForHealthy
and all mutation/compensation meanings are unchanged. Request validity does not
authorize execution or establish successful/fresh observations.
