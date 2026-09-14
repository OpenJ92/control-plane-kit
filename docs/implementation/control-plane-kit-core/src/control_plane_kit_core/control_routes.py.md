Source: [control-plane-kit-core/src/control_plane_kit_core/control_routes.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/control_routes.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Route surfaces as protocol data

ControlRoute carries a name, GET/POST method, path, scope and description.
ControlRouteSet groups these values under a closed family name. The twelve
maintained families cover status, logs, downstream targets, observers, metrics,
circuit state, traffic evidence, faults, cache, load runs, discovery and
workload node control. No handler, HTTP server or external effect is installed
by importing them.

Most paths use /__deploy. Workload node-control discovery, variable reads and
semantic commands use /__control, with distinct surface-read, variable-read
and apply scopes. Other mutation families name their intended powers explicitly,
such as signaling, fault injection, cache purge, load generation and discovery
writes. These scope labels are requirements for an interpreter to enforce;
route data itself performs no authentication or authorization.

control_path strips trailing slashes from the prefix and supplies a leading
slash for the path. It is string composition, not URL normalization, traversal
rejection, route-template expansion or network-exposure policy. The route
dataclasses have no constructor validation of field types, duplicate identities,
path shape or immutable collections. Canonical constants supply the maintained
values. Names can recur for GET/POST on the same path, so name alone is not a
unique route identity.

as_descriptor exposes fields in declaration order without scrubbing text.
route_set_named accepts a name enum or convertible string, returns the
canonical constant and reports unknown names with the supplied value and known
names; conversion errors retain their cause. Neither interface is a universal
bounded or redacted hostile-input boundary.

[Focused tests](../../tests/test_control_routes.py.md) check selected exact
method/path/scope mappings and the complete family inventory. Descriptions
such as bounded logs, test-only faults or authenticated commands express
protocol intent; actual handlers, data limits and access checks need their
own implementation evidence. Declaring a family does not advertise it on
every block or prove that every runtime implements it.
