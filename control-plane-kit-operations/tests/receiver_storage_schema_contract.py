"""Reviewed-target catalog delta for #1897; no installer or DDL implementation."""

INTRO = "cpk_graph_receiver_introductions"
BIND = "cpk_graph_receiver_bindings"
COLUMNS = {
    INTRO: (
        "workspace_id", "receiver_id", "runtime_id", "node_id", "provider_socket_name",
        "introducing_graph_id", "introducing_realized_projection_id", "introducing_action_id",
        "introducing_session_id", "introducing_draft_id", "first_accepted_action_id",
        "first_accepted_session_id", "retired_action_id", "retired_session_id",
    ),
    BIND: (
        "workspace_id", "graph_id", "realized_projection_id", "runtime_id", "node_id",
        "provider_socket_name", "receiver_id", "selected_configuration_digest", "declaration_identity",
    ),
}
NULLABLE = frozenset(("introducing_draft_id", "first_accepted_action_id",
                       "first_accepted_session_id", "retired_action_id", "retired_session_id"))

# relation, name, kind, local columns, foreign relation/columns, deferred
KEYS = (
    (INTRO, INTRO + "_pkey", "p", ("workspace_id", "receiver_id"), None, None, False),
    (INTRO, INTRO + "_receiver_key", "u", ("receiver_id",), None, None, False),
    (INTRO, INTRO + "_scope_key", "u", ("workspace_id", "receiver_id", "runtime_id", "node_id", "provider_socket_name"), None, None, False),
    (BIND, BIND + "_pkey", "p", ("workspace_id", "graph_id", "realized_projection_id", "node_id", "provider_socket_name"), None, None, False),
    (BIND, BIND + "_receiver_key", "u", ("workspace_id", "graph_id", "realized_projection_id", "receiver_id"), None, None, False),
    ("cpk_operation_actions", "cpk_operation_actions_action_session_key", "u", ("action_id", "session_id"), None, None, False),
    ("cpk_desired_topology_draft_revisions", "cpk_desired_topology_draft_revisions_draft_graph_key", "u", ("workspace_id", "draft_id", "graph_id"), None, None, False),
)
FOREIGN_KEYS = (
    (INTRO, INTRO + "_graph_fkey", "f", ("workspace_id", "introducing_graph_id"), "cpk_graph_versions", ("workspace_id", "graph_id"), False),
    (INTRO, INTRO + "_projection_workspace_fkey", "f", ("introducing_realized_projection_id", "workspace_id"), "cpk_realized_graph_projections", ("projection_id", "workspace_id"), False),
    (INTRO, INTRO + "_projection_source_fkey", "f", ("introducing_realized_projection_id", "introducing_graph_id"), "cpk_realized_graph_projections", ("projection_id", "source_authored_graph_id"), False),
    (INTRO, INTRO + "_origin_action_fkey", "f", ("introducing_action_id", "introducing_session_id"), "cpk_operation_actions", ("action_id", "session_id"), False),
    (INTRO, INTRO + "_origin_workspace_fkey", "f", ("introducing_session_id", "workspace_id"), "cpk_operation_sessions", ("session_id", "workspace_id"), False),
    (INTRO, INTRO + "_accepted_action_fkey", "f", ("first_accepted_action_id", "first_accepted_session_id"), "cpk_operation_actions", ("action_id", "session_id"), False),
    (INTRO, INTRO + "_accepted_workspace_fkey", "f", ("first_accepted_session_id", "workspace_id"), "cpk_operation_sessions", ("session_id", "workspace_id"), False),
    (INTRO, INTRO + "_retired_action_fkey", "f", ("retired_action_id", "retired_session_id"), "cpk_operation_actions", ("action_id", "session_id"), False),
    (INTRO, INTRO + "_retired_workspace_fkey", "f", ("retired_session_id", "workspace_id"), "cpk_operation_sessions", ("session_id", "workspace_id"), False),
    (INTRO, INTRO + "_draft_fkey", "f", ("workspace_id", "introducing_draft_id", "introducing_graph_id"), "cpk_desired_topology_draft_revisions", ("workspace_id", "draft_id", "graph_id"), False),
    (INTRO, INTRO + "_original_binding_fkey", "f", ("workspace_id", "introducing_graph_id", "introducing_realized_projection_id", "receiver_id"), BIND, ("workspace_id", "graph_id", "realized_projection_id", "receiver_id"), True),
    (BIND, BIND + "_graph_fkey", "f", ("workspace_id", "graph_id"), "cpk_graph_versions", ("workspace_id", "graph_id"), False),
    (BIND, BIND + "_projection_workspace_fkey", "f", ("realized_projection_id", "workspace_id"), "cpk_realized_graph_projections", ("projection_id", "workspace_id"), False),
    (BIND, BIND + "_projection_source_fkey", "f", ("realized_projection_id", "graph_id"), "cpk_realized_graph_projections", ("projection_id", "source_authored_graph_id"), False),
    (BIND, BIND + "_scope_fkey", "f", ("workspace_id", "receiver_id", "runtime_id", "node_id", "provider_socket_name"), INTRO, ("workspace_id", "receiver_id", "runtime_id", "node_id", "provider_socket_name"), False),
)
CHECKS = {
    INTRO: ("receiver_check", "acceptance_pair_check", "retirement_pair_check",
            "retirement_accepted_check", "retirement_action_check"),
    BIND: ("configuration_digest_check", "declaration_identity_check"),
}
