"""Automatic database profiler.

Works on any SQLAlchemy-supported engine with no database-specific code:
  1. reflect tables / views / columns / primary keys / foreign keys
  2. per table: row count, a sample of rows, MIN/MAX of numeric + date columns
  3. per text column: exact distinct values when there are not too many
     (city names, categories, product names...) -> used by word correction
  4. infer join relationships that are not declared as foreign keys
     (column-name match against a key, confirmed by value overlap)
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import inspect, text, types as sat
from sqlalchemy.engine import Engine

from app.profiler.models import ColumnProfile, Relationship, SourceProfile, TableProfile
from app.sources.registry import SYSTEM_SCHEMAS, Source

log = logging.getLogger(__name__)

SAMPLE_ROWS = 5000
MAX_CATEGORICAL_VALUES = 5000  # keep full value lists up to this many distinct values
MAX_VALUE_LEN = 80  # longer texts are free text (reviews, comments), not names
TOP_SAMPLES = 5

_HEX_ID = re.compile(r"^[0-9a-fA-F]{16,}$|^[0-9a-fA-F-]{36}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?(\.\d+)?)?")


# ------------------------------------------------------------------ helpers

# Turns any database value into short text for the profile.
def _to_str(v) -> str:
    if isinstance(v, (datetime, date)):
        return v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v == v.to_integral() else str(v)
    if isinstance(v, (bytes, memoryview)):
        return "<binary>"
    s = str(v)
    return s if len(s) <= 200 else s[:200] + "…"


# Groups a raw column type into number / text / date / bool.
def _base_kind(col_type) -> str:
    try:
        if isinstance(col_type, sat.Boolean):
            return "bool"
        if isinstance(col_type, (sat.Date, sat.DateTime, sat.Time)):
            return "date"
        if isinstance(col_type, (sat.Integer, sat.Numeric, sat.Float)):
            return "number"
        if isinstance(col_type, (sat.String, sat.Text, sat.Enum)):
            return "text"
        name = str(col_type).upper()
    except Exception:
        name = ""
    if any(k in name for k in ("INT", "NUM", "DEC", "REAL", "FLOAT", "DOUBLE", "MONEY")):
        return "number"
    if any(k in name for k in ("DATE", "TIME")):
        return "date"
    if any(k in name for k in ("CHAR", "TEXT", "CLOB", "STRING")):
        return "text"
    return "other"


# Guesses whether a column is an ID (by name and values).
def _looks_like_id(name: str, is_pk: bool, values: list) -> bool:
    n = name.lower()
    if is_pk or n == "id" or n.endswith("_id") or n.endswith("id") and n[:-2].isalpha() and name[-2:] in ("Id", "ID"):
        return True
    strs = [str(v) for v in values[:200] if v is not None]
    return bool(strs) and sum(bool(_HEX_ID.match(s)) for s in strs) / len(strs) > 0.9


# Splits names like CustomerId or order_status into words.
def _split_words(identifier: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", identifier)
    return re.sub(r"[_\-.\s]+", " ", s).strip().lower()


# Quotes table and column names correctly for each database engine.
class _Q:
    """Identifier quoting with the engine's own rules (handles spaces, reserved words)."""

    def __init__(self, eng: Engine):
        self.p = eng.dialect.identifier_preparer

    # Quotes a column name.
    def col(self, name: str) -> str:
        return self.p.quote(name)

    # Quotes a table name (with schema if needed).
    def table(self, schema: str | None, name: str) -> str:
        return (self.p.quote_schema(schema) + "." if schema else "") + self.p.quote(name)


# ------------------------------------------------------------------ table profiling

