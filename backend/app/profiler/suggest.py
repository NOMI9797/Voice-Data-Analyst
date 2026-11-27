"""Starter questions for an empty conversation, built from what each source really holds.

A blank input box gives a new user nothing to go on, and generic examples may ask
for data the connected database doesn't have. These come from the profile of each
active source, so they always fit (including databases never seen before):
  * a trend:      "How many orders were there each month?"   (table with a date)
  * a breakdown:  "How many orders are there per order status?" (small category column)
  * a measure:    "What is the total payment value per payment type?" (number + category)
"""

from __future__ import annotations

import re

from app.profiler.models import ColumnProfile, SourceProfile, TableProfile

MAX_PER_SOURCE = 3


def _words(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name.rsplit(".", 1)[-1]).replace("_", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def _plural(name: str) -> str:
    w = _words(name)
    if w.endswith(("s", "data", "info")):
        return w
    if w.endswith("y") and not w.endswith(("ay", "ey", "oy", "uy")):
        return w[:-1] + "ies"
    return w + "s"


FLAG_VALUES = {"0", "1", "true", "false", "yes", "no", "y", "n", "t", "f"}
SUM_MEASURE = re.compile(r"(value|amount|total|revenue|sales|freight|cost|payment|paid|spent|quantity|qty|units)", re.I)
AVG_MEASURE = re.compile(r"(price|score|rating|weight|length|height|width|size|duration|milliseconds|age)", re.I)


def _is_category(c: ColumnProfile) -> bool:
    name = c.name.lower()
    is_flag = c.values and {v.strip().lower() for v in c.values} <= FLAG_VALUES
    return (c.kind == "text" and c.categorical and 2 <= (c.distinct_count or 0) <= 30 and not is_flag
            and not re.search(r"(^id$|_id$|id$|name$|phone|email|address|zip|code|postal)", name))


def _is_measure(c: ColumnProfile) -> bool:
    return (c.kind == "number" and not c.primary_key
            and not re.search(r"(^id$|_id$|id$|zip|code|prefix|lat|lng|lon|year|seq|sequential|number$)",
                              c.name.lower()))


def _table_score(t: TableProfile) -> tuple:
    has_date = any(c.kind == "date" for c in t.columns)
    has_cat = any(_is_category(c) for c in t.columns)
    return (has_date and has_cat, has_cat, has_date, t.row_count or 0)


def _measure_phrase(measure: ColumnProfile) -> str:
    """'payment_value' -> 'total payment value', 'UnitPrice' -> 'average unit price'."""
    m = _words(measure.name)
    if AVG_MEASURE.search(measure.name) and not SUM_MEASURE.search(measure.name):
        return f"average {m}"
    return "total amount" if m == "total" else (m if m.startswith("total") else f"total {m}")


def suggestions_for(profile: SourceProfile) -> list[str]:
    # join columns (ShipVia, ReportsTo, customer_id ...) are links, not amounts
    keys = {(r.from_table, r.from_column) for r in profile.relationships} | \
           {(r.to_table, r.to_column) for r in profile.relationships}
    tables = sorted((t for t in profile.tables if not t.is_view and (t.row_count or 0) > 0),
                    key=_table_score, reverse=True)
    out: list[str] = []
    for n, t in enumerate(tables):
        cats = sorted((c for c in t.columns if _is_category(c)),
                      key=lambda c: (c.null_fraction or 0, c.distinct_count or 0))
        cat = cats[0] if cats else None
        date = next((c for c in t.columns if c.kind == "date"), None)
        measures = [c for c in t.columns if _is_measure(c) and (t.name, c.name) not in keys
                    and (SUM_MEASURE.search(c.name) or AVG_MEASURE.search(c.name))]
        measures.sort(key=lambda c: 0 if SUM_MEASURE.search(c.name) and "install" not in c.name.lower() else 1)
        measure = measures[0] if measures else None
        things = _plural(t.name)
        if n == 0:  # the main table: a trend and a breakdown
            if date:
                out.append(f"How many {things} were there each month?")
            if cat:
                out.append(f"How many {things} are there per {_words(cat.name)}?")
        elif cat and measure:  # other tables only when they have a real amount to add up
            out.append(f"What is the {_measure_phrase(measure)} per {_words(cat.name)}?")
        if len(out) >= MAX_PER_SOURCE:
            break
    return list(dict.fromkeys(out))[:MAX_PER_SOURCE]
