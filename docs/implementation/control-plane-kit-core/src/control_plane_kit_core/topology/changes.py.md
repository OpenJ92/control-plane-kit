Source: [changes.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/changes.py).
Maintain this companion with source and imported contract changes.

RuntimeManagementValue represents an optional selection in the closed structural
diff value algebra. RUNTIME_MANAGEMENT identifies its runtime-owned field.
Addition/removal/replacement are ModifiedChange values with explicit typed before
and after selections, including absence. Whole-runtime descriptors preserve
present management material and omit absence. This stores references only; it
neither resolves secrets nor establishes runtime authority.