# Profiles one table: row count, and per column type, blanks, distinct values, min/max, samples, value lists.
def _profile_table(conn, q: _Q, schema: str | None, name: str, display: str, cols: list[dict],
                   pk: set[str], is_view: bool) -> TableProfile:
    tref = q.table(schema, name)

    row_count = None
    try:
        row_count = int(conn.execute(text(f"SELECT COUNT(*) FROM {tref}")).scalar())
    except Exception as exc:
        log.warning("count failed on %s: %s", display, exc)
        conn.rollback()

    sample: list[tuple] = []
    try:
        sample = conn.execute(text(f"SELECT * FROM {tref} LIMIT {SAMPLE_ROWS}")).fetchall()
    except Exception as exc:
        log.warning("sample failed on %s: %s", display, exc)
        conn.rollback()

    profiles: list[ColumnProfile] = []
    for i, c in enumerate(cols):
        values = [r[i] for r in sample] if sample and i < len(sample[0]) else []
        non_null = [v for v in values if v is not None]
        kind = _base_kind(c["type"])
        if kind == "text" and non_null:
            strs = [str(v) for v in non_null[:200]]
            if sum(bool(_ISO_DATE.match(s)) for s in strs) / len(strs) > 0.9:
                kind = "date"  # dates stored as text (common in SQLite / CSV)
        if kind in ("text", "number") and _looks_like_id(c["name"], c["name"] in pk, non_null):
            kind = "id"

        counts = Counter(_to_str(v) for v in non_null)
        profiles.append(ColumnProfile(
            name=c["name"],
            type=str(c["type"]),
            kind=kind,
            nullable=bool(c.get("nullable", True)),
            primary_key=c["name"] in pk,
            null_fraction=round(1 - len(non_null) / len(values), 3) if values else None,
            distinct_count=len(counts) if values else None,
            samples=[v for v, _ in counts.most_common(TOP_SAMPLES)],
        ))

    # exact MIN / MAX over the whole table for numbers and dates (one query)
    ranged = [p for p in profiles if p.kind in ("number", "date")]
    if ranged:
        parts = ", ".join(f"MIN({q.col(p.name)}), MAX({q.col(p.name)})" for p in ranged)
        try:
            row = conn.execute(text(f"SELECT {parts} FROM {tref}")).fetchone()
            for j, p in enumerate(ranged):
                lo, hi = row[2 * j], row[2 * j + 1]
                p.min = None if lo is None else _to_str(lo)
                p.max = None if hi is None else _to_str(hi)
        except Exception as exc:
            log.warning("min/max failed on %s: %s", display, exc)
            conn.rollback()

    # full distinct value lists for name-like / category-like text columns
    for p in profiles:
        if p.kind != "text" or not p.samples:
            continue
        avg_len = sum(len(s) for s in p.samples) / len(p.samples)
        if avg_len > MAX_VALUE_LEN:
            continue  # free text (reviews, descriptions)
        col = q.col(p.name)
        try:
            rows = conn.execute(text(
                f"SELECT {col}, COUNT(*) AS n FROM {tref} WHERE {col} IS NOT NULL "
                f"GROUP BY {col} ORDER BY n DESC LIMIT {MAX_CATEGORICAL_VALUES + 1}"
            )).fetchall()
        except Exception as exc:
            log.warning("distinct failed on %s.%s: %s", display, p.name, exc)
            conn.rollback()
            continue
        if len(rows) <= MAX_CATEGORICAL_VALUES:
            vals = [_to_str(r[0]) for r in rows]
            if any(len(v) > 2 * MAX_VALUE_LEN for v in vals[:50]):
                continue
            p.categorical = True
            p.distinct_count = len(vals)
            p.values = vals
            p.samples = vals[:TOP_SAMPLES]

    return TableProfile(name=display, row_count=row_count, is_view=is_view, columns=profiles)


# ------------------------------------------------------------------ relationships

# Lowercases a name and removes underscores for comparing.
def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


# Makes a table name singular (orders -> order) for matching keys.
def _singular(word: str) -> str:
    w = _norm(word)
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith("ses") or w.endswith("xes"):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


# Finds joins that are not declared: matching column names confirmed by value overlap.
def _infer_relationships(conn, q: _Q, tables: list[TableProfile], schemas: dict[str, str | None],
                         raw_names: dict[str, str], declared: list[Relationship]) -> list[Relationship]:
    """Find likely joins: column A.x matches a key column B.y by name, confirmed by value overlap."""
    seen = {(r.from_table, r.from_column, r.to_table, r.to_column) for r in declared}
    seen |= {(r.to_table, r.to_column, r.from_table, r.from_column) for r in declared}
    out: list[Relationship] = []

    # candidate targets: single-column primary keys, or id-like columns with unique sample values
    targets: list[tuple[TableProfile, ColumnProfile]] = []
    for t in tables:
        pks = [c for c in t.columns if c.primary_key]
        if len(pks) == 1:
            targets.append((t, pks[0]))
        elif not pks:
            for c in t.columns:
                if c.kind == "id" and t.row_count and c.distinct_count == min(t.row_count, SAMPLE_ROWS) \
                        and c.null_fraction == 0:
                    targets.append((t, c))
                    break

    for bt, bc in targets:
        b_key = _norm(bc.name)
        b_alias = {b_key, _singular(bt.name.split(".")[-1]) + b_key, _singular(bt.name.split(".")[-1]) + "id"}
        if b_key == "id":
            b_alias.add(_singular(bt.name.split(".")[-1]) + "id")
        for at in tables:
            if at.name == bt.name or at.is_view or bt.is_view:
                continue
            for ac in at.columns:
                if ac.kind not in ("id", "number", "text") or _norm(ac.name) not in b_alias:
                    continue
                if ac.primary_key and len([c for c in at.columns if c.primary_key]) == 1:
                    continue  # A's own single key (e.g. two tables both keyed "id") is not a join
                key = (at.name, ac.name, bt.name, bc.name)
                if key in seen:
                    continue
                overlap = _value_overlap(conn, q, schemas, raw_names, at, ac, bt, bc)
                if overlap is not None and overlap >= 0.8:
                    out.append(Relationship(from_table=at.name, from_column=ac.name, to_table=bt.name,
                                            to_column=bc.name, source="inferred",
                                            confidence=round(overlap, 2)))
                    seen.add(key)
                    seen.add((bt.name, bc.name, at.name, ac.name))
    return out


