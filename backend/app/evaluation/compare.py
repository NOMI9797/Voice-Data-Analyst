"""Execution accuracy: do two query results contain the same answer?

Compares RESULTS, not SQL text (as the Spider / BIRD benchmarks do), so
different but equivalent queries count as correct:
  * column names are ignored; the predicted result may have extra columns
    (e.g. a label next to the number), but every gold column must map to a
    predicted column so that whole ROWS match, not just each column on its own
  * row order is ignored
  * numbers are compared after rounding to 2 decimals (1 == 1.0, 4.0849 ~ 4.08)
  * text is compared trimmed (case kept: 'SP' and 'sp' are different answers)

`lenient` additionally accepts a "which X has the most Y" answer where the gold
is one row and the prediction is a ranked list whose FIRST row is that row: the
spoken answer is still right. Reported separately from strict accuracy.
Pure Python, no dependencies.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import product

MAX_MAPPINGS = 5000


def norm(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return round(float(v), 2) + 0.0  # + 0.0 turns -0.0 into 0.0
    if isinstance(v, str):
        s = v.strip()
        try:  # numbers that arrive as text (DECIMAL as string)
            if s and s.lstrip("-").replace(".", "", 1).isdigit():
                return round(float(s), 2) + 0.0
        except ValueError:
            pass
        return s
    return str(v)


@dataclass
class Match:
    strict: bool
    lenient: bool
    reason: str


def _rows(rows: list[list]) -> list[tuple]:
    return [tuple(norm(v) for v in r) for r in rows]


def _mappings(gold: list[tuple], pred: list[tuple], n_gold_cols: int, n_pred_cols: int):
    """Candidate gold-column -> pred-column mappings: a pred column qualifies when
    its multiset of values equals the gold column's."""
    pcols = [Counter(r[j] for r in pred) for j in range(n_pred_cols)]
    options = []
    for i in range(n_gold_cols):
        g = Counter(r[i] for r in gold)
        cand = [j for j in range(n_pred_cols) if pcols[j] == g]
        if not cand:
            return None, i
        options.append(cand)
    return options, None


def same_rows(gold: list[tuple], pred: list[tuple], n_gold_cols: int, n_pred_cols: int) -> tuple[bool, str]:
    if len(gold) != len(pred):
        return False, f"{len(pred)} rows instead of {len(gold)}"
    if not gold:
        return True, "both empty"
    options, missing = _mappings(gold, pred, n_gold_cols, n_pred_cols)
    if options is None:
        return False, f"no column with the values of gold column {missing + 1}"
    want = Counter(gold)
    for k, mapping in enumerate(product(*options)):
        if k >= MAX_MAPPINGS:
            break
        if len(set(mapping)) != len(mapping):
            continue
        if Counter(tuple(r[j] for j in mapping) for r in pred) == want:
            return True, "same rows"
    return False, "same column values but rows are paired differently"


def compare(gold_cols: list[str], gold_rows: list[list], pred_cols: list[str], pred_rows: list[list]) -> Match:
    g, p = _rows(gold_rows), _rows(pred_rows)
    ok, why = same_rows(g, p, len(gold_cols), len(pred_cols))
    if ok:
        return Match(True, True, why)
    if len(g) == 1 and len(p) > 1:
        first_ok, _ = same_rows(g, p[:1], len(gold_cols), len(pred_cols))
        if first_ok:
            return Match(False, True, f"ranked list of {len(p)} rows; first row is the answer")
    return Match(False, False, why)
