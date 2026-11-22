"""Runs a guarded SELECT on a source and returns JSON-safe rows.

Time limits on every engine:
  postgresql -> statement_timeout (session option, see registry)
  mysql      -> max_execution_time (session, see registry)
  sqlite     -> progress handler aborts the query after the deadline
  duckdb     -> a timer calls connection.interrupt()
Rows are fetched with fetchmany(limit + 1), so even a missing LIMIT cannot
pull a huge result into memory.
"""

from __future__ import annotations

import math
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal

from app.config import get_settings
from app.sources.registry import get_registry


class QueryError(RuntimeError):
    """Database error, with the engine's own message (fed back to the LLM for repair)."""


def _json_value(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() and abs(v) < 2**53 else float(v)
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, (date, dtime)):
        return v.isoformat()
    if isinstance(v, timedelta):
        return v.total_seconds()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "<binary>"
    return str(v)


def run_query(source_id: str, sql: str, row_limit: int | None = None,
              timeout_s: int | None = None) -> dict:
    s = get_settings()
    row_limit = row_limit or s.query_row_limit
    timeout_s = timeout_s or s.query_timeout_seconds
    reg = get_registry()
    source = reg.get(source_id)
    eng = reg.engine(source_id)

    t0 = time.perf_counter()
    timer = None
    with eng.connect() as conn:
        raw = conn.connection.dbapi_connection  # plain DB-API: no SQLAlchemy bind-param parsing
        deadline = time.monotonic() + timeout_s
        if source.engine == "sqlite":
            raw.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        elif source.engine == "duckdb":
            target = getattr(raw, "_ConnectionWrapper__c", None) or raw
            timer = threading.Timer(timeout_s, lambda: target.interrupt())
            timer.start()
        cur = raw.cursor()
        try:
            # per-query time limit (our own statement, allowed in a read-only session)
            if source.engine == "postgresql":
                cur.execute(f"SET statement_timeout = {int(timeout_s * 1000)}")
            elif source.engine == "mysql":
                cur.execute(f"SET SESSION max_execution_time = {int(timeout_s * 1000)}")
            cur.execute(sql)
            columns = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(row_limit + 1)
        except Exception as exc:
            msg = str(exc).strip()
            if time.monotonic() > deadline or "interrupt" in msg.lower() or "timeout" in msg.lower() \
                    or "canceling statement" in msg.lower() or "max_execution_time" in msg.lower():
                raise QueryError(f"The query took longer than {timeout_s} seconds and was stopped.") from exc
            raise QueryError(msg.splitlines()[0][:500] if msg else type(exc).__name__) from exc
        finally:
            if timer:
                timer.cancel()
            if source.engine == "sqlite":
                raw.set_progress_handler(None, 0)
            try:
                cur.close()
            except Exception:
                pass
            try:
                raw.rollback()  # nothing to keep; ends the read-only transaction
            except Exception:
                pass

    truncated = len(rows) > row_limit
    rows = rows[:row_limit]
    return {
        "columns": columns,
        "rows": [[_json_value(v) for v in r] for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "ms": round((time.perf_counter() - t0) * 1000, 1),
    }
