"""One bounded logical evidence read, with reservation before each SQL fetch."""
from __future__ import annotations

from datetime import datetime
from contextlib import contextmanager
from contextvars import ContextVar
import json

from control_plane_kit_operations.configuration_preparation import (
    ConfigurationCapacityDecision, ConfigurationEvidenceFootprint,
    configuration_evidence_capacity,
)


class _Capacity(ValueError):
    pass


class _Unavailable(ValueError):
    pass


_COMPOSED_READ = ContextVar("cpk_configuration_composed_read", default=None)


@contextmanager
def _joined_read(connection):
    """Join active owner accounting without discarding any prelude charges."""
    from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
    accounting = _ACCOUNTING.get()
    if accounting is None:
        with _composed_read(connection) as read:
            yield read
        return
    read = _active_read(connection)
    if read is None:
        raise _Unavailable
    existing = _COMPOSED_READ.get()
    if existing is not None:
        yield read
        return
    token = _COMPOSED_READ.set(read)
    try:
        yield read
    finally:
        _COMPOSED_READ.reset(token)


@contextmanager
def _composed_read(connection):
    """Bind one command-local ledger and cache across existing evidence owners."""
    from control_plane_kit_operations._configuration_preparation import _configuration_accounting
    existing = _COMPOSED_READ.get()
    if existing is not None:
        if _active_read(connection) is not existing:
            raise _Unavailable
        yield existing
        return
    with _configuration_accounting():
        read = _EvidenceRead(connection)
        token = _COMPOSED_READ.set(read)
        try:
            yield read
        finally:
            _COMPOSED_READ.reset(token)


def _active_read(connection):
    from control_plane_kit_operations._configuration_preparation import _ACCOUNTING, _execution_context
    current = _ACCOUNTING.get()
    if current is not None and current.active:
        if current.execution_context != _execution_context():
            raise _Unavailable
        composed = _COMPOSED_READ.get()
        if composed is not None:
            if composed.connection is not connection or composed.accounting is not current:
                raise _Unavailable
            return composed
        return _EvidenceRead(connection)
    return None


class _EvidenceRead:
    def __init__(self, connection, *, standalone=False):
        self.connection = connection
        from control_plane_kit_operations._configuration_preparation import _ACCOUNTING, _ConfigurationAccounting, _execution_context
        self.accounting = _ACCOUNTING.get()
        if self.accounting is not None and self.accounting.execution_context != _execution_context():
            self.accounting = None
        if self.accounting is None:
            if not standalone:
                raise _Unavailable
            self.accounting = _ConfigurationAccounting()
        self.sources = {}
        self.refs = {}

    @property
    def used(self):
        return self.accounting.used

    @used.setter
    def used(self, footprint):
        self.accounting.used = footprint

    def query(self, sql, params, *, records, octets, cells, identities=1):
        # Maxima are fixed by each owner's SQL projection/CASE/LIMIT. A nested
        # source read shares this same ledger; cache hits execute no statement.
        reserve = ConfigurationEvidenceFootprint(records * identities, octets, records * cells, 1)
        if configuration_evidence_capacity(self.used.plus(reserve)) is not ConfigurationCapacityDecision.WITHIN_LIMITS:
            raise _Capacity
        if records == 0 and (params or not sql.startswith(("SAVEPOINT ", "RELEASE SAVEPOINT ", "ROLLBACK TO SAVEPOINT "))):
            raise _Unavailable
        # Keep the full reservation outstanding across execution/fetch. Failed
        # or malformed results retain it: transported invalid evidence is not
        # free, and a nested read cannot spend the reserved allowance.
        self.used = self.used.plus(reserve)
        cursor = self.connection.execute(sql, params)
        rows = cursor.fetchall() if records else []
        if len(rows) > records or any(len(row) != cells for row in rows):
            raise _Unavailable
        # All projected cells are bytea, text, scalar or NULL. JSON is projected
        # as SQL text, so the charged representation is never RFC8785-reduced.
        size = 0
        for row in rows:
            for value in row:
                if value is None:
                    continue
                if type(value) is bytes:
                    size += len(value)
                elif type(value) is str:
                    size += len(value.encode("utf-8"))
                elif type(value) is bool:
                    size += 1
                elif type(value) is int:
                    size += len(str(value))
                else:
                    raise _Unavailable
        if size > octets:
            raise _Unavailable
        actual = ConfigurationEvidenceFootprint(len(rows) * identities, size, len(rows) * cells, 1)
        self.used = ConfigurationEvidenceFootprint(
            self.used.records - reserve.records + actual.records,
            self.used.value_octets - reserve.value_octets + actual.value_octets,
            self.used.scalar_markers - reserve.scalar_markers + actual.scalar_markers,
            self.used.statements - reserve.statements + actual.statements)
        return rows

    def bounded_rows(self, table, columns, where, params, *, maximum=1, order="", point=True, identities=1):
        """Owner-declared fixed columns, measured then guarded before transport.

        SQL identifiers/expressions belong to package code, never callers.
        Each entry is (column, native kind, byte cap). The caller continues to
        use its existing complete-row decoder after this transport boundary.
        """
        expressions = tuple(name if kind == "bytes" else "(" + name + ")::text" for name, kind, _ in columns)
        suffix = " FROM " + table + " WHERE " + where
        if order:
            suffix += " ORDER BY " + order
        nominal = maximum if point else maximum + 1
        limit = min(nominal, (4096 - self.used.records) // identities,
            max(0, (16 * 1024 * 1024 - self.used.accounted_bytes - 256) // (128 * identities + 28 * len(columns))))
        if limit <= 0:
            raise _Capacity
        suffix += " LIMIT %s"
        lengths = self.query("SELECT " + ",".join("octet_length(" + value + ")" for value in expressions)
            + suffix, (*params, limit), records=limit, octets=limit * len(columns) * 12, cells=len(columns), identities=identities)
        if limit < nominal and len(lengths) == limit:
            raise _Capacity
        if not point and len(lengths) > maximum:
            raise _Capacity
        if not lengths:
            return ()
        bounds = []
        for index, (_, _, cap) in enumerate(columns):
            values = [row[index] for row in lengths if row[index] is not None]
            if any(type(value) is not int or value < 0 for value in values):
                raise _Unavailable
            if any(value > cap for value in values):
                raise _Capacity
            bounds.append(max(values, default=0))
        valid = " AND ".join(f"({expression} IS NULL OR octet_length({expression})<={bound})"
            for expression, bound in zip(expressions, bounds))
        fetch_limit = maximum if point else len(lengths) + 1
        rows = self.query("SELECT " + ",".join(f"CASE WHEN {valid} THEN {expression} END"
            for expression in expressions) + f",({valid})" + suffix, (*params, fetch_limit),
            records=fetch_limit, octets=fetch_limit * (sum(bounds) + 1), cells=len(columns) + 1, identities=identities)
        if len(rows) != len(lengths) or any(row[-1] is not True for row in rows):
            raise _Unavailable
        decoded = []
        for row in rows:
            values = []
            for value, (_, kind, _) in zip(row, columns):
                if value is not None:
                    if kind == "json":
                        value = json.loads(value)
                    elif kind == "time":
                        value = datetime.fromisoformat(value)
                    elif kind == "int":
                        value = int(value)
                    elif kind == "bool":
                        if value not in ("true", "false"):
                            raise _Unavailable
                        value = value == "true"
                values.append(value)
            decoded.append(tuple(values))
        return tuple(decoded)
