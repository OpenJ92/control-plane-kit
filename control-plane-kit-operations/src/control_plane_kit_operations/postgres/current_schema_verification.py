"""Bounded verification for the exact current operations schema."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Protocol

from .current_schema_contract import CURRENT_POSTGRES_SCHEMA_CONTRACT


class PostgresConnection(Protocol):
    def execute(self, query: str, params: object = ...) -> object: ...


_OBJECT_FREE_QUERY = """
WITH owned_namespace AS (
  SELECT oid FROM pg_namespace WHERE nspname = current_schema()
)
SELECT
  NOT EXISTS (
    SELECT 1 FROM pg_class
    WHERE relnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_proc
    WHERE pronamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_type
    WHERE typnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_collation
    WHERE collnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_conversion
    WHERE connamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_operator
    WHERE oprnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_opclass
    WHERE opcnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_opfamily
    WHERE opfnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_statistic_ext
    WHERE stxnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_ts_config
    WHERE cfgnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_ts_dict
    WHERE dictnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_ts_parser
    WHERE prsnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_ts_template
    WHERE tmplnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_extension
    WHERE extnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
"""

_EXPECTED_RELATIONS_QUERY = """
SELECT count(*) = %s
FROM pg_class AS relation
JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
WHERE namespace.nspname = current_schema()
  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND relation.relname = ANY(%s)
"""

_CURRENT_ADJUNCTS_QUERY = """
WITH owned_namespace AS (
  SELECT oid FROM pg_namespace WHERE nspname = current_schema()
)
SELECT
  NOT EXISTS (
    SELECT 1 FROM pg_class
    WHERE relnamespace = (SELECT oid FROM owned_namespace)
      AND relkind NOT IN ('r', 'p', 'v', 'm', 'f', 'i', 'I')
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_proc
    WHERE pronamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_type
    WHERE typnamespace = (SELECT oid FROM owned_namespace)
      AND typrelid = 0
      AND typelem = 0
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_collation
    WHERE collnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_conversion
    WHERE connamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_operator
    WHERE oprnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_opclass
    WHERE opcnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_opfamily
    WHERE opfnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_statistic_ext
    WHERE stxnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_ts_config
    WHERE cfgnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_ts_dict
    WHERE dictnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_ts_parser
    WHERE prsnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_ts_template
    WHERE tmplnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
  AND NOT EXISTS (
    SELECT 1 FROM pg_extension
    WHERE extnamespace = (SELECT oid FROM owned_namespace)
    LIMIT 1
  )
"""


def namespace_is_object_free(connection: PostgresConnection) -> bool:
    return _exact_boolean(connection.execute(_OBJECT_FREE_QUERY).fetchall())


def expected_relations_are_present(connection: PostgresConnection) -> bool:
    return _exact_boolean(
        connection.execute(
            _EXPECTED_RELATIONS_QUERY,
            (len(_CURRENT_SCHEMA_RELATION_NAMES), list(_CURRENT_SCHEMA_RELATION_NAMES)),
        ).fetchall()
    )


def current_namespace_adjuncts_are_exact(connection: PostgresConnection) -> bool:
    return _exact_boolean(connection.execute(_CURRENT_ADJUNCTS_QUERY).fetchall())


def _exact_boolean(rows: object) -> bool:
    if (
        not isinstance(rows, (list, tuple))
        or len(rows) != 1
        or not isinstance(rows[0], (list, tuple))
        or len(rows[0]) != 1
        or type(rows[0][0]) is not bool
    ):
        raise RuntimeError("catalog observation shape")
    return rows[0][0]


_CURRENT_SCHEMA_RELATIONS_JSON = json.dumps(
    tuple(asdict(item) for item in CURRENT_POSTGRES_SCHEMA_CONTRACT.relations),
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
)
_CURRENT_SCHEMA_COLUMNS_JSON = json.dumps(
    tuple(asdict(item) for item in CURRENT_POSTGRES_SCHEMA_CONTRACT.columns),
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
)
_CURRENT_SCHEMA_CONSTRAINTS_JSON = json.dumps(
    tuple(asdict(item) for item in CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints),
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
)
_CURRENT_SCHEMA_INDEXES_JSON = json.dumps(
    tuple(asdict(item) for item in CURRENT_POSTGRES_SCHEMA_CONTRACT.indexes),
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
)
_CURRENT_SCHEMA_RELATION_NAMES = tuple(item.name for item in CURRENT_POSTGRES_SCHEMA_CONTRACT.relations)


_CURRENT_SCHEMA_CONTRACT_QUERY = """
WITH
candidate_relations AS MATERIALIZED (
  SELECT relation.oid,
         relation.relname,
         relation.relkind::text AS kind,
         relation.relpersistence::text AS persistence,
         access_method.amname AS access_method,
         relation.relreplident::text AS replica_identity,
         relation.relispartition AS is_partition,
         relation.relrowsecurity AS row_security,
         relation.relforcerowsecurity AS force_row_security
  FROM pg_class AS relation
  JOIN pg_namespace AS namespace
    ON namespace.oid = relation.relnamespace
  LEFT JOIN pg_am AS access_method
    ON access_method.oid = relation.relam
  WHERE namespace.nspname = current_schema()
    AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
  ORDER BY relation.relname
  LIMIT %s
),
candidate_columns AS MATERIALIZED (
  SELECT relation.oid AS relation_oid,
         relation.relname,
         attribute.attnum,
         attribute.attname,
         type_namespace.nspname AS type_namespace,
         attribute.atttypid,
         attribute.atttypmod,
         attribute.attnotnull,
         attribute.attidentity::text AS identity,
         attribute.attgenerated::text AS generated,
         collation_namespace.nspname AS collation_namespace,
         owned_collation.collname AS collation_name,
         default_value.adbin,
         default_value.adrelid
  FROM candidate_relations AS relation
  JOIN pg_attribute AS attribute
    ON attribute.attrelid = relation.oid
  JOIN pg_type AS owned_type
    ON owned_type.oid = attribute.atttypid
  JOIN pg_namespace AS type_namespace
    ON type_namespace.oid = owned_type.typnamespace
  LEFT JOIN pg_collation AS owned_collation
    ON owned_collation.oid = attribute.attcollation
  LEFT JOIN pg_namespace AS collation_namespace
    ON collation_namespace.oid = owned_collation.collnamespace
  LEFT JOIN pg_attrdef AS default_value
    ON default_value.adrelid = attribute.attrelid
   AND default_value.adnum = attribute.attnum
  WHERE relation.kind = 'r'
    AND attribute.attnum > 0
    AND attribute.attisdropped IS FALSE
  ORDER BY relation.relname, attribute.attname
  LIMIT %s
),
candidate_constraints AS MATERIALIZED (
  SELECT relation.relname,
         owned_constraint.conname,
         owned_constraint.contype::text AS kind,
         owned_constraint.convalidated,
         owned_constraint.condeferrable,
         owned_constraint.condeferred,
         owned_constraint.connoinherit,
         owned_constraint.conrelid,
         owned_constraint.confrelid,
         owned_constraint.conkey,
         owned_constraint.confkey,
         owned_constraint.confupdtype::text AS update_action,
         owned_constraint.confdeltype::text AS delete_action,
         owned_constraint.confmatchtype::text AS match_type,
         owned_constraint.conbin,
         referenced_relation.relname AS referenced_relation,
         referenced_namespace.nspname AS referenced_namespace
  FROM pg_constraint AS owned_constraint
  JOIN candidate_relations AS relation
    ON relation.oid = owned_constraint.conrelid
   AND relation.kind = 'r'
  LEFT JOIN pg_class AS referenced_relation
    ON referenced_relation.oid = owned_constraint.confrelid
  LEFT JOIN pg_namespace AS referenced_namespace
    ON referenced_namespace.oid = referenced_relation.relnamespace
  ORDER BY relation.relname, owned_constraint.conname
  LIMIT %s
),
candidate_indexes AS MATERIALIZED (
  SELECT relation.relname,
         index_relation.relname AS index_name,
         owner.conname AS owning_constraint,
         access_method.amname AS access_method,
         index.indexrelid,
         index.indrelid,
         index.indisunique,
         index.indisprimary,
         index.indisvalid,
         index.indisready,
         index.indislive,
         index.indimmediate,
         index.indisclustered,
         index.indisreplident,
         index.indnullsnotdistinct,
         index.indnkeyatts,
         index.indnatts,
         index.indclass,
         cardinality(index.indclass::oid[]) AS indclass_cardinality,
         index.indcollation,
         cardinality(index.indcollation::oid[]) AS indcollation_cardinality,
         index.indoption,
         cardinality(index.indoption::smallint[]) AS indoption_cardinality,
         index.indpred,
         index.indexprs
  FROM pg_index AS index
  JOIN candidate_relations AS relation
    ON relation.oid = index.indrelid
   AND relation.kind = 'r'
  JOIN pg_class AS index_relation
    ON index_relation.oid = index.indexrelid
  JOIN pg_am AS access_method
    ON access_method.oid = index_relation.relam
  LEFT JOIN pg_constraint AS owner
    ON owner.conindid = index.indexrelid
   AND owner.contype IN ('p', 'u', 'x')
  ORDER BY relation.relname, index_relation.relname
  LIMIT %s
),
semantic_relations AS (
  SELECT relation.relname,
         jsonb_build_object(
           'name', relation.relname,
           'kind', relation.kind,
           'persistence', relation.persistence,
           'access_method', relation.access_method,
           'replica_identity', relation.replica_identity,
           'is_partition', relation.is_partition,
           'row_security', relation.row_security,
           'force_row_security', relation.force_row_security,
           'non_internal_triggers', CASE WHEN EXISTS (
             SELECT 1
             FROM pg_trigger AS trigger
             WHERE trigger.tgrelid = relation.oid
               AND trigger.tgisinternal IS FALSE
             LIMIT 1
           ) THEN 1 ELSE 0 END,
           'policies', CASE WHEN EXISTS (
             SELECT 1
             FROM pg_policy AS policy
             WHERE policy.polrelid = relation.oid
             LIMIT 1
           ) THEN 1 ELSE 0 END,
           'user_rules', CASE WHEN EXISTS (
             SELECT 1
             FROM pg_rewrite AS rule
             WHERE rule.ev_class = relation.oid
               AND rule.rulename <> '_RETURN'
             LIMIT 1
           ) THEN 1 ELSE 0 END
         ) AS value
  FROM candidate_relations AS relation
),
semantic_columns AS (
  SELECT column_value.relname,
         column_value.attname,
         jsonb_build_object(
           'relation', column_value.relname,
           'name', column_value.attname,
           'type_namespace', column_value.type_namespace,
           'formatted_type', format_type(
             column_value.atttypid,
             column_value.atttypmod
           ),
           'not_null', column_value.attnotnull,
           'identity', column_value.identity,
           'generated', column_value.generated,
           'collation_namespace', column_value.collation_namespace,
           'collation_name', column_value.collation_name,
           'default_expression', pg_get_expr(
             column_value.adbin,
             column_value.adrelid,
             false
           )
         ) AS value
  FROM candidate_columns AS column_value
),
semantic_constraints AS (
  SELECT owned_constraint.relname,
         owned_constraint.conname,
         jsonb_build_object(
           'relation', owned_constraint.relname,
           'name', owned_constraint.conname,
           'kind', owned_constraint.kind,
           'validated', owned_constraint.convalidated,
           'deferrable', owned_constraint.condeferrable,
           'deferred', owned_constraint.condeferred,
           'no_inherit', owned_constraint.connoinherit,
           'local_columns', CASE
             WHEN owned_constraint.conkey IS NULL THEN NULL
             ELSE ARRAY(
               SELECT CASE
                 WHEN key.attnum = 0 THEN NULL
                 ELSE attribute.attname
               END
               FROM unnest(owned_constraint.conkey)
                    WITH ORDINALITY AS key(attnum, position)
               LEFT JOIN pg_attribute AS attribute
                 ON attribute.attrelid = owned_constraint.conrelid
                AND attribute.attnum = key.attnum
                AND attribute.attisdropped IS FALSE
               ORDER BY key.position
             )
           END,
           'referenced_relation', CASE
             WHEN owned_constraint.referenced_namespace = current_schema()
               THEN owned_constraint.referenced_relation
             ELSE NULL
           END,
           'referenced_columns', CASE
             WHEN owned_constraint.confkey IS NULL THEN NULL
             ELSE ARRAY(
               SELECT CASE
                 WHEN key.attnum = 0 THEN NULL
                 ELSE attribute.attname
               END
               FROM unnest(owned_constraint.confkey)
                    WITH ORDINALITY AS key(attnum, position)
               LEFT JOIN pg_attribute AS attribute
                 ON attribute.attrelid = owned_constraint.confrelid
                AND attribute.attnum = key.attnum
                AND attribute.attisdropped IS FALSE
               ORDER BY key.position
             )
           END,
           'update_action', NULLIF(owned_constraint.update_action, ' '),
           'delete_action', NULLIF(owned_constraint.delete_action, ' '),
           'match_type', NULLIF(owned_constraint.match_type, ' '),
           'check_expression', CASE
             WHEN owned_constraint.kind = 'c'
               THEN pg_get_expr(
                 owned_constraint.conbin,
                 owned_constraint.conrelid,
                 false
               )
             ELSE NULL
           END
         ) AS value,
         cardinality(owned_constraint.conkey) AS local_cardinality,
         cardinality(owned_constraint.confkey) AS referenced_cardinality
  FROM candidate_constraints AS owned_constraint
),
semantic_indexes AS (
  SELECT owned_index.relname,
         owned_index.index_name,
         jsonb_build_object(
           'relation', owned_index.relname,
           'name', owned_index.index_name,
           'owning_constraint', owned_index.owning_constraint,
           'access_method', owned_index.access_method,
           'unique', owned_index.indisunique,
           'primary', owned_index.indisprimary,
           'valid', owned_index.indisvalid,
           'ready', owned_index.indisready,
           'live', owned_index.indislive,
           'immediate', owned_index.indimmediate,
           'clustered', owned_index.indisclustered,
           'replica_identity', owned_index.indisreplident,
           'nulls_not_distinct', owned_index.indnullsnotdistinct,
           'key_entries', ARRAY(
             SELECT pg_get_indexdef(owned_index.indexrelid, position, false)
             FROM generate_series(1, owned_index.indnkeyatts) AS position
             ORDER BY position
           ),
           'include_entries', ARRAY(
             SELECT pg_get_indexdef(owned_index.indexrelid, position, false)
             FROM generate_series(
               owned_index.indnkeyatts + 1,
               owned_index.indnatts
             ) AS position
             ORDER BY position
           ),
           'opclasses', ARRAY(
             SELECT opclass_namespace.nspname || '.' || opclass.opcname
             FROM unnest(owned_index.indclass::oid[])
                  WITH ORDINALITY AS item(opclass_oid, position)
             JOIN pg_opclass AS opclass
               ON opclass.oid = item.opclass_oid
             JOIN pg_namespace AS opclass_namespace
               ON opclass_namespace.oid = opclass.opcnamespace
             ORDER BY item.position
           ),
           'collations', ARRAY(
             SELECT CASE
               WHEN item.collation_oid = 0 THEN NULL
               ELSE collation_namespace.nspname || '.' || owned_collation.collname
             END
             FROM unnest(owned_index.indcollation::oid[])
                  WITH ORDINALITY AS item(collation_oid, position)
             LEFT JOIN pg_collation AS owned_collation
               ON owned_collation.oid = item.collation_oid
             LEFT JOIN pg_namespace AS collation_namespace
               ON collation_namespace.oid = owned_collation.collnamespace
             ORDER BY item.position
           ),
           'options', owned_index.indoption::smallint[],
           'predicate', pg_get_expr(
             owned_index.indpred,
             owned_index.indrelid,
             false
           ),
           'expressions', pg_get_expr(
             owned_index.indexprs,
             owned_index.indrelid,
             false
           )
         ) AS value,
         owned_index.indclass_cardinality AS opclass_cardinality,
         owned_index.indcollation_cardinality AS collation_cardinality,
         owned_index.indoption_cardinality AS option_cardinality,
         owned_index.indnkeyatts
  FROM candidate_indexes AS owned_index
)
SELECT
  COALESCE(
    (SELECT count(*) = %s AND jsonb_agg(value ORDER BY relname) = %s::jsonb
     FROM semantic_relations),
    FALSE
  ),
  COALESCE(
    (SELECT count(*) = %s
            AND jsonb_agg(value ORDER BY relname, attname) = %s::jsonb
     FROM semantic_columns),
    FALSE
  ),
  COALESCE(
    (SELECT count(*) = %s
            AND bool_and(
              (local_cardinality IS NULL OR local_cardinality =
                jsonb_array_length(value -> 'local_columns'))
              AND (referenced_cardinality IS NULL OR referenced_cardinality =
                jsonb_array_length(value -> 'referenced_columns'))
            )
            AND jsonb_agg(value ORDER BY relname, conname) = %s::jsonb
     FROM semantic_constraints),
    FALSE
  ),
  COALESCE(
    (SELECT count(*) = %s
            AND bool_and(
              opclass_cardinality = indnkeyatts
              AND collation_cardinality = indnkeyatts
              AND option_cardinality = indnkeyatts
            )
            AND jsonb_agg(value ORDER BY relname, index_name) = %s::jsonb
     FROM semantic_indexes),
    FALSE
  )
"""



def current_schema_contract_is_exact(connection: PostgresConnection) -> bool:
    rows = connection.execute(
        _CURRENT_SCHEMA_CONTRACT_QUERY,
        (
            len(CURRENT_POSTGRES_SCHEMA_CONTRACT.relations) + 1,
            len(CURRENT_POSTGRES_SCHEMA_CONTRACT.columns) + 1,
            len(CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints) + 1,
            len(CURRENT_POSTGRES_SCHEMA_CONTRACT.indexes) + 1,
            len(CURRENT_POSTGRES_SCHEMA_CONTRACT.relations),
            _CURRENT_SCHEMA_RELATIONS_JSON,
            len(CURRENT_POSTGRES_SCHEMA_CONTRACT.columns),
            _CURRENT_SCHEMA_COLUMNS_JSON,
            len(CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints),
            _CURRENT_SCHEMA_CONSTRAINTS_JSON,
            len(CURRENT_POSTGRES_SCHEMA_CONTRACT.indexes),
            _CURRENT_SCHEMA_INDEXES_JSON,
        ),
    ).fetchall()
    return (
        isinstance(rows, (list, tuple))
        and len(rows) == 1
        and isinstance(rows[0], (list, tuple))
        and len(rows[0]) == 4
        and all(type(value) is bool and value for value in rows[0])
    )
