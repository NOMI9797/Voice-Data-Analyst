"""Turns a query result into a short answer meant to be SPOKEN.

The LLM sees the question and the actual rows (at most 30), so the numbers it
says come from the data. If the LLM is unavailable, a plain template answer is
built from the chart spec instead, so the user always hears something.
"""

from __future__ import annotations

import json
import logging

from app.llm.client import LLMError, chat_json
from app.nl2sql.insights import facts

log = logging.getLogger(__name__)

MAX_ROWS = 30
ANSWER_TIMEOUT_S = 6  # past this, speak the template answer instead of waiting

SYSTEM = """You are a data analyst speaking to a business user. Answer their question in one or two short spoken sentences, using ONLY the query result given.

- Lead with the direct answer (the number, the winner, the trend), then at most one thing that matters (a peak, a share, a gap), like "Sales peaked in March, about 20% above average."
- Use the KEY FACTS for numbers: they are computed exactly over all rows. Do not do your own arithmetic.
- Never read the table out loud or list more than three items.
- Say numbers the way a person would: round sensibly ("about 1.3 million", "4.2 out of 5", "38 percent"); no more than 3 numbers in total.
- Do not assume a currency unless the schema notes or column names state it.
- No markdown, no lists, no SQL, no column names in snake_case.
- If the result is empty, say that nothing matched.
- If only part of the rows are shown, do not claim totals over rows you cannot see.
Reply as JSON: {"answer": "..."}"""


# Writes up to 30 result rows as text for the LLM.
def _rows_text(columns: list[str], rows: list[list], total: int, truncated: bool) -> str:
    shown = rows[:MAX_ROWS]
    lines = [" | ".join(columns)] + [" | ".join("" if v is None else str(v) for v in r) for r in shown]
    more = len(rows) - len(shown)
    tail = ""
    if more > 0 or truncated:
        tail = f"\n(showing {len(shown)} of {total}{'+' if truncated else ''} rows)"
    return "\n".join(lines) + tail


# Backup answer without the LLM: speaks the computed facts.
def template_answer(columns: list[str], rows: list[list], chart: dict) -> str:
    """No-LLM fallback: the first computed facts, as plain sentences."""
    if not rows:
        return "The query ran, but nothing matched."
    found = facts(columns, rows, chart)
    if found:
        return " ".join(found[:2])
    return f"I found {len(rows)} row{'s' if len(rows) != 1 else ''}. They are shown below."


# MAIN: asks Gemini for a 1-2 sentence spoken answer (6 s deadline, then the backup).
def spoken_answer(question: str, columns: list[str], rows: list[list], row_count: int,
                  truncated: bool, chart: dict, assumption: str = "") -> tuple[str, bool]:
    """Returns (answer, written_by_llm)."""
    prompt = f"QUESTION: {question}\n\nRESULT:\n{_rows_text(columns, rows, row_count, truncated)}"
    key = facts(columns, rows, chart)
    if key:
        prompt += "\n\nKEY FACTS (exact, over all rows):\n" + "\n".join(f"- {f}" for f in key)
    if assumption:
        prompt += f"\n\nASSUMPTION MADE WHEN WRITING THE QUERY: {assumption}"
    try:
        out = chat_json([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                        max_tokens=300, temperature=0.2,
                        timeout=ANSWER_TIMEOUT_S, fallback=False)
        answer = str(out.get("answer") or "").strip()
        if answer:
            return answer, True
    except (LLMError, json.JSONDecodeError) as exc:
        log.warning("spoken answer fell back to template: %s", exc)
    return template_answer(columns, rows, chart), False
