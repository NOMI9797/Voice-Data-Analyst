"""LLM-written plain-English descriptions for tables and columns.

Real databases have cryptic names ("product_name_lenght", "qty_x", "flg2").
A one-time LLM pass turns each table + its columns + sample values into short
descriptions. These are used by the retriever and shown to the SQL generator.
Optional: if no LLM key is set, profiling still works without descriptions.
"""

from __future__ import annotations

import logging

from app.llm.client import LLMError, chat_json
from app.profiler.models import SourceProfile, TableProfile

log = logging.getLogger(__name__)

BATCH = 6  # tables per LLM call

SYSTEM = (
    "You document databases for business users. For each table write a one-sentence description "
    "of what one row represents and what the table is for. For each column write a short "
    "description (max 12 words) that explains its meaning in plain English, including units, "
    "codes, or language if the sample values reveal them (e.g. 'category name in Portuguese', "
    "'length of product name in characters (misspelled lenght)'). Do not invent facts that are "
    "not supported by the names or samples. Reply as JSON: "
    '{"source_description": "...", "tables": [{"name": "...", "description": "...", '
    '"columns": {"<column>": "<description>"}}]}'
)


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


def describe_profile(profile: SourceProfile) -> SourceProfile:
    source_desc = ""
    tables = profile.tables
    for i in range(0, len(tables), BATCH):
        batch = tables[i:i + BATCH]
        prompt = (f"Database: {profile.source_name} ({profile.engine}). "
                  f"All tables: {', '.join(t.name for t in tables)}\n\n"
                  + "\n\n".join(_table_brief(t) for t in batch))
        try:
            out = chat_json([{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": prompt}], max_tokens=3000)
        except LLMError as exc:
            log.warning("describe failed for %s: %s", profile.source_name, exc)
            return profile
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
    profile.description = source_desc[:400]
    profile.described = True
    return profile
