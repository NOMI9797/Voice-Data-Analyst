"""Picks a chart for a query result with fast, deterministic rules (no LLM).

Each column is classified as one of:
  temporal     dates/timestamps, 'YYYY-MM' strings, or a year/month-named column
  measure      numbers worth adding up or comparing (not ids, zip codes, years)
  identifier   numeric codes: id, *_id, zip, code ... (treated like a category)
  category     everything else (text)

Then, in order:
  no rows                                   -> empty
  1 row, 1-4 measures                       -> kpi (big number cards)
  time + measures (3+ points)               -> line  (+ series if one small category)
  category + measures                       -> bar   (+ series for a 2nd small category)
  2+ measures, no dimension, 5+ rows        -> scatter
  otherwise                                 -> table
The frontend can switch to any type listed in `alternatives`.
"""

from __future__ import annotations

import re
from datetime import date, datetime

TEMPORAL_NAME = re.compile(r"(^|_)(date|day|week|month|quarter|year|time|timestamp|period|yr|mon)(_|$)", re.I)
YEAR_NAME = re.compile(r"(^|_)(year|yr)(_|$)", re.I)
ID_NAME = re.compile(r"(^id$|_id$|^id_|(^|_)(zip|zipcode|postal|code|prefix|phone|sku|number)(_|$))", re.I)
DATE_STR = re.compile(r"^\d{4}-\d{2}(-\d{2})?([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?([+-]\d{2}:?\d{2}|Z)?$")

MAX_BAR_CATEGORIES = 30
MAX_SERIES = 8
SAMPLE = 500


# True for numbers (not true/false).
def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# Decides if a column is a date, a number, an ID or a category.
def classify_column(name: str, values: list) -> str:
    vals = [v for v in values[:SAMPLE] if v is not None and v != ""]
    if not vals:
        return "category"
    if all(_is_num(v) for v in vals):
        if YEAR_NAME.search(name) and all(isinstance(v, int) or float(v).is_integer() for v in vals) \
                and all(1800 <= v <= 2200 for v in vals):
            return "temporal"
        if ID_NAME.search(name):
            return "identifier"
        return "measure"
    if all(isinstance(v, (date, datetime)) for v in vals):
        return "temporal"
    if all(isinstance(v, str) for v in vals):
        if all(DATE_STR.match(v.strip()) for v in vals):
            return "temporal"
        if TEMPORAL_NAME.search(name) and all(re.match(r"^\d{4}[-/ ]?(Q[1-4]|W\d{1,2}|\d{1,2})$", v.strip(), re.I)
                                              for v in vals):
            return "temporal"  # 2017-Q3, 2017W12, 2017/03
    return "category"


# How many different values a column has.
def _distinct(values: list) -> int:
    return len({v for v in values if v is not None})


# Avoids mixing very different numbers on one chart axis.
def _measures_for_plot(measures: list[str], cols: dict[str, list]) -> list[str]:
    """First measure, plus others on a similar scale (a count next to a revenue
    sum would flatten into the axis)."""
    if not measures:
        return []

    def peak(c):
        nums = [abs(v) for v in cols[c] if _is_num(v)]
        return max(nums) if nums else 0

    base = peak(measures[0]) or 1
    keep = [measures[0]]
    for m in measures[1:4]:
        p = peak(m) or 1
        if max(p, base) / min(p, base) <= 20:
            keep.append(m)
    return keep


# Turns a column name into readable words.
def _h(name: str) -> str:
    """order_purchase_timestamp / CategoryName -> 'order purchase timestamp' / 'category name'."""
    return re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").strip().lower()


# MAIN: picks the chart: number card, line, bar, grouped bar, scatter or table.
def choose_chart(columns: list[str], rows: list[list]) -> dict:
    n = len(rows)
    cols = {c: [r[i] for r in rows] for i, c in enumerate(columns)}
    kinds = {c: classify_column(c, cols[c]) for c in columns}
    measures = [c for c in columns if kinds[c] == "measure"]
    temporals = [c for c in columns if kinds[c] == "temporal"]
    cats = [c for c in columns if kinds[c] in ("category", "identifier")]

    def spec(type_, reason, x=None, y=None, series=None, alternatives=(), **extra):
        return {"type": type_, "x": x, "y": y or [], "series": series, "reason": reason,
                "alternatives": [a for a in dict.fromkeys([*alternatives, "table"]) if a != type_],
                "column_types": kinds, **extra}

    if n == 0:
        return spec("empty", "The query returned no rows.")

    if n == 1 and 1 <= len(measures) <= 4:
        # a "which X has the most Y" answer: show the winning label beside its numbers
        label = next((c for c in columns if kinds[c] != "measure"), None)
        return spec("kpi", "A single result is shown as headline values.", x=label, y=measures)

    if temporals and measures:
        x = temporals[0]
        series = next((c for c in cats if 1 < _distinct(cols[c]) <= MAX_SERIES), None)
        if series:
            points = _distinct(cols[x])
            if points >= 3:
                return spec("line", f"Values over {_h(x)}, one line per {_h(series)}.", x=x, y=measures[:1],
                            series=series, alternatives=["bar"])
            return spec("bar", f"Few time points, so bars per {_h(x)} grouped by {_h(series)}.", x=x,
                        y=measures[:1], series=series, alternatives=["line"])
        if not cats or _distinct(cols[x]) == n:  # one row per time point
            y = _measures_for_plot(measures, cols)
            if n >= 3:
                return spec("line", f"Change over {_h(x)}.", x=x, y=y, alternatives=["bar"])
            return spec("bar", f"Only {n} time points, so bars instead of a line.", x=x, y=y,
                        alternatives=["line"])

    dims = cats + temporals
    if dims and measures:
        x = dims[0]
        y = _measures_for_plot(measures, cols)
        second = next((c for c in dims[1:] if 1 < _distinct(cols[c]) <= MAX_SERIES), None)
        if second and len(measures) == 1 and _distinct(cols[x]) <= MAX_BAR_CATEGORIES:
            return spec("bar", f"{_h(measures[0]).capitalize()} by {_h(x)}, split by {_h(second)}.", x=x, y=measures[:1],
                        series=second, orientation=_orientation(cols[x], _distinct(cols[x])))
        if _distinct(cols[x]) == n or len(dims) == 1:
            shown = min(n, MAX_BAR_CATEGORIES)
            note = (f"Showing the first {shown} of {n} rows; the table has all of them."
                    if n > MAX_BAR_CATEGORIES else None)
            return spec("bar", f"Compares {' and '.join(map(_h, y))} across {_h(x)}.", x=x, y=y,
                        orientation=_orientation(cols[x], shown), limit=shown, note=note,
                        alternatives=["line"] if kinds[x] == "temporal" else [])

    if len(measures) >= 2 and not dims and n >= 5:
        return spec("scatter", f"Relationship between {_h(measures[0])} and {_h(measures[1])}.",
                    x=measures[0], y=[measures[1]])

    return spec("table", "No simple chart fits this result, so it is shown as a table.")


# Horizontal bars for many or long labels, vertical otherwise.
def _orientation(labels: list, count: int) -> str:
    longest = max((len(str(v)) for v in labels[:MAX_BAR_CATEGORIES] if v is not None), default=0)
    return "horizontal" if count > 8 or longest > 12 else "vertical"