# Measures how many values of one column exist in another (used to confirm a join).
def _value_overlap(conn, q: _Q, schemas, raw_names, at, ac, bt, bc) -> float | None:
    a_ref = q.table(schemas[at.name], raw_names[at.name])
    b_ref = q.table(schemas[bt.name], raw_names[bt.name])
    try:
        vals = [r[0] for r in conn.execute(text(
            f"SELECT DISTINCT {q.col(ac.name)} FROM {a_ref} WHERE {q.col(ac.name)} IS NOT NULL LIMIT 200"
        )).fetchall()]
        if not vals:
            return None
        params = {f"v{i}": v for i, v in enumerate(vals)}
        placeholders = ", ".join(f":v{i}" for i in range(len(vals)))
        hit = conn.execute(text(
            f"SELECT COUNT(DISTINCT {q.col(bc.name)}) FROM {b_ref} WHERE {q.col(bc.name)} IN ({placeholders})"
        ), params).scalar()
        return (hit or 0) / len(vals)
    except Exception as exc:
        log.info("overlap check failed %s.%s -> %s.%s: %s", at.name, ac.name, bt.name, bc.name, exc)
        conn.rollback()
        return None


# ------------------------------------------------------------------ entry point

# MAIN: profiles a whole database (all tables, columns and relationships).
def profile_source(source: Source, eng: Engine) -> SourceProfile:
    t0 = time.perf_counter()
    q = _Q(eng)
    insp = inspect(eng)
    is_duck = eng.dialect.name == "duckdb"
    default_schema = "main" if is_duck else insp.default_schema_name

    # (schema, raw table name, is_view) for every user table / view
    objects: list[tuple[str | None, str, bool]] = []
    if is_duck:
        with eng.connect() as conn:
            for s, t, kind in conn.execute(text(
                "SELECT table_schema, table_name, table_type FROM information_schema.tables "
                "WHERE table_catalog = current_database() ORDER BY 1, 2"
            )):
                objects.append((None if s == "main" else s, t, kind == "VIEW"))
    else:
        for schema in insp.get_schema_names():
            if schema in SYSTEM_SCHEMAS or schema.startswith(("pg_temp", "pg_toast")):
                continue
            sch = None if schema == default_schema else schema
            objects += [(sch, t, False) for t in insp.get_table_names(schema=schema)]
            objects += [(sch, v, True) for v in insp.get_view_names(schema=schema)]

    tables: list[TableProfile] = []
    declared: list[Relationship] = []
    schemas: dict[str, str | None] = {}
    raw_names: dict[str, str] = {}

    with eng.connect() as conn:
        for schema, name, is_view in objects:
            display = f"{schema}.{name}" if schema else name
            schemas[display], raw_names[display] = schema, name
            try:
                cols = insp.get_columns(name, schema=schema)
            except Exception as exc:
                log.warning("cannot reflect %s: %s", display, exc)
                continue
            pk: set[str] = set()
            if not is_view:
                try:
                    pk = set(insp.get_pk_constraint(name, schema=schema).get("constrained_columns") or [])
                except Exception:
                    pass
                try:
                    for fk in insp.get_foreign_keys(name, schema=schema):
                        ref_schema = fk.get("referred_schema")
                        ref = f"{ref_schema}.{fk['referred_table']}" \
                            if ref_schema and ref_schema != default_schema else fk["referred_table"]
                        for a, b in zip(fk["constrained_columns"], fk["referred_columns"]):
                            declared.append(Relationship(from_table=display, from_column=a, to_table=ref,
                                                         to_column=b, source="declared"))
                except Exception:
                    pass
            tables.append(_profile_table(conn, q, schema, name, display, cols, pk, is_view))

        inferred = _infer_relationships(conn, q, tables, schemas, raw_names, declared)

    return SourceProfile(
        source_id=source.id,
        source_name=source.name,
        engine=source.engine,
        dialect=source.dialect,
        tables=tables,
        relationships=declared + inferred,
        profiled_at=datetime.now(timezone.utc).isoformat(),
        seconds=round(time.perf_counter() - t0, 2),
    )


__all__ = ["profile_source", "_split_words"]
