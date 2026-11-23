"""Turns a query result into a short answer meant to be SPOKEN.

The LLM sees the question and the actual rows (at most 30), so the numbers it
says come from the data. If the LLM is unavailable, a plain template answer is
built from the chart spec instead, so the user always hears something.
"""

from __future__ import annotations

import json
import logging
import re

from app.llm.client import LLMError, chat_json

log = logging.getLogger(__name__)

MAX_ROWS = 30
ANSWER_TIMEOUT_S = 6  # past this, speak the template answer instead of waiting

SYSTEM = """You are a data analyst speaking to a business user. Answer their question in one or two short spoken sentences, using ONLY the query result given.

- Lead with the direct answer (the number, the winner, the trend).
- Say numbers the way a person would: round sensibly ("about 1.3 million", "4.2 out of 5", "38 percent"); no more than 3 numbers in total.
- Do not assume a currency unless the schema notes or column names state it.
- No markdown, no lists, no SQL, no column names in snake_case.
- If the result is empty, say that nothing matched.
- If only part of the rows are shown, do not claim totals over rows you cannot see.
Reply as JSON: {"answer": "..."}"""


def _rows_text(columns: list[str], rows: list[list], total: int, truncated: bool) -> str:
    shown = rows[:MAX_ROWS]
    lines = [" | ".join(columns)] + [" | ".join("" if v is None else str(v) for v in r) for r in shown]
    more = len(rows) - len(shown)
    tail = ""
    if more > 0 or truncated:
        tail = f"\n(showing {len(shown)} of {total}{'+' if truncated else ''} rows)"
    return "\n".join(lines) + tail


def _human(name: str) -> str:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").strip().lower()
    return re.sub(r" name$", "", words)  # "category name" -> "category"


def _num(v) -> str:
    if isinstance(v, float):
        if abs(v) >= 1_000_000:
            return f"about {v / 1_000_000:.1f} million"
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def template_answer(columns: list[str], rows: list[list], chart: dict) -> str:
    """No-LLM fallback: a plain sentence from the chart spec."""
    if not rows:
        return "The query ran, but nothing matched."
    idx = {c: i for i, c in enumerate(columns)}
    t = chart.get("type")
    y = (chart.get("y") or [None])[0]
    if t == "kpi":
        head = f"{rows[0][idx[chart['x']]]}. " if chart.get("x") else ""
        return head + ". ".join(f"{_human(c).capitalize()}: {_num(rows[0][idx[c]])}" for c in chart["y"]) + "."
    if t == "bar" and chart.get("x") and y and not chart.get("series"):
        top = rows[0]
        return (f"The top {_human(chart['x'])} is {top[idx[chart['x']]]} with {_num(top[idx[y]])} {_human(y)}. "
                f"There are {len(rows)} results in total.")
    if t == "line" and chart.get("x") and y and not chart.get("series"):
        first, last = rows[0], rows[-1]
        return (f"{_human(y).capitalize()} went from {_num(first[idx[y]])} in {first[idx[chart['x']]]} "
                f"to {_num(last[idx[y]])} in {last[idx[chart['x']]]}.")
    return f"I found {len(rows)} row{'s' if len(rows) != 1 else ''}. They are shown below."


def spoken_answer(question: str, columns: list[str], rows: list[list], row_count: int,
                  truncated: bool, chart: dict, assumption: str = "") -> tuple[str, bool]:
    """Returns (answer, written_by_llm)."""
    prompt = f"QUESTION: {question}\n\nRESULT:\n{_rows_text(columns, rows, row_count, truncated)}"
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
