"""Does a query result that ran fine still look wrong?

A query can run without an error and still be wrong: a filter value spelled
differently from the data ('sao paulo' vs 'SP') returns no rows, an AVG over a
bad join returns NULL. These cheap checks flag results worth one repair attempt.
The repair prompt says to return the same SQL when the result is actually right
(an empty answer can be the truth), so a flag is a question, not a verdict.
"""

from __future__ import annotations

import re

AGGREGATE_QUESTION = re.compile(r"\b(how many|how much|total|average|avg|mean|sum|count|number of|percentage|"
                                r"percent|share|ratio|rate)\b", re.I)
COUNT_COLUMN = re.compile(r"(count|num|number|qty|quantity|orders|customers|items|tracks|products|sellers|"
                          r"invoices|units|total_)", re.I)
PERCENT_COLUMN = re.compile(r"(pct|percent|percentage|share)", re.I)


# True for numbers (not true/false).
def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# Spots results that ran but look wrong (no rows, all blank, negative counts, % above 100).
def suspicious(question: str, columns: list[str], rows: list[list], truncated: bool) -> str | None:
    """Returns a short description of what looks wrong, or None."""
    if not rows:
        return ("it returned no rows. If the question expects data, the filters probably don't match the "
                "data: compare text values with the listed column values (exact spelling and case, codes "
                "like 'SP' instead of names), keep dates inside the column ranges, and check the join keys")
    cols = list(zip(*rows)) if rows else []
    for name, values in zip(columns, cols):
        present = [v for v in values if v is not None]
        if not present and len(columns) <= 3:
            return (f"column {name} is NULL in every row, which usually means an average or sum over no "
                    f"matching rows (a filter or join that matches nothing)")
        nums = [v for v in present if _is_num(v)]
        if nums and COUNT_COLUMN.search(name) and min(nums) < 0:
            return f"column {name} looks like a count or quantity but has negative values ({min(nums)})"
        if nums and PERCENT_COLUMN.search(name) and max(nums) > 100.0001:
            return f"column {name} looks like a percentage but has values above 100 ({max(nums)})"
    if truncated and AGGREGATE_QUESTION.search(question):
        return (f"the question asks for a number or summary, but the query returned {len(rows)}+ raw rows "
                f"(it hit the row limit); aggregate instead of listing rows")
    return None
