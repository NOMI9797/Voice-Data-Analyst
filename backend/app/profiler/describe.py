"""LLM-written plain-English descriptions for tables and columns.

Real databases have cryptic names ("product_name_lenght", "qty_x", "flg2").
A one-time LLM pass turns each table + its columns + sample values into short
descriptions. These are used by the retriever and shown to the SQL generator.
Optional: if no LLM key is set, profiling still works without descriptions.
"""

from __future__ import annotations

import logging
import time

from app.llm.client import LLMError, chat_json
from app.profiler.models import SourceProfile, TableProfile

log = logging.getLogger(__name__)

BATCH = 6  # tables per LLM call
RETRY_WAITS = (5, 15, 45)  # background job: worth waiting out a Gemini overload

SYSTEM = (
    "You document databases for business users. For each table write a one-sentence description "
    "of what one row represents and what the table is for. For each column write a short "
    "description (max 12 words) that explains its meaning in plain English, including units, "
    "codes, or language if the sample values reveal them (e.g. 'category name in Portuguese', "
    "'length of product name in characters (misspelled lenght)'). Do not invent facts that are "
    "not supported by the names or samples. In source_description, say what kind of business the "
    "data is from and, if the names or values reveal it, which country or region and language "
    "(e.g. 'Brazilian e-commerce marketplace; category names in Portuguese'). Reply as JSON: "
    '{"source_description": "...", "tables": [{"name": "...", "description": "...", '
    '"columns": {"<column>": "<description>"}}]}'
)


# Short text about one table (columns, samples) to send to the LLM.
def _table_brief(t: TableProfile) -> str:
    lines = [f"TABLE {t.name} ({t.row_count} rows){' [view]' if t.is_view else ''}"]
    for c in t.columns:
        extra = ""
        if c.samples:
            extra = " e.g. " + ", ".join(repr(s[:40]) for s in c.samples[:4])
        if c.min is not None:
            extra += f" range {c.min} .. {c.max}"
        lines.append(f"  {c.name} {c.type}{' PK' if c.primary_key else ''}{extra}")
    return "\n".join(lines)


# When re-profiling, keeps the descriptions already written.
def carry_over_descriptions(old: SourceProfile, new: SourceProfile) -> None:
    """Re-profiling keeps descriptions already written for tables/columns that still
    exist, so a flaky LLM can't erase earlier work and only new tables cost calls."""
    new.description = new.description or old.description
    old_tables = {t.name: t for t in old.tables}
    for t in new.tables:
        o = old_tables.get(t.name)
        if o is None:
            continue
        t.description = t.description or o.description
        old_cols = {c.name: c.description for c in o.columns}
        for c in t.columns:
            c.description = c.description or old_cols.get(c.name, "")


# Asks Gemini for a plain-English line per table and column (6 tables per call, with retries).
def describe_profile(profile: SourceProfile) -> SourceProfile:
    """Describes tables that have no description yet (all of them on a first run)."""
    source_desc = profile.description
    failed = 0
    tables = profile.tables
    todo = [t for t in tables if not t.description]
    if not todo and not source_desc:
        todo = tables[:BATCH]  # one call just for the source description
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        prompt = (f"Database: {profile.source_name} ({profile.engine}). "
                  f"All tables: {', '.join(t.name for t in tables)}\n\n"
                  + "\n\n".join(_table_brief(t) for t in batch))
        out = None
        for wait in (*RETRY_WAITS, None):
            try:
                out = chat_json([{"role": "system", "content": SYSTEM},
                                 {"role": "user", "content": prompt}], max_tokens=3000)
                break
            except LLMError as exc:
                if wait is None:
                    log.warning("describe: batch %d of %s skipped: %s", i // BATCH + 1, profile.source_name,
                                str(exc)[:200])
                else:
                    log.info("describe: batch %d of %s failed, retrying in %ds", i // BATCH + 1,
                             profile.source_name, wait)
                    time.sleep(wait)
        if out is None:
            failed += 1
            continue
        source_desc = source_desc or out.get("source_description", "")
        by_name = {t.get("name"): t for t in out.get("tables", []) if isinstance(t, dict)}
        for t in batch:
            d = by_name.get(t.name)
            if not d:
                continue
            t.description = str(d.get("description", ""))[:400]
            cols = d.get("columns") or {}
            for c in t.columns:
                if c.name in cols:
                    c.description = str(cols[c.name])[:200]
    profile.description = (source_desc or "")[:400]
    profile.described = failed == 0  # partly described sources are retried on the next profile run
    return profile
