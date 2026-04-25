"""Tests for the storage backend Protocol + Postgres adapter shape."""

from __future__ import annotations

from biomodel_monitor.store import Storage
from biomodel_monitor.store.repository import MetricsStore


def test_metrics_store_satisfies_storage_protocol(tmp_path):
    store = MetricsStore(str(tmp_path / "m.db"))
    try:
        # runtime_checkable protocol — duck-typed isinstance
        assert isinstance(store, Storage)
    finally:
        store.close()


def test_postgres_store_runs_schema_against_fake_connection():
    """Prove the Postgres adapter issues the expected SQL on construction."""
    from biomodel_monitor.store.postgres import PG_SCHEMA, PostgresStore

    executed: list[str] = []

    class FakeCursor:
        def execute(self, sql, params=None):
            executed.append(sql)

        def fetchone(self):
            return None

        def fetchall(self):
            return []

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    store = PostgresStore("dummy", connect=lambda _dsn: FakeConn())
    # all CREATE TABLE statements should have been executed
    create_count = sum(1 for s in executed if "CREATE TABLE" in s.upper())
    expected = sum(1 for s in PG_SCHEMA.split(";") if "CREATE TABLE" in s.upper())
    assert create_count == expected
    store.close()


def test_postgres_adapter_translates_question_marks_to_pg_style():
    """The SQL placeholder shim should convert '?' to '%s'."""
    from biomodel_monitor.store.postgres import _Cursor

    captured = {}

    class FakeRaw:
        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params

    cur = _Cursor(raw=FakeRaw())
    cur.execute("SELECT * FROM foo WHERE x=? AND y=?", (1, 2))
    assert captured["sql"] == "SELECT * FROM foo WHERE x=%s AND y=%s"
    assert captured["params"] == (1, 2)
