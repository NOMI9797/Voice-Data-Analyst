"""Question -> SQL with the LLM, and SQL repair from an error message.

The LLM only sees the schema of the retrieved tables (not the whole database),
values from the question that matched real data, and the last few turns for
follow-up questions. It must answer in JSON so the result is machine-checkable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.llm.client import chat_json

DIALECT_TIPS = {
    "postgres": "PostgreSQL: month buckets with DATE_TRUNC('month', col)::date, year with EXTRACT(YEAR FROM col)::int, "
                "string concat with ||, ILIKE for case-insensitive match. Cast to numeric before ROUND(x, 2).",
    "mysql": "MySQL: month buckets with DATE_FORMAT(col, '%Y-%m'), year with YEAR(col), "
             "CONCAT() for strings, backticks only if a name needs quoting.",
    "sqlite": "SQLite: month buckets with strftime('%Y-%m', col), year with CAST(strftime('%Y', col) AS INTEGER), "
              "|| for concat, no ILIKE (use LOWER(col) LIKE ...). Dates are stored as text.",
    "duckdb": "DuckDB: month buckets with DATE_TRUNC('month', col)::date, year with YEAR(col), ILIKE for case-insensitive match.",
}

SYSTEM = """You translate business questions into ONE read-only SQL query.

Rules:
- Use only the tables and columns in the schema below. Never invent names.
- Write a single SELECT (CTEs allowed). Never modify data.
- Answer the question directly: aggregate (COUNT, SUM, AVG ...) instead of listing raw rows when the question asks "how many", "total", "average", "which ... most".
- Give result columns short snake_case aliases that a chart can use as labels (e.g. month, state, total_revenue, orders).
- Put the label/dimension column first and the measures after it.
- For trends over time, group by a time bucket and ORDER BY it ascending.
- For rankings ("top", "most", "best", "worst"), ORDER BY the measure and LIMIT to the number asked (default 10).
- Use values exactly as they appear in the data (see VALUE MATCHES and the column value lists); match text case-insensitively when unsure.
- Join along the JOINS listed in the schema.
- Round money and averages to 2 decimals.
- Do not select personal data (names, emails, phones, addresses) or secrets unless the question needs a count of them.
- {dialect_tip}

If the question cannot be answered with this schema, or is not about this data, set "sql" to null and explain why in "message".
If it is ambiguous in a way that changes the answer, still write the most reasonable query and state the assumption in "assumption".

Reply with JSON only:
{{"sql": "...", "assumption": "... or empty", "message": "... (only when sql is null)"}}"""


@dataclass
class Turn:
    question: str
    sql: str = ""
    source_id: str = ""


@dataclass
class Generation:
    sql: str | None
    assumption: str = ""
    message: str = ""
    raw: dict = field(default_factory=dict)


def _system(dialect: str | None) -> str:
    return SYSTEM.format(dialect_tip=DIALECT_TIPS.get(dialect or "", "Use standard SQL."))


def _context(schema_text: str, value_hits: list[dict], history: list[Turn]) -> str:
    parts = [schema_text]
    if value_hits:
        parts.append("VALUE MATCHES (words in the question that are real values in the data):\n"
                     + "\n".join(f"- {v['table']}.{v['match']}" for v in value_hits[:12]))
    if history:
        parts.append("EARLIER IN THIS CONVERSATION (use it to resolve follow-ups like 'and in 2018?' or 'what about Rio?'):\n"
                     + "\n".join(f"Q: {t.question}\nSQL: {t.sql}" for t in history[-3:]))
    return "\n\n".join(parts)


def _parse(out: dict) -> Generation:
    sql = out.get("sql")
    if isinstance(sql, str):
        sql = sql.strip().removeprefix("```sql").removeprefix("```").removesuffix("```").strip() or None
    else:
        sql = None
    return Generation(sql=sql, assumption=str(out.get("assumption") or "").strip(),
                      message=str(out.get("message") or "").strip(), raw=out)


def generate_sql(question: str, schema_text: str, dialect: str | None,
                 value_hits: list[dict] | None = None, history: list[Turn] | None = None) -> Generation:
    msgs = [
        {"role": "system", "content": _system(dialect)},
        {"role": "user", "content": _context(schema_text, value_hits or [], history or [])
         + f"\n\nQUESTION: {question}"},
    ]
    return _parse(chat_json(msgs, max_tokens=1200))


def repair_sql(question: str, schema_text: str, dialect: str | None, bad_sql: str, error: str,
               value_hits: list[dict] | None = None, history: list[Turn] | None = None) -> Generation:
    msgs = [
        {"role": "system", "content": _system(dialect)},
        {"role": "user", "content": _context(schema_text, value_hits or [], history or [])
         + f"\n\nQUESTION: {question}"},
        {"role": "assistant", "content": json.dumps({"sql": bad_sql})},
        {"role": "user", "content": f"That query failed:\n{error}\n\nFix it. Reply with the same JSON format."},
    ]
    return _parse(chat_json(msgs, max_tokens=1200))
